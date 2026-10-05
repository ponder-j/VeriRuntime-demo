"""User-facing command boundary. Machine output is JSON with --json."""
import argparse
import asyncio
import json
import sys

from veriruntime.dsl import DSLValidationError, load_workflow
from veriruntime.model import to_data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="vrun", description="Declarative multi-verifier runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "parse"):
        sub = commands.add_parser(command)
        sub.add_argument("task")
    for command in ("tools", "doctor"):
        sub = commands.add_parser(command)
        sub.add_argument("--json", action="store_true")
    sub = commands.add_parser("verify")
    sub.add_argument("task")
    sub.add_argument("--json", action="store_true")
    sub.add_argument("--data-dir", default=".veriruntime")
    sub.add_argument("--no-cache", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command in ("tools", "doctor"):
            from veriruntime.tools import default_registry
            profiles = default_registry().discover()
            if args.json:
                print(json.dumps(to_data(profiles), indent=2))
            else:
                for p in profiles:
                    print(f"{p.name}: available={str(p.available).lower()} version={p.version or '-'}")
                    print(f"  path: {p.path or '-'}; properties: {', '.join(p.properties)}")
                    print(f"  semantics: {', '.join(p.c_standards)} / {', '.join(p.data_models)}")
                    if p.diagnostic:
                        print(f"  diagnostic: {p.diagnostic}")
            return 0
        workflow = load_workflow(args.task)
        if args.command == "verify":
            from veriruntime.service import VerificationService
            service = VerificationService(args.data_dir, cache_enabled=not args.no_cache)
            execution = asyncio.run(service.verify(workflow))
            if args.json:
                print(json.dumps(to_data(execution), indent=2))
            else:
                for goal in execution.goals:
                    result = goal.report.result
                    print(f"Goal {result.goal_id}: {result.verdict.value}; confirmations={result.confirmations}; requirement_satisfied={result.requirement_satisfied}")
                    print(f"  execution: {result.execution_id}; wall time: {result.wall_time_sec:.3f}s")
                    print(f"  verifier executions: {len(goal.report.attempts)}")
                    for attempt in goal.report.attempts:
                        print(f"  {attempt.tool}: {attempt.status.value} / {attempt.verdict.value} ({attempt.wall_time_sec:.3f}s)")
                    if result.failure_reasons:
                        print(f"  diagnostics: {', '.join(result.failure_reasons)}")
            return 0
        if args.command == "validate":
            print(f"VALID workflow={workflow.workflow_id} goals={len(workflow.goals)}")
            for goal in workflow.goals:
                print(f"  {goal.id} semantic_key={goal.semantic_key}")
        else:
            print(json.dumps({"logical_workflow": to_data(workflow),
                              "semantic_keys": {g.id: g.semantic_key for g in workflow.goals}}, indent=2, sort_keys=True))
        return 0
    except DSLValidationError as exc:
        print(f"Invalid task: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
