#!/bin/sh
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -x "$task_root/.venv/bin/python" ]; then
    exec "$task_root/.venv/bin/python" "$task_root/scripts/bootstrap_verifiers.py" "$@"
fi
exec python3 "$task_root/scripts/bootstrap_verifiers.py" "$@"
