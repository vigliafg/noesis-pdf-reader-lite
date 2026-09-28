#!/usr/bin/env python3
"""Genera ``corpus.json`` per l'harness di valutazione.

- ha22: due bunch **fissi** (A = 1230–1240, B = 2230–2240);
- ogni altro PDF in ``--pdf-dir``: **10 pagine casuali oltre --min-page**,
  estratte con seed deterministico (riproducibile).

Uso::

    ../../.venv/bin/python make_corpus.py
    ../../.venv/bin/python make_corpus.py --pdf-dir /percorso/pdfs --sample 10
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import pymupdf

from evalutil import (
    CORPUS_PATH,
    DEFAULT_EXTERNAL_DIR,
    REPO_PDF,
    default_corpus,
    save_json,
)


def _page_count(pdf: Path) -> int:
    with pymupdf.open(str(pdf)) as doc:
        return doc.page_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", default=str(DEFAULT_EXTERNAL_DIR),
                        help="cartella con gli altri PDF (default: %(default)s)")
    parser.add_argument("--seed", type=int, default=20260928)
    parser.add_argument("--sample", type=int, default=10, help="pagine casuali per PDF")
    parser.add_argument("--min-page", type=int, default=300,
                        help="campiona oltre questa pagina (1-based)")
    parser.add_argument("--out", default=str(CORPUS_PATH))
    args = parser.parse_args(argv)

    pdf_dir = Path(args.pdf_dir)
    ha22 = REPO_PDF if REPO_PDF.is_file() else pdf_dir / "ha22.pdf"

    bunches: list[dict] = []
    if ha22.is_file():
        for b in default_corpus()["bunches"]:
            b = dict(b)
            b["pdf"] = str(ha22)
            bunches.append(b)
        print(f"[fisso] {ha22.name}: A={len(bunches[0]['pages'])} pagine, "
              f"B={len(bunches[1]['pages'])} pagine")
    else:
        print(f"ATTENZIONE: {ha22} non trovato, salto i bunch fissi")

    if not pdf_dir.is_dir():
        print(f"ATTENZIONE: cartella PDF '{pdf_dir}' non trovata, solo bunch fissi")
    else:
        # Esclude i PDF già coperti dai bunch fissi (anche se in un'altra cartella).
        fixed_names = {Path(b["pdf"]).name for b in bunches}
        for pdf in sorted(pdf_dir.glob("*.pdf")):
            if pdf.name in fixed_names:
                continue
            try:
                total = _page_count(pdf)
            except Exception as exc:  # noqa: BLE001
                print(f"  skip {pdf.name}: {type(exc).__name__}: {exc}")
                continue
            if total <= args.min_page:
                print(f"  skip {pdf.name}: solo {total} pagine")
                continue
            lo = args.min_page + 1
            k = min(args.sample, total - lo + 1)
            rng = random.Random(f"{pdf.stem}-{args.seed}")
            pages = sorted(rng.sample(range(lo, total + 1), k))
            bunches.append({
                "id": f"{pdf.stem}_r1",
                "pdf": str(pdf),
                "pages": pages,
                "origin": "random",
                "note": f"{k} pagine casuali >{args.min_page} (seed {args.seed})",
            })
            print(f"[random] {pdf.name}: {k} pagine → {pages}")

    save_json(Path(args.out), {"seed": args.seed, "bunches": bunches})
    print(f"\nScritto {args.out}: {len(bunches)} bunch, "
          f"{sum(len(b['pages']) for b in bunches)} pagine totali")
    return 0


if __name__ == "__main__":
    sys.exit(main())
