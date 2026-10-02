#!/bin/bash
# Esegue un blocco dello studio IR (150 pagine corpus150.json).
# Uso: tools/ir150.sh <blocco 1..N> <dimensione> [out]
set -u
B="${1:?blocco}"; SIZE="${2:-15}"; OUT="${3:-/tmp/opencode/ir150}"
cd /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite-ir || exit 1
mkdir -p "$OUT/block$B"
TSV="/tmp/opencode/block${B}.tsv"
.venv/bin/python - "$B" "$SIZE" > "$TSV" <<'PY'
import json, sys
from collections import defaultdict
b=int(sys.argv[1]); size=int(sys.argv[2])
d=json.load(open("corpus150.json"))
s=d[(b-1)*size:b*size]
g=defaultdict(list)
for e in s: g[e["path"]].append(e["page"])
for p,pages in g.items(): print(p, ",".join(map(str,sorted(set(pages)))))
PY
echo "### BLOCCO $B ($(wc -l < "$TSV") PDF) ###"
while read -r pdf pages; do
  name=$(basename "$pdf" .pdf)
  QT_QPA_PLATFORM=offscreen .venv/bin/python tools/verify_pages.py "$pdf" \
     --pages "$pages" --out "$OUT/block$B/$name" --pipelines current,ir 2>&1 \
     | grep -vE "Document parser|Tesseract|^Using|OCR on|Error opening|Failed loading|Please make sure"
done < "$TSV"
.venv/bin/python tools/ir_study_summary.py --dir "$OUT/block$B" 2>/dev/null > "$OUT/block$B/summary.md"
echo "--- BLOCCO $B: summary in $OUT/block$B/summary.md ---"
