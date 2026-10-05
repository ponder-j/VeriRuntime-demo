#!/usr/bin/env python3
"""Executable acceptance demo using only real verifiers, with scoped cache clearing."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, '-m', 'veriruntime']
DATA = '.veriruntime/demo'


def run(args, capture=False):
    print('\n$ vrun ' + ' '.join(args), flush=True)
    result = subprocess.run(CLI + args, cwd=ROOT, text=True, capture_output=capture, check=True)
    return json.loads(result.stdout) if capture else None


def main():
    run(['doctor'])
    profiles = run(['doctor', '--json'], True)
    assert len([p for p in profiles if p['available']]) >= 2, 'Install at least two real verifiers first'
    run(['cache', 'clear', '--demo', '--data-dir', DATA])
    task = 'examples/tasks/unsafe_assert.json'
    run(['validate', task])
    run(['explain', task, '--data-dir', DATA])
    first = run(['verify', '--explain', task, '--data-dir', DATA, '--json'], True)
    a = first['goals'][0]['report']
    assert a['result']['verdict'] == 'UNSAFE' and not a['result']['cache_hit']
    assert len(a['attempts']) >= 1
    print('CACHE MISS:', a['result']['verdict'], '; verifier executions:', len(a['attempts']))
    for attempt in a['attempts']:
        print(attempt['tool'], attempt['status'], attempt['verdict'], round(attempt['wall_time_sec'], 3), 's')
    run(['explain', '--analyze', task, '--data-dir', DATA])
    second = run(['verify', '--explain', task, '--data-dir', DATA, '--json'], True)
    b = second['goals'][0]['report']
    assert b['result']['cache_hit'] and b['result']['verdict'] == a['result']['verdict'] and not b['attempts']
    print('CACHE HIT:', b['result']['verdict'], '; verifier executions: 0')
    safe = run(['verify', 'examples/tasks/safe_assert.json', '--data-dir', DATA, '--json'], True)
    assert safe['goals'][0]['report']['result']['verdict'] == 'SAFE'
    print('safe program: SAFE')
    # M7 cross-check examples are automatically included once present.
    for case in ('unsafe', 'safe'):
        path = f'examples/tasks/{case}_assert_crosscheck.json'
        if (ROOT / path).exists():
            check = run(['verify', path, '--explain', '--data-dir', DATA, '--json'], True)
            result = check['goals'][0]['report']['result']
            assert result['requirement_satisfied'] and result['confirmations'] >= 2
            print('cross-check:', result['goal_id'], result['verdict'], result['confirmations'], 'confirmations')
    run(['history', '--data-dir', DATA])
    provenance = run(['show', a['result']['execution_id'], '--data-dir', DATA, '--json'], True)
    assert provenance['attempts'] and provenance['artifact_records']
    out = ROOT / DATA / 'demo-record.json'
    out.write_text(json.dumps({'first': first, 'second': second, 'safe': safe, 'provenance': provenance}, indent=2))
    print('Demo passed. Full evidence:', out)


if __name__ == '__main__':
    main()
