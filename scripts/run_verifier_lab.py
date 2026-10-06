"""Real isolated experiments for new families and separate proof-artifact checking."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import uuid

from veriruntime.dsl import load_task
from veriruntime.model import Budget, Requirements, to_data
from veriruntime.service import VerificationService
from veriruntime.tools import default_registry, ToolRegistry
from veriruntime.tools.docker_api import DockerEngine, worker_config
from veriruntime.proofs.dsl import load_proof
from veriruntime.proofs.service import check_proof

ROOT = Path(__file__).resolve().parents[1]


async def main():
    root = Path('/data/verifier-lab') / uuid.uuid4().hex
    root.mkdir(parents=True)
    registry = default_registry(ROOT, extra_tools=('ultimate', 'framac'))
    profiles = registry.discover()
    assert all(registry.get_profile(name).available for name in ('ultimate', 'framac'))
    cases = json.loads((ROOT / 'examples/benchmark/manifest.json').read_text())
    records, rows = [], []
    for tool in ('ultimate', 'framac'):
        service = VerificationService(root / tool, registry=ToolRegistry((registry.get(tool),)), cache_enabled=False)
        for case in cases:
            task = load_task(ROOT / 'examples/benchmark' / case['task'])
            # This caller requests one family's answer for the experiment matrix.
            # The runtime receives that fixed requirement and never lowers it.
            task = replace(task, budget=Budget(60, 2048, 1), requirements=Requirements(1))
            execution = await service.verify_goal(task)
            expected = case['expected'] if tool == 'ultimate' or case['expected'] == 'SAFE' else 'UNKNOWN'
            assert execution.report.result.verdict.value == expected, to_data(execution)
            records.append(to_data(execution))
            row = {'tool': tool, 'task': task.id, 'verdict': execution.report.result.verdict.value,
                   'wall_time_sec': execution.report.result.wall_time_sec, 'expected': expected}
            rows.append(row)
            print(json.dumps(row), flush=True)
    # No forced confirmation policy changes: same fixed C goal, separate families.
    for tool in ('cbmc', 'esbmc', 'ultimate', 'framac'):
        service = VerificationService(root / ('long-' + tool), registry=ToolRegistry((registry.get(tool),)), cache_enabled=False)
        task = load_task(ROOT / 'examples/experiments/long_loop.json')
        execution = await service.verify_goal(task)
        actual = execution.report.result.verdict.value
        assert actual == ('UNKNOWN' if tool in ('cbmc', 'esbmc') else 'SAFE'), to_data(execution)
        records.append(to_data(execution))
        row = {'tool': tool, 'task': 'long-loop-100', 'verdict': actual, 'wall_time_sec': execution.report.result.wall_time_sec}
        rows.append(row)
        print(json.dumps(row), flush=True)
    proofs = []
    for case in ('Correct', 'Incorrect', 'Admitted'):
        source = root / ('proof-' + case)
        source.mkdir()
        doc = json.loads((ROOT / 'examples/proofs/check.json').read_text())
        doc['goal']['proof'].update(files=[case + '.v'], symbol='VRGoal.' + case + '.discharge')
        for name in ('Expected.v', case + '.v'):
            (source / name).write_bytes((ROOT / 'examples/proofs' / name).read_bytes())
        (source / 'check.json').write_text(json.dumps(doc))
        result = await check_proof(load_proof(source / 'check.json'), root / 'proof-executions')
        assert result['status'] == ('VERIFIED' if case == 'Correct' else 'REJECTED'), result
        assert result['code_verdict'] is None
        proofs.append({'case': case, 'result': result})
        print('Rocq', case, result['status'], result['reason'], flush=True)
    engine = DockerEngine()
    wp = []
    for solver in ('z3', 'cvc5'):
        workspace = root / ('wp-' + solver)
        workspace.mkdir(mode=0o777)
        workspace.chmod(0o777)
        (workspace / 'square.c').write_bytes((ROOT / 'examples/experiments/square.c').read_bytes())
        mount = {'Type': 'volume', 'Source': 'veriruntime-data', 'Target': '/work',
            'VolumeOptions': {'NoCopy': True, 'Subpath': workspace.relative_to('/data').as_posix()}}
        name = 'vrun-wp-' + uuid.uuid4().hex
        config = worker_config(registry.get_profile('framac').path, [], 1024, mount)
        config['Entrypoint'] = ['/opt/framac/bin/frama-c']
        config['Cmd'] = ['-wp', '-wp-rte', '-wp-fct', 'square', '-wp-prover', solver,
            '-wp-timeout', '5', '-wp-cache', 'none', '-wp-report-json', '/work/wp.json',
            '-wp-out', '/work/wp', '/work/square.c']
        try:
            engine.create(name, config)
            engine.request('POST', f'/containers/{name}/start')
            state = engine.request('POST', f'/containers/{name}/wait', timeout=60)
            stdout, stderr = engine.logs(name)
            (workspace / 'stdout.log').write_bytes(stdout)
            (workspace / 'stderr.log').write_bytes(stderr)
            assert state['StatusCode'] == 0, stderr.decode()
            goals = json.loads((workspace / 'wp.json').read_text())
            assert len(goals) == 4 and all(goal['passed'] for goal in goals), goals
            assert any(p['success'] and p['prover'].lower().startswith(solver) for g in goals for p in g['provers']), goals
            # Reports carry WP's trust, not a Rocq certificate or a second C family.
            wp.append({'solver': solver, 'goals': goals, 'stdout': stdout.decode(), 'stderr': stderr.decode(),
                       'image': config['Image'], 'evidence_kind': 'solver_report', 'kernel_checked': False})
            print('Frama-C WP', solver, '4/4 report goals, including signed-overflow guards', flush=True)
        finally:
            engine.remove(name)
    remaining = engine.request('GET', '/containers/json?all=true&filters=%7B%22label%22%3A%5B%22org.veriruntime.managed%3Dattempt%22%5D%7D')
    assert not remaining, remaining
    summary = {'profiles': to_data(profiles), 'rows': rows, 'executions': records,
               'rocq': proofs, 'wp': wp, 'remaining_workers': 0,
               'scope': 'C-family experiments and standalone supplied-statement proof checks; no completed Frama-C/Rocq C proof bridge'}
    (root / 'lab.json').write_text(json.dumps(summary, indent=2))
    latest = Path('/data/verifier-lab/latest.json')
    latest.write_text(json.dumps(summary, indent=2))
    print('Real verifier lab passed:', latest, flush=True)


if __name__ == '__main__':
    asyncio.run(main())
