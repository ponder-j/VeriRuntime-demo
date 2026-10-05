"""User-facing command boundary. Machine output is JSON with --json."""
import argparse
import json
import sys

from veriruntime.dsl import DSLValidationError, parse
from veriruntime.model import to_data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="vrun", description="Declarative multi-verifier runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "parse"):
        sub = commands.add_parser(command)
        sub.add_argument("task")
    args = parser.parse_args(argv)
    try:
        logical = parse(args.task)
        if args.command == "validate":
            print(f"VALID {logical.task.id} semantic_key={logical.task.semantic_key}")
        else:
            print(json.dumps({"semantic_key": logical.task.semantic_key,
                              "logical_plan": to_data(logical)}, indent=2, sort_keys=True))
        return 0
    except DSLValidationError as exc:
        print(f"Invalid task: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
