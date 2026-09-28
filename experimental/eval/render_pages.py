#!/usr/bin/env python3
"""Renderizza le pagine dei bunch in PNG per la revisione visiva (arbitro).

- pagina intera a 150 DPI → ``out/<bunch>/pages/pXXXX.png``
- tabelle e figure in ritaglio a 300 DPI → ``out/<bunch>/crops/pXXXX_{table,fig}N.png``
"""

from __future__ import annotations

import sys

import pymupdf

from evalutil import (
    bunch_pages,
    crops_dir,
    items,
    original_page,
    pages_dir,
    save_json,
    slice_path,
    OUT_DIR,
)

PAGE_DPI = 150
CROP_DPI = 300


def _matrix(dpi: int) -> pymupdf.Matrix:
    z = dpi / 72.0
    return pymupdf.Matrix(z, z)


def main() -> int:
    manifest: dict[str, dict] = {}
    for bunch in items():
        bid = bunch["id"]
        pdf = slice_path(bid)
        if not pdf.is_file():
            print(f"ERRORE: manca {pdf}; esegui prima make_corpus.py + make_slices.py")
            return 1

        pages_dir(bid).mkdir(parents=True, exist_ok=True)
        crops_dir(bid).mkdir(parents=True, exist_ok=True)
        manifest[bid] = {}

        with pymupdf.open(str(pdf)) as doc:
            for i in range(doc.page_count):
                orig = original_page(bid, i)
                page = doc[i]
                stem = f"p{orig}"

                png = pages_dir(bid) / f"{stem}.png"
                page.get_pixmap(matrix=_matrix(PAGE_DPI)).save(str(png))

                crops: list[str] = []
                try:
                    infos = page.get_image_info()
                except Exception:
                    infos = []
                for n, info in enumerate(infos, 1):
                    bbox = pymupdf.Rect(info.get("bbox", ()))
                    if bbox.is_empty:
                        continue
                    out = crops_dir(bid) / f"{stem}_fig{n}.png"
                    page.get_pixmap(matrix=_matrix(CROP_DPI), clip=bbox).save(str(out))
                    crops.append(out.name)

                try:
                    tables = page.find_tables().tables
                except Exception:
                    tables = []
                for n, table in enumerate(tables, 1):
                    bbox = pymupdf.Rect(table.bbox)
                    if bbox.is_empty:
                        continue
                    out = crops_dir(bid) / f"{stem}_table{n}.png"
                    page.get_pixmap(matrix=_matrix(CROP_DPI), clip=bbox).save(str(out))
                    crops.append(out.name)

                manifest[bid][str(orig)] = {
                    "page_png": f"{bid}/pages/{stem}.png",
                    "crops": crops,
                    "n_images": len(infos),
                    "n_tables": len(tables),
                }
                print(f"[{bid}] p{orig}: render + {len(crops)} crop")

    save_json(OUT_DIR / "render_manifest.json", manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
