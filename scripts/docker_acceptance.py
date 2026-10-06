"""Fresh-store real Linux acceptance; no mock verdicts, no dependence on old evidence."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import uuid

from veriruntime.dsl import load_task, load_workflow
from veriruntime.model import Budget, Requirements, VerificationProperty, Verdict, to_data
from veriruntime.service import VerificationService
from veriruntime.tools import default_registry
from veriruntime.tools.docker_api import DockerEngine, worker_config

ROOT = Path(__file__).resolve().parents[1]


async def main(with_cpa, export_dir=None):
    output = Path('/data/acceptance/latest')
    output.mkdir(parents=True, exist_ok=True)
    data = output / uuid.uuid4().hex
    registry = default_registry(ROOT)
    profiles = registry.discover()
    minimum = 3 if with_cpa else 2
    assert sum(p.available for p in profiles) >= minimum, to_data(profiles)
    service = VerificationService(data, registry=registry)
    records = {}
    for case, verdict in (('safe', Verdict.SAFE), ('unsafe', Verdict.UNSAFE)):
        task = replace(load_task(ROOT / f'examples/tasks/{case}_assert.json'), requirements=Requirements(minimum))
        first = await service.verify_goal(task)
        assert first.report.result.verdict == verdict and first.report.result.requirement_satisfied, to_data(first)
        assert not first.report.result.cache_hit and len(first.report.attempts) >= minimum
        repeat = await service.verify_goal(task)
        assert repeat.report.result.cache_hit and not repeat.report.attempts
        assert repeat.report.result.verdict == verdict
        records[case] = {'first': to_data(first), 'repeat': to_data(repeat)}
        print(f'{case}: {verdict.value}, {first.report.result.confirmations} families; repeat: CACHE HIT / 0 executions', flush=True)
        for attempt in first.report.attempts:
            workspace = Path(attempt.workspace)
            state = json.loads((workspace / 'container-state.json').read_text())
            config = state['host_config']
            assert config['ReadonlyRootfs'] and config['NetworkMode'] == 'none'
            assert config['Memory'] == config['MemorySwap'] and config['Memory'] > 0
            assert config['CapDrop'] == ['ALL'] and 'no-new-privileges' in config['SecurityOpt']
            assert len(state['mounts']) == 1 and state['mounts'][0]['Destination'] == '/work'
            assert attempt.config_id == registry.get_profile(attempt.tool).config_id
            assert service.store.show(first.report.result.execution_id)['artifact_records']
    workflow = await service.verify(load_workflow(ROOT / 'examples/tasks/assertion_workflow.json'))
    assert [g.report.result.verdict for g in workflow.goals] == [Verdict.SAFE, Verdict.UNSAFE]
    records['workflow'] = to_data(workflow)
    task = load_task(ROOT / 'examples/tasks/safe_assert.json')
    unsupported = await service.verify_goal(replace(task, property=VerificationProperty('memory_safety')))
    assert unsupported.report.result.verdict == Verdict.UNKNOWN and not unsupported.report.attempts
    records['unsupported'] = to_data(unsupported)
    timeout = await VerificationService(data / 'timeout', registry=registry, cache_enabled=False).verify_goal(
        replace(task, budget=Budget(0.15, 2048, 2)))
    assert timeout.report.result.verdict == Verdict.UNKNOWN and not timeout.report.result.cache_hit
    records['timeout'] = to_data(timeout)
    # Test the actual mount boundary from both directions. A peer sentinel in the
    # same volume is invisible; its own file survives, and the socket is absent.
    engine = DockerEngine()
    peer = data / 'peer'
    peer.mkdir()
    (peer / 'secret').write_text('not shared with other attempts')
    own = data / 'isolation'
    own.mkdir(mode=0o777)
    own.chmod(0o777)
    mount = {'Type': 'volume', 'Source': 'veriruntime-data', 'Target': '/work',
             'VolumeOptions': {'NoCopy': True, 'Subpath': own.relative_to('/data').as_posix()}}
    name = 'vrun-isolation-' + uuid.uuid4().hex
    config = worker_config(registry.get_profile('cbmc').path, [], 64, mount)
    config['Entrypoint'] = ['python3', '-c']
    config['Cmd'] = ["from pathlib import Path; import os; assert not Path('/data').exists(); assert not Path('/var/run/docker.sock').exists(); assert not Path('/work/../peer/secret').exists(); Path('/work/own').write_text('isolated'); print('isolation passed')"]
    try:
        engine.create(name, config)
        engine.request('POST', f'/containers/{name}/start')
        state = engine.request('POST', f'/containers/{name}/wait')
        assert state['StatusCode'] == 0, engine.logs(name)
        assert (own / 'own').read_text() == 'isolated' and (peer / 'secret').read_text() == 'not shared with other attempts'
    finally:
        engine.remove(name)
    name = 'vrun-oom-' + uuid.uuid4().hex
    config = worker_config(registry.get_profile('cbmc').path, [], 64)
    config['Entrypoint'] = ['python3', '-c']
    config['Cmd'] = ['data=bytearray(256*1024*1024)']
    try:
        engine.create(name, config)
        engine.request('POST', f'/containers/{name}/start')
        engine.request('POST', f'/containers/{name}/wait')
        info = engine.request('GET', f'/containers/{name}/json')
        assert info['State']['OOMKilled'], info['State']
        (own / 'container-state.json').write_text(json.dumps({'state': info['State']}))
        parsed = registry.get('cbmc').parse_attempt_result('', '', 137, own)
        assert parsed.status.value == 'OOM' and parsed.verdict == Verdict.UNKNOWN
        records['oom'] = to_data(parsed)
    finally:
        engine.remove(name)
    remaining = engine.request('GET', '/containers/json?all=true&filters=%7B%22label%22%3A%5B%22org.veriruntime.managed%3Dattempt%22%5D%7D')
    assert not remaining, remaining
    summary = {'platform': 'linux/amd64', 'profiles': to_data(profiles), 'confirmations': minimum,
               'safe': 'SAFE', 'unsafe': 'UNSAFE', 'cache_repeat_verifier_executions': 0,
               'workflow': ['SAFE', 'UNSAFE'], 'unsupported': 'UNKNOWN', 'timeout': 'UNKNOWN',
               'isolated_mounts': True, 'oom': 'UNKNOWN / OOM',
               'remaining_worker_containers': len(remaining), 'records': records}
    (output / 'acceptance.json').write_text(json.dumps(summary, indent=2))
    if export_dir:
        export = Path(export_dir)
        export.mkdir(parents=True, exist_ok=True)
        (export / 'acceptance.json').write_text(json.dumps(summary, indent=2))
    print('Docker acceptance passed; evidence:', output / 'acceptance.json', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-cpachecker', action='store_true')
    parser.add_argument('--export-dir')
    args = parser.parse_args()
    asyncio.run(main(args.with_cpachecker, args.export_dir))
