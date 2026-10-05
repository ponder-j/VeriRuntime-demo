#!/bin/sh
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$task_root"
exec "$task_root/.venv/bin/python" "$task_root/scripts/run_mini_benchmark.py" "$@"
