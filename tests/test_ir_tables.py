#!/usr/bin/env python3
"""Regressione tabelle IR (Stadio C).

Caso guida `corpus2/fe22.pdf` p1101 (idx 1100): tabella a **4 colonne** con
celle lunghe su più righe. La content map del motore è **corretta**; la nostra
griglia a copertura parole invece **disallinea** le celle. Il selettore deve
quindi fidarsi della content map finché non è davvero rotta (recall < 0.90).

CI-safe: salta se il corpus è assente.
"""

from __future__ import annotations

import os
import sys
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


def _md(pdf: Path, idx: int) -> str:
    doc = pymupdf.open(pdf)
    try:
        chunk = ir_layout.page_chunk(doc, idx)
        return ir_layout.build_markdown(doc[idx], doc, idx,
                                        embed_figures=False, chunk=chunk)
    finally:
        doc.close()


@unittest.skipUnless(_OK, "pymupdf/ir_layout non disponibili")
class TableCellAlignmentTests(unittest.TestCase):
    _PDF = _ROOT / "corpus2" / "fe22.pdf"

    def test_four_columns_aligned(self):
        if not self._PDF.exists():
            self.skipTest("corpus2/fe22.pdf assente")
        md = _md(self._PDF, 1100)
        header = next((ln for ln in md.splitlines() if "Idarucizumab" in ln), None)
        self.assertIsNotNone(header, "intestazione tabella assente")
        cells = [c.strip() for c in header.strip().strip("|").split("|")]
        self.assertEqual(len(cells), 4, f"colonne errate: {cells}")
        self.assertTrue(any("Andexanet Alfa" in c for c in cells), cells)
        self.assertTrue(any("Ciraparantag" in c for c in cells), cells)
        # il vecchio difetto fondeva le due intestazioni in una cella
        self.assertFalse(any("Andexanet Alfa Ciraparantag" in c for c in cells))

    def test_long_cells_kept(self):
        if not self._PDF.exists():
            self.skipTest("corpus2/fe22.pdf assente")
        md = _md(self._PDF, 1100)
        self.assertIn("Recombinant human", md)
        self.assertIn("factor Xa variant", md)


@unittest.skipUnless(_OK, "pymupdf/ir_layout non disponibili")
class TableCellJunkTests(unittest.TestCase):
    """Artefatto di cella: ``<br>i`` da un carattere di controllo del font.

    `corpus2/ox16.pdf` p506 (idx 505): la content map appende ``<br>i`` a fine
    cella. Il token non è una parola della pagina → va rimosso.
    """

    _PDF = _ROOT / "corpus2" / "ox16.pdf"

    def test_control_char_artifact_removed(self):
        if not self._PDF.exists():
            self.skipTest("corpus2/ox16.pdf assente")
        md = _md(self._PDF, 505)
        self.assertNotIn("<br>i", md)
        # il contenuto reale della cella resta
        self.assertIn("To eliminate the cause", md)
        self.assertIn("probability of the issue (re) occurring", md)


if __name__ == "__main__":
    unittest.main()
