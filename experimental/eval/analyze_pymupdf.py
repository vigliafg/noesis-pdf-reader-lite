#!/usr/bin/env python3
"""Analisi delle debolezze di PyMuPDF4LLM sui bunch del corpus.

Scansiona gli output ``out/<bunch>/pymupdf4llm/{raw,engine}/*.md`` e produce
``out/pymupdf_weaknesses.md`` con metriche per pagina e classifiche dei casi
peggiori:

- **rumore di layout**: numeri di pagina, header/footer di stampa
  (``HPIM21e_…indd``, orari), header di capitolo;
- **tabelle**: righe, celle vuote adiacenti (``||``), tabelle a 1 colonna;
- **immagini**: blocchi ``picture text`` e didascalie;
- **heading**: quanti titoli ``#`` e variazione raw→engine;
- **senso di lettura/spaziatura**: perdita di spazi tra parole nell'output
  ``engine`` rispetto al ``raw`` (bug noto su alcune pagine).

Uso (da experimental/eval):
    ../../.venv/bin/python analyze_pymupdf.py
"""

from __future__ import annotations

import re
import sys

from evalutil import bunch_pages, items, md_dir, OUT_DIR

PAGE_NUM_RE = re.compile(r"^\s*\**\s*\d{1,4}\s*\**\s*$")
FOOTER_RE = re.compile(r"(indd|HPIM21e|Downloaded from|\d{1,2}:\d{2}\s*(AM|PM))", re.I)
CHAPTER_RE = re.compile(r"^\s*\**\s*CHAPTER\b", re.I)
PICTURE_RE = re.compile(r"<!--\s*Start of picture text")
CAPTION_RE = re.compile(r"^\s*\**\s*(FIGURE|FIG\.|TABLE)\s", re.I)
HEADING_RE = re.compile(r"^#{1,6}\s")
BULLET_RE = re.compile(r"^\s*[-*]\s")
TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")


def _read(bunch: str, mode: str, page: int) -> str:
    try:
        return (md_dir(bunch, "pymupdf4llm", mode) / f"p{page}.md").read_text(encoding="utf-8")
    except OSError:
        return ""


def _metrics(text: str) -> dict:
    lines = text.splitlines()
    table_rows = [ln for ln in lines if TABLE_ROW_RE.match(ln)]
    return {
        "chars": len(text),
        "spaces_per100": round(100 * text.count(" ") / max(len(text), 1), 2),
        "page_nums": sum(1 for ln in lines if PAGE_NUM_RE.match(ln)),
        "footers": sum(1 for ln in lines if FOOTER_RE.search(ln)),
        "chapter_headers": sum(1 for ln in lines if CHAPTER_RE.match(ln)),
        "picture_blocks": len(PICTURE_RE.findall(text)),
        "captions": sum(1 for ln in lines if CAPTION_RE.match(ln)),
        "headings": sum(1 for ln in lines if HEADING_RE.match(ln)),
        "bullets": sum(1 for ln in lines if BULLET_RE.match(ln)),
        "table_rows": len(table_rows),
        "empty_cells": sum(ln.count("||") for ln in table_rows),
    }


def main() -> int:
    rows: list[dict] = []
    for bunch in items():
        bid = bunch["id"]
        for page in bunch_pages(bid):
            raw = _read(bid, "raw", page)
            eng = _read(bid, "engine", page)
            mr, me = _metrics(raw), _metrics(eng)
            rows.append({
                "bunch": bid,
                "page": page,
                "raw": mr,
                "engine": me,
                "space_loss": round(mr["spaces_per100"] - me["spaces_per100"], 2),
                "noise": mr["page_nums"] + mr["footers"] + mr["chapter_headers"],
                "heading_delta": mr["headings"] - me["headings"],
            })

    lines = ["# Debolezze PyMuPDF4LLM — scan delle 92 pagine", ""]

    def _table(title: str, key, rev=True, limit=15, cols=None):
        lines.append(f"## {title}")
        lines.append("")
        chosen = sorted(rows, key=key, reverse=rev)[:limit]
        lines.append("| bunch | pagina | valore | contesto |")
        lines.append("|---|---:|---:|---|")
        for r in chosen:
            ctx = (f"rumore={r['noise']} (num={r['raw']['page_nums']}, "
                   f"foot={r['raw']['footers']}, chap={r['raw']['chapter_headers']}) "
                   f"tabelle={r['raw']['table_rows']} (celle vuote {r['raw']['empty_cells']}) "
                   f"pic={r['raw']['picture_blocks']} heading={r['raw']['headings']} "
                   f"spazi/100 {r['raw']['spaces_per100']}→{r['engine']['spaces_per100']}")
            lines.append(f"| {r['bunch']} | p{r['page']} | {key(r)} | {ctx} |")
        lines.append("")

    _table("Più rumore di layout (numero pagina / footer di stampa / header capitolo)",
           lambda r: r["noise"])
    _table("Più celle vuote adiacenti nelle tabelle (struttura rotta)",
           lambda r: r["raw"]["empty_cells"])
    _table("Pagine con più blocchi 'picture text' (testo dentro immagini)",
           lambda r: r["raw"]["picture_blocks"])
    _table("Maggiore perdita di spazi raw→engine (bug del layout_engine)",
           lambda r: r["space_loss"])
    _table("Pagine con più heading rilevati dal raw",
           lambda r: r["raw"]["headings"])
    _table("Pagine SENZA heading nonostante probabili titoli (0 heading)",
           lambda r: 1 if r["raw"]["headings"] == 0 else 0)

    # Totali.
    tot_noise = sum(r["noise"] for r in rows)
    pages_noisy = sum(1 for r in rows if r["noise"] > 0)
    pages_loss = sum(1 for r in rows if r["space_loss"] > 1.0)
    pages_no_head = sum(1 for r in rows if r["raw"]["headings"] == 0)
    pages_table = sum(1 for r in rows if r["raw"]["table_rows"] > 0)
    lines += [
        "## Totali",
        "",
        f"- pagine con rumore di layout (numero/footer/header): **{pages_noisy}/92**",
        f"- pagine con perdita di spazi raw→engine >1/100: **{pages_loss}/92**",
        f"- pagine senza alcun heading `#`: **{pages_no_head}/92**",
        f"- pagine con almeno una tabella: **{pages_table}/92**",
        f"- occorrenze totali di rumore: **{tot_noise}**",
        "",
    ]
    out = OUT_DIR / "pymupdf_weaknesses.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Scritto {out}")
    print(f"noisy={pages_noisy}/92 space_loss_pages={pages_loss} no_heading={pages_no_head} tables={pages_table}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
