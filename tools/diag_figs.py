#!/usr/bin/env python3
"""D1 diagnosis (Pack 7): why is a figure not embedded on this page?

Usage:
    .venv/bin/python tools/diag_figs.py <pdf> <page0> [<page0> ...]

For each page it prints, in order:
  * page size / rotation / image count
  * every figure candidate from main._figure_candidates (cluster_drawings + images)
  * every text block whose text matches main._FIGURE_CAPTION_RE (the captions
    the engine uses to anchor a figure region), with its rect
  * the resulting main._figure_regions
  * the figure links / caption lines actually present in the raw markdown
This separates "caption not recognised" from "graphic region not found".
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pymupdf  # noqa: E402

import main  # noqa: E402


def _txt(blk) -> str:
    t = " ".join(s["text"] for line in blk["lines"] for s in line)
    return re.sub(r"\s+", " ", t).strip()


def diag(path: str, page_num: int) -> None:
    print("=" * 78)
    print(f"{os.path.basename(path)}  page {page_num}")
    with pymupdf.open(path) as doc:
        page = doc[page_num]
        print(f"  rect={tuple(round(v, 1) for v in page.rect)}  "
              f"rotation={page.rotation}  images={len(page.get_images(full=True))}")

        imgs = page.get_image_info()
        print(f"  -- get_image_info(): {len(imgs)}")
        for info in imgs[:8]:
            b = info.get("bbox")
            print(f"       bbox={tuple(round(v, 1) for v in b) if b else None}")

        try:
            clusters = page.cluster_drawings()
        except Exception as e:  # noqa: BLE001
            clusters = []
            print(f"  cluster_drawings errore: {e}")
        print(f"  -- cluster_drawings(): {len(clusters)}")
        for r in clusters[:12]:
            print(f"       rect={tuple(round(v, 1) for v in r)}")

        cands = main._figure_candidates(page)
        print(f"  -- _figure_candidates() kept: {len(cands)}")
        for c in cands[:12]:
            w, h = c[2] - c[0], c[3] - c[1]
            print(f"       w={w:6.1f} h={h:6.1f}  {tuple(round(v, 1) for v in c)}")

        print("  -- text blocks matching _FIGURE_CAPTION_RE")
        for blk in main._collect_blocks(page):
            t = _txt(blk)
            if main._FIGURE_CAPTION_RE.match(t):
                print(f"       MATCH  {tuple(round(v, 1) for v in (blk['x0'], blk['y0'], blk['x1'], blk['y1']))}  {t[:70]!r}")
        # also show caption-like blocks the regex *rejects* (candidates for D2)
        shows = 0
        for blk in main._collect_blocks(page):
            t = _txt(blk)
            if (shows < 6 and re.match(r"^\s*(?:e[-\s]?)?fig", t, re.I)
                    and not main._FIGURE_CAPTION_RE.match(t)):
                print(f"       NO-MATCH {t[:70]!r}")
                shows += 1

        regions = main._figure_regions(page)
        print(f"  -- _figure_regions(): {len(regions)}")
        for f in regions:
            print(f"       rect={tuple(round(v, 1) for v in f['rect'])}  cap={f['caption'][:50]!r}")

        raw = main._extract_pymupdf4llm(path, page_num, ocr_language="eng") or ""
        caps_in_md = [ln for ln in raw.splitlines()
                      if re.search(r"(?i)\bfig(?:ure)?\.?\s*(?:\d|[:\-–—]|[A-Z])", ln)]
        print(f"  -- raw md: {len(re.findall(r'!\[', raw))} figure link(s)")
        for c in caps_in_md[:6]:
            print(f"       md-cap {c.strip()[:70]!r}")
        # Guard of _link_figures: does it even enter the linking path?
        guard = re.search(r"(?im)^.{0,80}?\bfig(?:ure)?\.?\s*(?:\d|[:\-–—])", raw)
        print(f"  -- _link_figures guard pass: {bool(guard)}")
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            linked = main._link_figures(raw, page, td, page_num)
        print(f"  -- after _link_figures: {len(re.findall(r'!\[', linked))} figure link(s)")


def main_cli(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    path = argv[1]
    for p in argv[2:]:
        diag(path, int(p))
    return 0


if __name__ == "__main__":
    sys.exit(main_cli(sys.argv))
