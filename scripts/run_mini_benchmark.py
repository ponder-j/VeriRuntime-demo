#!/usr/bin/env python3
"""Small real-verifier benchmark using automatic per-goal physical optimization."""
import argparse
import asyncio
import csv
from dataclasses import replace
import json
from pathlib import Path
import sys

from veriruntime.dsl import load_task
from veriruntime.model import Requirements, to_data
from veriruntime.service import VerificationService

ROOT = Path(__file__).resolve().parents[1]


async def benchmark(args):
    data = Path(args.data_dir).resolve()
    service = VerificationService(data, cache_enabled=False)
    cases = json.loads((ROOT / 'examples/benchmark/manifest.json').read_text())
    rows, reports, mismatches = [], [], []
    print('verifier\ttask\tstatus\tverdict\twall_time_sec')
    for case in cases:
        task = load_task(ROOT / 'examples/benchmark' / case['task'])
        # Explicit caller-level trust request for this experiment; the runtime
        # receives this fixed obligation and never changes it during optimization.
        task = replace(task, requirements=Requirements(args.confirmations))
        execution = await service.verify_goal(task)
        result = execution.report.result
        for attempt in execution.report.attempts:
            row = {'verifier': attempt.tool, 'task': task.id, 'status': attempt.status.value,
                   'verdict': attempt.verdict.value, 'wall_time_sec': round(attempt.wall_time_sec, 6)}
            rows.append(row)
            print('\t'.join(str(v) for v in row.values()), flush=True)
        reports.append(to_data(execution))
        if result.verdict.value != case['expected']:
            mismatches.append({'task': task.id, 'expected': case['expected'], 'actual': result.verdict.value,
                               'diagnostics': to_data(result.diagnostics)})
    summary = {'cases': len(cases), 'verifier_executions': len(rows), 'confirmations_requested': args.confirmations,
               'unexpected_results': mismatches, 'verdict_counts': {v: sum(r['report']['result']['verdict'] == v for r in reports)
                   for v in ('SAFE','UNSAFE','UNKNOWN','CONFLICT')}}
    (data / 'benchmark-results.json').write_text(json.dumps({'summary': summary, 'executions': reports}, indent=2))
    with (data / 'benchmark-results.csv').open('w', newline='') as output:
        writer = csv.DictWriter(output, fieldnames=['verifier','task','status','verdict','wall_time_sec'])
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps(summary, indent=2))
    return 1 if mismatches else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(ROOT / '.veriruntime/benchmark'))
    parser.add_argument('--confirmations', type=int, default=2)
    args = parser.parse_args()
    return asyncio.run(benchmark(args))


if __name__ == '__main__':
    raise SystemExit(main())
