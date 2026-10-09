#!/bin/bash
# Noesis PDF Reader Lite — launcher dell'app.
#
# Self-locating: usa il checkout in cui risiede (nessun path hardcoded),
# lanciabile da qualsiasi directory e senza attivare il venv.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec "$ROOT/.venv/bin/python" "$ROOT/main.py" "$@"
