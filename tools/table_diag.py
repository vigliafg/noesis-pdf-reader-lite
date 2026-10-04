#!/usr/bin/env python3
"""Diagnosi tabelle — **Stadio C.0**.

Per ogni pagina esporta, in JSON + Markdown:

- le tabelle dei ``page_boxes`` (content map) e le loro statistiche markdown;
- le tabelle **raw** del GNN (``layout_information``: ``h_lines``/``v_lines``);
- l'esito della **matrice di configurazioni** di ``page.find_tables``:
  ``{lines, lines_strict, text} × {use_layout True/False} × {union, refine}``;
- la nostra griglia a **copertura parole** (``ir_layout._grid_table_from_page``);
- per ogni candidato: righe/colonne e **word recall** vs le parole di pagina
  nella regione della tabella.

Serve a decidere **con i dati** quale configurazione recupera quale caso.
Nessuna modifica alla pipeline.

Uso (``--pages`` = **page_ui 1-based**, come piano/golden)::

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/table_diag.py \\
        --pdf corpus1/ha22.pdf --pages 230,231 --out /tmp/opencode/table_diag
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: matrice di configurazioni di find_tables (ordine = costo crescente)
CONFIGS: list[tuple[str, dict]] = [
    ("html_union_refine", {"use_layout": True, "union": True, "refine": True}),
    ("layout_lines", {"use_layout": True, "strategy": "lines"}),
    ("layout_lines_strict", {"use_layout": True, "strategy": "lines_strict"}),
    ("layout_text", {"use_layout": True, "strategy": "text"}),
    ("nolayout_lines", {"use_layout": False, "strategy": "lines"}),
    ("nolayout_lines_strict", {"use_layout": False, "strategy": "lines_strict"}),
    ("nolayout_text", {"use_layout": False, "strategy": "text"}),
]


def _norm_words(text: str, minlen: int = 4) -> set[str]:
    t = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return {w for w in t.split() if len(w) >= minlen}


def _page_words_in(page, rect, pad: float = 3.0) -> list[str]:
    x0, y0, x1, y1 = rect
    return [w[4] for w in page.get_text("words")
            if w[0] >= x0 - pad and w[2] <= x1 + pad
            and w[1] >= y0 - pad and w[3] <= y1 + pad]


def _recall(ref_words: list[str], text: str) -> float:
    ref = _norm_words(" ".join(ref_words))
    if not ref:
        return 1.0
    return round(len(ref & _norm_words(text)) / len(ref), 4)


def _grid_counts(grid) -> dict:
    if grid is None:
        return {"h_lines": 0, "v_lines": 0}
    try:
        return {"h_lines": len(grid.h_lines), "v_lines": len(grid.v_lines)}
    except Exception:
        return {"h_lines": 0, "v_lines": 0}


def _find_tables_result(page, cfg: dict, rect) -> dict:
    try:
        tf = page.find_tables(**cfg)
        tables = list(tf.tables)
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e), "n": 0, "tables": []}
    out = []
    for t in tables:
        try:
            rows = t.extract() or []
            ncol = max((len(r) for r in rows), default=0)
            text = " ".join(str(c) for r in rows for c in r if c)
        except Exception as e:  # noqa: BLE001
            rows, ncol, text = [], 0, ""
            out.append({"error": repr(e)})
            continue
        tb = tuple(t.bbox)
        out.append({
            "bbox": [round(v, 1) for v in tb],
            "rows": len(rows), "cols": ncol,
            "recall": _recall(_page_words_in(page, tb), text),
        })
    return {"n": len(tables), "tables": out}


def _diag_page(doc, page_ui: int) -> dict:
    import pymupdf4llm  # attiva il layout

    import ir_layout
    import layout_proxies

    idx = page_ui - 1
    page = doc[idx]
    res: dict = {"page_ui": page_ui, "page_idx": idx,
                 "page_width": round(page.rect.width, 1)}

    # 1) layout raw: griglie GNN
    try:
        page.get_layout(return_raw=True)
    except Exception:
        pass
    raw = getattr(page, "layout_information", None) or []
    raw_tables = []
    for b in raw:
        if not isinstance(b, dict) or b.get("class_name") != "table":
            continue
        gb = tuple(b.get("group_bbox") or (0, 0, 0, 0))
        g = _grid_counts(b.get("table_grid"))
        raw_tables.append({"bbox": [round(v, 1) for v in gb], **g,
                           "rows": g["h_lines"] + 1, "cols": g["v_lines"] + 1})
    res["gnn_raw_tables"] = raw_tables

    # 2) matrice find_tables (usa il layout raw appena calcolato)
    res["find_tables"] = {name: _find_tables_result(page, cfg, None)
                          for name, cfg in CONFIGS}

    # 3) content map (page_boxes) + nostra griglia a copertura
    chunk = ir_layout.page_chunk(doc, idx)
    _text, els = ir_layout._elements_from_chunk(chunk)
    cm_tables = []
    for e in els:
        if e.get("class") != "table":
            continue
        st = layout_proxies._md_table_stats(e.get("text", ""))
        cm_tables.append({"bbox": [round(v, 1) for v in e["bbox"]],
                          "rows": st["rows"], "cols": st["cols"],
                          "empty_ratio": round(st["empty_ratio"], 2)})
    res["content_map_tables"] = cm_tables
    res["proxies"] = {
        "table": layout_proxies.table_proxy(els)["score"],
        "layout_class": layout_proxies.layout_class(els, page.rect.width),
    }

    # 4) griglia a copertura parole sulle regioni raw e content map
    cover = []
    regions = [(t["bbox"], t.get("cols", 0)) for t in raw_tables]
    regions += [(t["bbox"], t["cols"]) for t in cm_tables]
    seen = set()
    for bbox, ncol in regions:
        key = tuple(bbox)
        if key in seen:
            continue
        seen.add(key)
        try:
            grid = ir_layout._grid_table_from_page(page, tuple(bbox), ncol)
        except Exception:
            grid = ""
        cover.append({"bbox": bbox,
                      "recall": _recall(_page_words_in(page, tuple(bbox)), grid),
                      "len": len(grid)})
    res["coverage_grid"] = cover
    return res


def _md_report(results: list[dict]) -> str:
    lines = ["# Diagnosi tabelle (Stadio C.0)", ""]
    for r in results:
        lines += [f"## pagina {r['page_ui']} (idx {r['page_idx']}, "
                  f"pw {r['page_width']}) — {r['proxies']['layout_class']} "
                  f"(table_proxy {r['proxies']['table']})", ""]
        lines += ["### Griglie GNN raw", "",
                  "| bbox | h_lines | v_lines | rows | cols |",
                  "|---|---|---|---|---|"]
        for t in r["gnn_raw_tables"]:
            lines.append(f"| {t['bbox']} | {t['h_lines']} | {t['v_lines']} | "
                         f"{t['rows']} | {t['cols']} |")
        lines += ["", "### Content map (page_boxes)", "",
                  "| bbox | rows | cols | empty |", "|---|---|---|---|"]
        for t in r["content_map_tables"]:
            lines.append(f"| {t['bbox']} | {t['rows']} | {t['cols']} | "
                         f"{t['empty_ratio']} |")
        lines += ["", "### find_tables: matrice", "",
                  "| config | n | tabelle (rows×cols, recall) |", "|---|---|---|"]
        for name, ft in r["find_tables"].items():
            if ft.get("error"):
                cell = f"ERR {ft['error'][:60]}"
            else:
                cell = "; ".join(f"{t['rows']}×{t['cols']} r={t['recall']}"
                                 for t in ft["tables"]) or "-"
            lines.append(f"| {name} | {ft['n']} | {cell} |")
        lines += ["", "### Griglia a copertura parole", "",
                  "| bbox | recall | len |", "|---|---|---|"]
        for c in r["coverage_grid"]:
            lines.append(f"| {c['bbox']} | {c['recall']} | {c['len']} |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnosi tabelle (C.0)")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--pages", required=True, help="page_ui 1-based, es. 230,231")
    ap.add_argument("--out", default="/tmp/opencode/table_diag")
    args = ap.parse_args()

    import pymupdf

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = [int(p) for p in args.pages.replace(" ", "").split(",") if p]
    pdf = str(Path(args.pdf).resolve())
    doc = pymupdf.open(pdf)
    results = []
    try:
        for p in pages:
            results.append(_diag_page(doc, p))
    finally:
        doc.close()

    stem = Path(pdf).stem
    (out / f"{stem}_table_diag.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"{stem}_table_diag.md").write_text(_md_report(results),
                                               encoding="utf-8")
    print(f"Report: {out / (stem + '_table_diag.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
