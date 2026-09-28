#!/usr/bin/env bash
# Orchestratore dell'harness di valutazione PyMuPDF4LLM vs Xberg.
#
#   ./run_all.sh
#
# Prerequisiti: venv con requirements.txt + requirements-xberg.txt.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
PY="$ROOT/.venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "ERRORE: $PY non trovato. Crea il venv e installa i requirements."
    exit 1
fi

cd "$HERE"

echo "=== 0/5 corpus ==="
"$PY" make_corpus.py

BUNCHES="$("$PY" -c 'import evalutil; print(" ".join(evalutil.bunch_ids()))')"
echo "bunch: $BUNCHES"

echo "=== 1/5 slices ==="
"$PY" make_slices.py

echo "=== 2/5 render ==="
"$PY" render_pages.py

echo "=== 3/5 engines (cold + warm) ==="
# La cache di contenuto di Xberg va svuotata UNA volta prima dell'intera
# passata cold: così la passata warm riusa davvero i risultati.
rm -rf "$HOME/.cache/xberg"
for bunch in $BUNCHES; do
    for engine in pymupdf4llm xberg; do
        echo "--- $bunch / $engine / cold ---"
        "$PY" run_engines.py --bunch "$bunch" --engine "$engine" --pass-name cold \
            || echo "  (fallito: $bunch/$engine/cold)"
    done
done

for bunch in $BUNCHES; do
    for engine in pymupdf4llm xberg; do
        echo "--- $bunch / $engine / warm ---"
        "$PY" run_engines.py --bunch "$bunch" --engine "$engine" --pass-name warm \
            || echo "  (fallito: $bunch/$engine/warm)"
    done
done

echo "=== 4/5 report ==="
"$PY" make_report.py

echo "Fatto. Vedi out/summary.md, out/report.html, out/scorecard.md"
