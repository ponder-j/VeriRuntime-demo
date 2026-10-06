#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/run_llm_experiment.py "$@"
