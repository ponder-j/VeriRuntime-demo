"""CLI boundary: declarative goals/workflows in, structured results out."""
import argparse
import asyncio
from pathlib import Path
import signal
import sys

from veriruntime.dsl import DSLValidationError, load_workflow
from veriruntime.observability import CLIRenderer
from veriruntime.runtime import Cancellation


def _execution_options(sub):
    sub.add_argument('--json', action='store_true')
    sub.add_argument('--data-dir', default='.veriruntime')
    sub.add_argument('--no-cache', action='store_true')


async def _cancellable(operation):
    token = Cancellation()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, token.cancel)
    try:
        return await operation(token)
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='vrun', description='Declarative multi-verifier runtime')
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('validate', 'parse'):
        sub = commands.add_parser(command)
        sub.add_argument('task')
    for command in ('tools', 'doctor'):
        sub = commands.add_parser(command)
        sub.add_argument('--json', action='store_true')
    for command in ('verify', 'explain'):
        sub = commands.add_parser(command)
        sub.add_argument('task')
        _execution_options(sub)
        if command == 'verify':
            sub.add_argument('--explain', action='store_true')
        else:
            sub.add_argument('--analyze', action='store_true')
    for command in ('plan', 'experiment'):
        sub = commands.add_parser(command, help='Upstream Codex semantic planning on existing C assertion inputs')
        sub.add_argument('task', help='Single-task or workflow input manifest')
        request = sub.add_mutually_exclusive_group(required=True)
        request.add_argument('--request', help='Natural-language planning requirement')
        request.add_argument('--request-file', help='UTF-8 planning requirement file')
        sub.add_argument('--model', help='Defaults to the configured sol model')
        sub.add_argument('--planner-timeout', type=float, default=180)
        sub.add_argument('--max-rounds', type=int, default=2)
        _execution_options(sub)
    sub = commands.add_parser('history')
    sub.add_argument('--limit', type=int, default=20)
    _execution_options(sub)
    sub = commands.add_parser('show')
    sub.add_argument('execution_id')
    _execution_options(sub)
    sub = commands.add_parser('cache')
    sub.add_argument('action', choices=['clear'])
    sub.add_argument('task', nargs='?')
    sub.add_argument('--demo', action='store_true')
    _execution_options(sub)
    args = parser.parse_args(argv)
    ui = CLIRenderer()
    try:
        if args.command in ('tools', 'doctor'):
            from veriruntime.tools import default_registry
            profiles = default_registry().discover()
            if args.json:
                ui.json(profiles)
            else:
                for p in profiles:
                    ui.emit(f'{p.name}: available={str(p.available).lower()} version={p.version or "-"}')
                    ui.emit(f'  path: {p.path or "-"}; properties: {", ".join(p.properties)}')
                    ui.emit(f'  semantics: {", ".join(p.c_standards)} / {", ".join(p.data_models)}')
                    if p.diagnostic:
                        ui.emit(f'  diagnostic: {p.diagnostic}')
            return 0
        if args.command in ('validate', 'parse'):
            workflow = load_workflow(args.task)
            if args.command == 'validate':
                ui.emit(f'VALID workflow={workflow.workflow_id} goals={len(workflow.goals)}')
                for goal in workflow.goals:
                    ui.emit(f'  {goal.id} semantic_key={goal.semantic_key}')
            else:
                ui.json({'logical_workflow': workflow, 'semantic_keys': {g.id: g.semantic_key for g in workflow.goals}})
            return 0
        from veriruntime.service import VerificationService
        service = VerificationService(args.data_dir, cache_enabled=not args.no_cache)
        if args.command in ('plan', 'experiment'):
            from veriruntime.planner import CodexPlanner, ExperimentRunner
            request = args.request if args.request is not None else Path(args.request_file).read_text(encoding='utf-8')
            seed = load_workflow(args.task)
            planner = CodexPlanner(model=args.model, timeout_sec=args.planner_timeout)
            runner = ExperimentRunner(service, planner, max_rounds=args.max_rounds)
            result = asyncio.run(_cancellable(lambda token: runner.run(seed, request,
                plan_only=args.command == 'plan', cancellation=token)))
            if args.json:
                ui.json(result)
            else:
                ui.emit(f'Experiment {result.experiment_id}: {result.status} ({result.stop_reason})')
                ui.emit(f'Model: {result.model}; planning rounds: {len(result.rounds)}; verifier executions: {result.verifier_executions}')
                ui.emit(f'Provenance: {result.directory}/experiment.json')
                for item in result.rounds:
                    for goal in item.get('execution', {}).get('goals', []):
                        value = goal['report']['result']
                        ui.emit(f'  {value["goal_id"]}: {value["verdict"]}; confirmations={value["confirmations"]}; cache_hit={value["cache_hit"]}')
                for diagnostic in result.diagnostics:
                    ui.emit(f'  {diagnostic["code"]}: {diagnostic["detail"]}')
            return 2 if result.status == 'ERROR' else 130 if result.status == 'CANCELLED' else 0
        if args.command == 'history':
            if not 1 <= args.limit <= 10000:
                parser.error('--limit must be between 1 and 10000')
            records = service.store.history(args.limit)
            if args.json:
                ui.json(records)
            else:
                for row in records:
                    result = row['result'] or {}
                    ui.emit(f'{row["id"]}  {row["goal_id"]}  {result.get("verdict", "IN_PROGRESS")}  cache_hit={result.get("cache_hit", False)}  {row["created_time"]}')
            return 0
        if args.command == 'show':
            record = service.store.show(args.execution_id)
            source_id = (record['result'] or {}).get('source_execution_id')
            if source_id:
                record['cache_source_provenance'] = service.store.show(source_id)
            ui.json(record)
            return 0
        if args.command == 'cache':
            if bool(args.task) == bool(args.demo):
                parser.error('cache clear requires a task path or --demo')
            paths = [Path(args.task)] if args.task else sorted((Path(__file__).resolve().parents[1] / 'examples/tasks').glob('*.json'))
            keys = [goal.semantic_key for path in paths for goal in load_workflow(path).goals]
            count = service.cache.clear(keys)
            ui.json({'cleared_entries': count}) if args.json else ui.emit(f'Cleared {count} selected cache entries; history and artifacts retained.')
            return 0
        workflow = load_workflow(args.task)
        analyze = args.command == 'explain' and args.analyze
        show_plan = args.command == 'explain' or args.explain
        if show_plan:
            optimizations = service.explain_workflow(workflow)
            if args.command == 'explain' and not analyze:
                if args.json:
                    ui.json({'logical_workflow': workflow, 'physical_execution': optimizations, 'tool_profiles': service.registry.profiles()})
                else:
                    ui.explain(workflow, optimizations, service.registry.profiles())
                return 0
            if not args.json:
                ui.explain(workflow, optimizations, service.registry.profiles())
        execution = asyncio.run(_cancellable(lambda token: service.verify(workflow, token)))
        ui.json(execution) if args.json else ui.execution(execution, service.cache_enabled)
        return 0
    except (DSLValidationError, KeyError, OSError, ValueError) as exc:
        print(f'vrun: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
