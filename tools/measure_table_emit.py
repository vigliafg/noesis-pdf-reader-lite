#!/usr/bin/env python3
"""Valuta l'emissione "tabella" del page model vs il motore (dev-only).

Esperimento (poi **parcheggiato**, vedi docs/STUDIO-PAGE-MODEL §8): per ogni pagina
confronta l'md **emesso** dal motore con l'md ottenuto rendendo la/le
``TableItem`` del page model (griglia ``num_rows × num_cols``, colonne vuote
scartate, righe a tutta larghezza come intestazioni) + i testi. Usa la metrica
d'ordine del gate (``tools/verify_pages._order_report``).

Uso:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_table_emit.py \\
        corpus1/ce24.pdf:480 corpus1/ha22.pdf:1977 ...
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def render_table(t: dict) -> str:
    d = t.get("data") or {}
    nr, nc = d.get("num_rows", 0), d.get("num_cols", 0)
    g = [["" for _ in range(nc)] for _ in range(nr)]
    lab = [["" for _ in range(nc)] for _ in range(nr)]
    for c in d.get("table_cells") or []:
        r0, c0 = c.get("start_row_offset_idx", 0), c.get("start_col_offset_idx", 0)
        if 0 <= r0 < nr and 0 <= c0 < nc:
            g[r0][c0] = c.get("text", "")
            lab[r0][c0] = c.get("label", "")
    keep = [c for c in range(nc) if any(g[r][c].strip() for r in range(nr))]
    if not keep:
        return ""
    out: list[str] = []
    for r in range(nr):
        row = [g[r][c].replace("\n", " ") for c in keep]
        full = (len(keep) == 1) or all(not g[r][c].strip() for c in keep[1:])
        if lab[r][keep[0]] == "row_section" or (full and row and row[0]):
            if row[0]:
                out.append(f"\n**{row[0]}**\n")
            continue
        out.append("| " + " | ".join(x or " " for x in row) + " |")
    return "\n".join(out)


def render_page(pmd: dict) -> str:
    kinds = {k: pmd.get(k) or []
             for k in ("texts", "tables", "pictures", "groups")}

    def children(r: str) -> list[str]:
        k, i = r.split("/")[1], int(r.split("/")[2])
        return [c["cref"] for c in (kinds[k][i].get("children") or [])]

    out: list[str] = []

    def dfs(r: str) -> None:
        k = r.split("/")[1]
        if k == "texts":
            out.append(kinds[k][int(r.split("/")[2])].get("text", ""))
        elif k == "tables":
            out.append(render_table(kinds[k][int(r.split("/")[2])]))
        for c in children(r):
            dfs(c)

    for c in pmd["body"]["children"]:
        dfs(c["cref"])
    return "\n\n".join(x for x in out if x.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+", help="pdf:pagina (1-based)")
    args = ap.parse_args()

    import pymupdf
    import ir_layout
    import page_model
    import verify_pages as vp

    for spec in args.specs:
        pdf, _, page = spec.rpartition(":")
        idx = int(page) - 1
        with pymupdf.open(pdf) as doc:
            if idx >= doc.page_count:
                print(f"skip {Path(pdf).name} p{page} (fuori range)")
                continue
            p = doc[idx]
            pw = p.rect.width
            chunk = ir_layout.page_chunk(doc, idx)
            md_eng, _ = ir_layout.build_markdown(
                p, doc, idx, embed_figures=False, return_meta=True, chunk=chunk)
            _t, els = ir_layout._elements_from_chunk(chunk)
            boxes = ir_layout._full_width_boxes(p, pw)
        pmd = page_model.build_page_document(pdf, idx).to_dict()
        md_pm = render_page(pmd)
        re = vp._order_report(md_eng, els, pw, boxes=boxes)
        rp = vp._order_report(md_pm, els, pw, boxes=boxes)
        print(f"{Path(pdf).name} p{page} tables={len(pmd['tables'])}")
        print(f"   ENGINE   inv={re['inversions']:4} recall={re['content_recall']} "
              f"prec={re['content_precision']}")
        print(f"   PAGEMOD  inv={rp['inversions']:4} recall={rp['content_recall']} "
              f"prec={rp['content_precision']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
