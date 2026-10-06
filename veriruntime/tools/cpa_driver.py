"""Compilation layer inside the same supervised process group as CPAchecker."""
import json
from pathlib import Path
import os
import sys
from veriruntime.execution import ExecutionSpec, LocalExecutionBackend


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text())
    backend = LocalExecutionBackend()
    def run(command, **outputs):
        return backend.run_sync(ExecutionSpec(tuple(command), str(Path.cwd()), dict(os.environ)),
            capture_output=False, inherit_group=True, **outputs)
    for command, destination in zip(manifest['preprocessor_commands'], manifest['compiled_sources']):
        with open(destination, 'wb') as output:
            result = run(command, stdout=output)
        if result.exit_code:
            return result.exit_code
    return run(manifest['verifier_argv']).exit_code


if __name__ == '__main__':
    raise SystemExit(main())
