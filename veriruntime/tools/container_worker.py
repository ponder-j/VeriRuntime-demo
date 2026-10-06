"""Worker entrypoint: the existing native adapter runs inside its own image."""
import json
from pathlib import Path
import sys
from veriruntime.execution import ExecutionSpec, LocalExecutionBackend

from veriruntime.model import (Budget, ProgramFile, ProgramSnapshot, Requirements,
    Semantics, SemanticHints, VerificationProperty, VerificationTask, to_data)
from veriruntime.tools.registry import default_registry


def decode_task(data):
    program = data['program']
    return VerificationTask(data['id'], data['language'], data['entry'],
        ProgramSnapshot(tuple(ProgramFile(**f) for f in program['files']), tuple(program['sources'])),
        VerificationProperty(**data['property']), Semantics(**data['semantics']),
        Requirements(**data['requirements']), Budget(**data['budget']), data['version'],
        SemanticHints(**data.get('hints', {})))


def main():
    action, tool = sys.argv[1:3]
    if tool == 'rocq':
        from .rocq import RocqAdapter
        adapter = RocqAdapter('/opt/runtime')
    else:
        adapter = default_registry('/opt/runtime', backend='native', extra_tools=(tool,)).get(tool)
    if action == 'probe':
        print(json.dumps(to_data(adapter.detect())))
        return 0
    if action != 'run':
        raise ValueError('Unsupported worker action')
    workspace = Path('/work')
    manifest = json.loads((workspace / 'container-request.json').read_text())
    if tool == 'rocq':
        from veriruntime.proofs.model import decode_proof
        task = decode_proof(manifest['task'])
    else:
        task = decode_task(manifest['task'])
    # Image ID fixes the binary, system headers, Python and all native dependencies.
    profile = adapter.detect()
    if not profile.available:
        raise RuntimeError(profile.diagnostic)
    command = adapter.build_command(task, workspace)
    for path in (workspace / 'input').rglob('*'):
        path.chmod(0o555 if path.is_dir() else 0o444)
    (workspace / 'input').chmod(0o555)
    env = adapter.attempt_environment(workspace)
    (workspace / 'native-command.json').write_text(json.dumps({
        'argv': command, 'profile': to_data(profile), 'environment': env}, indent=2))
    outcome = LocalExecutionBackend().run_sync(ExecutionSpec(tuple(command), str(workspace), env,
        metadata={'tool': tool, 'transport': 'isolated Linux worker'}),
        capture_output=False, inherit_group=True)
    (workspace / 'native-outcome.json').write_text(json.dumps(to_data(outcome), indent=2))
    return outcome.exit_code


if __name__ == '__main__':
    raise SystemExit(main())
