#!/usr/bin/env sh
# Starts FreeShape; installs its requirements automatically on the first run.
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then echo "Python 3.10-3.13 is required: https://www.python.org"; exit 1; fi
exec "$PY" start.py "$@"
