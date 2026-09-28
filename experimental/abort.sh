#!/usr/bin/env bash
# Wrapper sottile per abort_experiment.py (Linux/macOS).
#   ./experimental/abort.sh --yes
#   ./experimental/abort.sh --yes --purge-models --purge-venv
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
else
    PY=python
fi

exec "$PY" "$DIR/abort_experiment.py" "$@"
