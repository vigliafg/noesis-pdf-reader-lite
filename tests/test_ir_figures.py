#!/usr/bin/env python3
"""Regressione ordine figure/didascalie IR (Stadio D).

Caso guida `corpus2/ox2.pdf` p384 (idx 383): due figure **impilate** (Fig. 6.7
sopra Fig. 6.8), ciascuna con didascalia e riga di credito. Il vecchio
`_figure_block` fondeva le due figure e metteva **tutte le immagini prima di
tutte le didascalie**, con i crediti prima delle didascalie.

Ora l'ordine deve essere: [IMG 6.7] → "Fig. 6.7" → credito p98 → [IMG 6.8] →
"Fig. 6.8". CI-safe: salta se il corpus è assente.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import pymupdf  # noqa: E402

    import ir_layout  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)


@unittest.skipUnless(_OK, "pymupdf/ir_layout non disponibili")
class StackedFiguresOrderTests(unittest.TestCase):
    _PDF = _ROOT / "corpus2" / "ox2.pdf"

    def _md(self) -> str:
        doc = pymupdf.open(self._PDF)
        try:
            chunk = ir_layout.page_chunk(doc, 383)
            with tempfile.TemporaryDirectory() as d:
                md = ir_layout.build_markdown(doc[383], doc, 383,
                                              figures_dir=Path(d),
                                              embed_figures=True, chunk=chunk)
            return re.sub(r"data:image/[^)]+", "[IMG]", md)
        finally:
            doc.close()

    def test_two_images_with_their_captions(self):
        if not self._PDF.exists():
            self.skipTest("corpus2/ox2.pdf assente")
        md = self._md()
        self.assertEqual(md.count("[IMG]"), 2, md[:400])
        i67 = md.find("Fig. 6.7")
        credit = md.find("p98")
        i68 = md.find("Fig. 6.8")
        self.assertGreaterEqual(i67, 0)
        self.assertGreaterEqual(credit, 0)
        self.assertGreaterEqual(i68, 0)
        first_img = md.find("[IMG]")
        second_img = md.find("[IMG]", first_img + 1)
        # [IMG 6.7] < Fig 6.7 < credito p98 < [IMG 6.8] < Fig 6.8
        self.assertLess(first_img, i67)
        self.assertLess(i67, credit)
        self.assertLess(credit, second_img)
        self.assertLess(second_img, i68)


@unittest.skipUnless(_OK, "pymupdf/ir_layout non disponibili")
class FooterCaptionRecoveryTests(unittest.TestCase):
    _PDF = _ROOT / "corpus1" / "ce24.pdf"

    def test_footer_figure_caption_recovered(self):
        if not self._PDF.exists():
            self.skipTest("corpus1/ce24.pdf assente")
        doc = pymupdf.open(self._PDF)
        try:
            chunk = ir_layout.page_chunk(doc, 1774)
            md = ir_layout.build_markdown(doc[1774], doc, 1774,
                                          embed_figures=False, chunk=chunk)
        finally:
            doc.close()
        # FIGURE 154-1 era classificata `page-footer` e veniva scartata
        self.assertIn("FIGURE 154-1", md)
        self.assertIn("FIGURE 154-2", md)


if __name__ == "__main__":
    unittest.main()
