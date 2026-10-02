#!/usr/bin/env python3
"""Ricalcola l'order_score (metrica a transizioni) dai md già salvati.

Motivo: la prima metrica (contiguità del testo per colonna) era inquinata dalla
**formattazione** (box/tabelle rese come markdown rompono la contiguità pur
essendo nell'ordine giusto). Qui l'ordine si misura così:

- ogni blocco di testo della content map diventa un **anchor** (prime parole);
- si trova la posizione dell'anchor nel md (normalizzato);
- si guarda la **sequenza delle colonne** in ordine di posizione: in un ordine
  colona-major ideale ci sono ``ncol-1`` transizioni; ogni transizione in più è
  interlacciamento.
- ``order = 1 - max(0, transizioni - (ncol-1)) / (n-1)``  (1 = perfetto).

Uso: .venv/bin/python tools/ir_study_rescore.py --dir /tmp/opencode/ir_study --pdfs corpus1
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
import sys  # noqa: E402
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _norm(t: str) -> str:
    t = t.lower()
    t = re.sub(r"-\s*\n\s*", "", t)
    t = re.sub(r"-\s+(?=[a-z])", "", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def _order_score(md: str, elements: list[dict], page_width: float) -> float:
    import main

    texty = [e for e in elements if e["class"] in ("text", "section-header", "title")
             and e["w"] < 0.6 * page_width]
    if not texty:
        return 1.0
    splits = main._detect_column_splits(
        [{"x0": e["bbox"][0], "x1": e["bbox"][2],
          "y0": e["bbox"][1], "y1": e["bbox"][3]} for e in texty], page_width)
    ncol = len(splits) + 1
    M = _norm(md)
    seq: list[tuple[int, float]] = []  # (colonna, posizione)
    for e in texty:
        mid = (e["bbox"][0] + e["bbox"][2]) / 2
        col = sum(1 for s in splits if mid > s)
        anchor = " ".join(_norm(e["text"]).split()[:6])
        if len(anchor) < 20:
            continue
        pos = M.find(anchor)
        if pos < 0:
            continue
        seq.append((col, pos))
    if len(seq) < 3:
        return 1.0
    seq.sort(key=lambda t: t[1])
    cols = [c for c, _ in seq]
    transitions = sum(1 for a, b in zip(cols, cols[1:]) if a != b)
    ideal = ncol - 1
    extra = max(0, transitions - ideal)
    return max(0.0, 1.0 - extra / (len(cols) - 1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--pdfs", default="corpus1")
    args = ap.parse_args()
    import pymupdf

    import ir_layout

    root = Path(args.dir)
    docs: dict[str, object] = {}
    updated = 0
    for jl in sorted(root.glob("*/report.jsonl")):
        rows = [json.loads(x) for x in jl.read_text().splitlines() if x.strip()]
        # ricostruisco il nome pdf dall'ultima colonna del path (nome cartella)
        name = jl.parent.name
        pdf = str(Path(args.pdfs) / f"{name}.pdf")
        if not Path(pdf).exists():
            print("pdf assente:", pdf); continue
        if pdf not in docs:
            docs[pdf] = pymupdf.open(pdf)
        doc = docs[pdf]
        for r in rows:
            idx = r["page_idx"]
            page = doc[idx]
            try:
                _t, els = ir_layout.page_elements(doc, idx)
            except Exception:
                els = []
            md = (jl.parent / r["pipeline"] / f"page_{idx:04d}.md")
            mdtext = md.read_text() if md.exists() else ""
            r["order_anchor"] = round(_order_score(mdtext, els, page.rect.width), 3)
            updated += 1
        jl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
                      encoding="utf-8")
    for d in docs.values():
        d.close()
    print(f"ricalcolato order_anchor su {updated} righe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
