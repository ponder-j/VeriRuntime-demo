#!/usr/bin/env python3
"""Real default-sol planning, two-family verification/cache, and UNKNOWN feedback."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import uuid

from veriruntime.dsl import load_workflow, load_task
from veriruntime.model import Requirements, VerificationProperty, VerificationWorkflow, to_data
from veriruntime.planner import CodexPlanner, ExperimentRunner
from veriruntime.service import VerificationService
from veriruntime.tools import default_registry

ROOT = Path(__file__).resolve().parents[1]


async def experiment(args):
    base = Path(args.data_dir).resolve()
    data = base / uuid.uuid4().hex
    service = VerificationService(data, registry=default_registry(ROOT))
    seed = load_workflow(ROOT / 'examples/tasks/assertion_workflow.json')
    # The caller explicitly requests two confirmations before invoking the planner.
    seed = replace(seed, goals=tuple(replace(g, requirements=Requirements(2)) for g in seed.goals))
    if any(len(service.registry.compatible_tools(g)) < 2 for g in seed.goals):
        raise ValueError('Install at least two real verifier families before running this experiment')
    runner = ExperimentRunner(service, CodexPlanner(timeout_sec=args.planner_timeout))
    request = ('Verify the existing C assertions in both supplied inputs. First verify input-0. '
        'Execute input-1 only after input-0 is SAFE with at least two independent confirmations. '
        'Keep the original goals, semantics, budgets, and confirmation requirements. '
        'The second input may be UNSAFE; that is a meaningful verification answer.')
    # A fresh store makes the first miss reproducible while preserving earlier runs.
    first = await runner.run(seed, request)
    assert first.status == 'COMPLETED', to_data(first.diagnostics)
    assert first.verifier_executions >= 4
    second = await runner.run(seed, request)
    assert second.status == 'COMPLETED' and second.verifier_executions == 0
    for result in (first, second):
        goals = result.rounds[-1]['execution']['goals']
        assert [g['report']['result']['verdict'] for g in goals] == ['SAFE','UNSAFE']
        assert all(g['report']['result']['confirmations'] >= 2 for g in goals)
        assert len(result.rounds[-1]['proposal']['workflow_document']['workflow']['dependencies']) == 1
    unknown_seed = VerificationWorkflow.single(replace(load_task(ROOT / 'examples/tasks/safe_assert.json'),
        property=VerificationProperty('memory_safety')))
    unknown = await runner.run(unknown_seed,
        'Verify the supplied memory_safety goal. Keep its property and source unchanged. '
        'If the interface or runtime cannot support it, preserve UNKNOWN and explain the limitation; '
        'do not replace it with assertion_safety or invent evidence.')
    assert unknown.status == 'INCOMPLETE' and unknown.verifier_executions == 0
    assert unknown.stop_reason in ('unchanged_workflow','round_limit')
    assert len(unknown.rounds) == 2
    output = {'model': runner.planner.model, 'first':to_data(first),'second':to_data(second),'unknown':to_data(unknown)}
    (data / 'llm-experiment-results.json').write_text(json.dumps(output, indent=2))
    (base / 'latest.json').write_text(json.dumps({'record':str(data/'llm-experiment-results.json')},indent=2))
    print(json.dumps({'model':runner.planner.model,'first':first.status,'first_verifier_executions':first.verifier_executions,
        'second':second.status,'second_verifier_executions':second.verifier_executions,
        'unknown':unknown.status,'unknown_stop_reason':unknown.stop_reason,'unknown_planning_rounds':len(unknown.rounds),
        'record':str(data/'llm-experiment-results.json')}, indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',default=str(ROOT/'.veriruntime/llm-demo'))
    parser.add_argument('--planner-timeout',type=float,default=180)
    asyncio.run(experiment(parser.parse_args()))


if __name__ == '__main__':
    main()
