#!/usr/bin/env bash
# Start WholeBoard on Linux or macOS. Creates a virtual environment on first run and
# reinstalls the dependencies whenever pyproject.toml changes.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -x .venv/bin/python ]; then
  "$PY" -m venv .venv
  .venv/bin/pip install --upgrade pip >/dev/null
fi
if [ ! -f .venv/.installed ] || [ pyproject.toml -nt .venv/.installed ]; then
  .venv/bin/pip install -e .
  touch .venv/.installed
fi
exec .venv/bin/python -m wholeboard "$@"
