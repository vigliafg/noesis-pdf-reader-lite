#!/bin/bash
# Harness unico di verifica — wrapper self-locating.
#
# Esegue il harness dell'agente (suite completa + pipeline E2E + arbitrato
# visivo) usando il venv del checkout in cui risiede, da qualsiasi directory.
#
# Uso:
#   tools/run-harness.sh            # full
#   tools/run-harness.sh --quick    # solo suite unit (veloce)
#   tools/run-harness.sh --corpus   # includi lo smoke su corpus reale
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/.venv/bin/python" "$ROOT/tools/harness.py" "$@"
