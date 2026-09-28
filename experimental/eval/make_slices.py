#!/usr/bin/env python3
"""Ritaglia ogni bunch del corpus in un PDF di 10–11 pagine.

Xberg estrae a livello documento: estrarre interi volumi (fino a 4132 pagine,
300 MB) sarebbe impraticabile. Ritagliando i bunch, **entrambi** i motori
lavorano sugli stessi PDF piccoli → confronto equo e tempi ragionevoli.

Verifica anche che il testo di ogni pagina ritagliata coincida con quello
dell'originale (nessuna perdita nel ritaglio).
"""

from __future__ import annotations

import re
import sys

import pymupdf

from evalutil import bunch_pages, items, original_page, pdf_path, save_json, slice_path, OUT_DIR

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def main() -> int:
    report: dict[str, dict] = {}
    for bunch in items():
        bid = bunch["id"]
        pdf = pdf_path(bid)
        if not pdf.is_file():
            print(f"[{bid}] ERRORE: PDF non trovato: {pdf}")
            report[bid] = {"error": f"PDF non trovato: {pdf}"}
            continue

        pages = bunch_pages(bid)
        out = slice_path(bid)
        out.parent.mkdir(parents=True, exist_ok=True)

        doc = pymupdf.open(str(pdf))
        try:
            before = {p: _norm(doc[p - 1].get_text("text")) for p in pages}
            doc.select([p - 1 for p in pages])
            doc.save(str(out))

            page_info = []
            ok_all = doc.page_count == len(pages)
            for i in range(doc.page_count):
                orig_no = original_page(bid, i)
                after = _norm(doc[i].get_text("text"))
                ok = after == before.get(orig_no, after)
                ok_all = ok_all and ok
                page_info.append({
                    "local_index": i,
                    "original_page": orig_no,
                    "chars": len(after),
                    "identical_to_original": ok,
                })
        finally:
            doc.close()

        report[bid] = {
            "pdf": str(pdf),
            "slice": str(out.relative_to(OUT_DIR)),
            "page_count": len(pages),
            "identical_to_original": ok_all,
            "pages": page_info,
        }
        flag = "OK" if ok_all else "DIVERGE"
        print(f"[{bid}] {out.name}: {len(pages)} pagine, testo {flag}")

    save_json(OUT_DIR / "slices" / "slices.json", report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
