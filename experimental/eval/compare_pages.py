#!/usr/bin/env python3
"""Confronto automatico per pagina tra i due motori (proxy, poi arbitro visivo).

Per ogni pagina calcola similarità normalizzata, conteggi e flag tra i markdown
``raw`` di PyMuPDF4LLM e Xberg, e la stessa cosa in modalità ``engine``.

Uso (da experimental/eval):
    ../../.venv/bin/python compare_pages.py
"""

from __future__ import annotations

import difflib
import re
import sys

import evalutil
from evalutil import bunch_pages, items, md_dir, OUT_DIR

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def _norm(text: str) -> str:
    return _WS.sub(" ", _PUNCT.sub(" ", text.lower())).strip()


def _ratio(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _read(bunch: str, engine: str, mode: str, page: int) -> str:
    try:
        return (md_dir(bunch, engine, mode) / f"p{page}.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def main() -> int:
    lines = ["# Confronto automatico PyMuPDF4LLM vs Xberg", ""]
    per_bunch: list[tuple[str, float, float]] = []
    for bunch in items():
        bid = bunch["id"]
        lines.append(f"## {bid} — {bunch.get('note', '')}")
        lines.append("")
        lines.append("| pagina | chars py | chars xb | ratio raw | ratio engine | "
                     "tabelle py/xb | img py/xb | heading py/xb | � py/xb |")
        lines.append("|---|---:|---:|---:|---:|---|---|---|---|")
        r_raw: list[float] = []
        r_eng: list[float] = []
        for page in bunch_pages(bid):
            py_raw = _read(bid, "pymupdf4llm", "raw", page)
            xb_raw = _read(bid, "xberg", "raw", page)
            py_eng = _read(bid, "pymupdf4llm", "engine", page)
            xb_eng = _read(bid, "xberg", "engine", page)
            mpy, mxb = evalutil.text_metrics(py_raw), evalutil.text_metrics(xb_raw)
            ratio = _ratio(py_raw, xb_raw)
            ratio_e = _ratio(py_eng, xb_eng)
            r_raw.append(ratio)
            r_eng.append(ratio_e)
            lines.append(
                f"| p{page} | {mpy['chars']} | {mxb['chars']} | {ratio:.2f} | {ratio_e:.2f} | "
                f"{mpy['table_rows']}/{mxb['table_rows']} | "
                f"{mpy['images']}/{mxb['images']} | "
                f"{mpy['headings']}/{mxb['headings']} | "
                f"{mpy['replacement_chars']}/{mxb['replacement_chars']} |"
            )
        avg_raw = sum(r_raw) / len(r_raw) if r_raw else 0.0
        avg_eng = sum(r_eng) / len(r_eng) if r_eng else 0.0
        per_bunch.append((bid, avg_raw, avg_eng))
        lines.append("")
        lines.append(f"**Media bunch {bid}: ratio raw {avg_raw:.2f}, engine {avg_eng:.2f}**")
        lines.append("")

    lines.append("## Riepilogo media per bunch")
    lines.append("")
    lines.append("| bunch | ratio raw | ratio engine |")
    lines.append("|---|---:|---:|")
    for bid, a, b in per_bunch:
        lines.append(f"| {bid} | {a:.2f} | {b:.2f} |")
    lines.append("")

    out = OUT_DIR / "compare.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Scritto {out}")
    print("\n".join(f"{bid}: raw {a:.2f} engine {b:.2f}" for bid, a, b in per_bunch))
    return 0


if __name__ == "__main__":
    sys.exit(main())
