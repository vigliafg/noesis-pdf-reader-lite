#!/usr/bin/env python3
"""Regressione: pannello-immagine (raster) classificato `table` dalla content map.

Caso guida `corpus1/ha22.pdf` p3355 (idx 3354): la figura EEG (immagine raster) è
resa come immagine, ma PyMuPDF4LLM la classificava anche `table` producendo una
tabella di **OCR-spazzatura** (`| ti eee | yarUte |`). La pipeline IR ora la
rimuove (``_raster_figure_table_rects`` + ``_strip_table_blocks``): niente doppio
testo+immagine.

Contro-caso `corpus2/fe22.pdf` p207 (idx 206): una **tabella vettoriale** reale
dentro una regione-figura (FIG. E2) **non** va rimossa (non è raster).

CI-safe: i test sul corpus si saltano se il PDF è assente.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import main  # noqa: E402
    from main import _strip_table_blocks  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)


@unittest.skipUnless(_OK, "main non importabile")
class StripTableBlocksUnitTests(unittest.TestCase):
    """Logica pura di ``_strip_table_blocks`` (nessun PDF)."""

    def test_removes_matching_block(self):
        el = {"class": "table", "bbox": (0, 0, 10, 10),
              "text": "|A|B|\n|1|2|"}
        md = "| A | B |\n|---|---|\n| 1 | 2 |\n\ntesto dopo"
        out = _strip_table_blocks(md, [el], [(0, 0, 10, 10)])
        self.assertNotRegex(out, r"^\s*\|", re.M)
        self.assertIn("testo dopo", out)

    def test_keeps_non_matching_block(self):
        el = {"class": "table", "bbox": (0, 0, 10, 10),
              "text": "|A|B|\n|1|2|"}
        md = "| X | Y |\n|---|---|\n| 7 | 8 |\n\ntesto"
        out = _strip_table_blocks(md, [el], [(0, 0, 10, 10)])
        self.assertRegex(out, r"^\s*\|", re.M)
        self.assertIn("7", out)

    def test_no_rects_noop(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        el = {"class": "table", "bbox": (0, 0, 10, 10), "text": "|A|B|"}
        self.assertEqual(_strip_table_blocks(md, [el], []), md)


@unittest.skipUnless(_OK, "main non importabile")
class RasterFigureTableCorpusTests(unittest.TestCase):
    _HA22 = _ROOT / "corpus1" / "ha22.pdf"
    _FE22 = _ROOT / "corpus2" / "fe22.pdf"

    def _md(self, pdf: Path, idx: int) -> str:
        md, _raw, _gate, _els, _reason = main._apply_ir_on_page_full(str(pdf), idx)
        return md

    def test_ha22_p3355_junk_table_removed(self):
        if not self._HA22.exists():
            self.skipTest("corpus1/ha22.pdf assente")
        md = self._md(self._HA22, 3354)
        # la tabella-spazzatura è tolta, ma l'immagine della figura resta
        self.assertNotRegex(md, r"^\s*\|", re.M)
        self.assertNotIn("yarUte", md)
        self.assertIn("data:image", md)
        self.assertIn("FIGURE 425-3", md)

    def test_fe22_p207_real_table_kept(self):
        if not self._FE22.exists():
            self.skipTest("corpus2/fe22.pdf assente")
        md = self._md(self._FE22, 206)
        # tabella vettoriale reale: NON deve essere rimossa
        self.assertRegex(md, r"^\s*\|", re.M)
        self.assertIn("Bacterial", md)


if __name__ == "__main__":
    unittest.main()
