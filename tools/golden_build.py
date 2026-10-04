#!/usr/bin/env python3
"""Costruisce/aggiorna il **golden set strutturale** CI-safe (Fase 0.1c).

Non usa rete né advisor: registra per pagina la **struttura** (classi GNN
presenti), la **classe di layout** e i **punteggi dei proxy**. Serve da
**regressione per classe**: ``tests/test_golden_layout.py`` verifica che i proxy
non peggiorino oltre tolleranza e che la struttura non cambi. L'arbitraggio
advisor (Fase 0.1b) può aggiungere in seguito etichette di qualità.

Uso (``--pages`` accetta **page_ui 1-based**, come nei report dello studio)::

    .venv/bin/python tools/golden_build.py \\
        --pages "corpus1/ha22.pdf:230,corpus2/fe22.pdf:894" \\
        --out tests/data/golden
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _slug(cls: str) -> str:
    return cls.replace("/", "_").replace(" ", "_")


def _entry(pdf_rel: str, page_ui: int) -> dict:
    import pymupdf

    import ir_layout
    import layout_proxies

    doc = pymupdf.open(_ROOT / pdf_rel)
    try:
        idx = page_ui - 1
        if idx < 0 or idx >= len(doc):
            raise SystemExit(f"pagina fuori range: {pdf_rel} p{page_ui} "
                             f"(max {len(doc)})")
        page = doc[idx]
        _text, els = ir_layout.page_elements(doc, idx)
        prox = layout_proxies.all_proxies(els, page.rect.width)
        classes = {e.get("class") for e in els}
        return {
            "pdf": pdf_rel,
            "page": idx,
            "page_ui": page_ui,
            "layout_class": prox["layout_class"],
            "structure": {
                "has_table": "table" in classes,
                "has_picture": "picture" in classes,
                "has_formula": "formula" in classes,
                "n_columns": prox["order"]["metrics"]["n_columns"],
            },
            "proxies": {
                "order": prox["order"]["score"],
                "table": prox["table"]["score"],
                "figure": prox["figure"]["score"],
                "text": prox["text"]["score"],
                "confidence": prox["confidence"],
            },
            "advisor": None,
        }
    finally:
        doc.close()


def _merge(out_dir: Path, entry: dict, update: bool) -> Path:
    path = out_dir / f"{_slug(entry['layout_class'])}.json"
    data = {"class": entry["layout_class"], "pages": []}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    pages = [p for p in data.get("pages", [])
             if not (p["pdf"] == entry["pdf"] and p["page"] == entry["page"])]
    pages.append(entry)
    pages.sort(key=lambda p: (p["pdf"], p["page"]))
    data["pages"] = pages
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Builder golden strutturale")
    ap.add_argument("--pages", default="",
                    help="lista 'pdf:page_ui' separata da virgole")
    ap.add_argument("--out", default="tests/data/golden")
    ap.add_argument("--update", action="store_true",
                    help="scrive davvero (default: dry-run)")
    args = ap.parse_args()

    specs = [s.strip() for s in args.pages.split(",") if s.strip()]
    if not specs:
        ap.error("serve --pages")
    out = Path(args.out)
    if not out.is_absolute():
        out = _ROOT / out
    for spec in specs:
        pdf_rel, _, page = spec.rpartition(":")
        entry = _entry(pdf_rel, int(page))
        path = _merge(out, entry, args.update)
        print(f"{pdf_rel} p{entry['page_ui']} -> {entry['layout_class']} "
              f"proxies={entry['proxies']} -> {path.name}"
              + ("" if args.update else " [dry-run]"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
