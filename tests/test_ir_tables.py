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


@unittest.skipUnless(_OK, "pymupdf/ir_layout non disponibili")
class SideBySideTableSplitTests(unittest.TestCase):
    """Due tabelle **affiancate** fuse in un'unica griglia → separate.

    `corpus2/fe22.pdf` p2685 (idx 2684): la content map fonde TABLE 2 e TABLE 4;
    la griglia a copertura è pulita ma unica → si taglia al secondo marker.
    """

    _PDF = _ROOT / "corpus2" / "fe22.pdf"

    def test_split_into_two_tables(self):
        if not self._PDF.exists():
            self.skipTest("corpus2/fe22.pdf assente")
        md = _md(self._PDF, 2684)
        import re as _re
        blocks = [b for b in _re.split(r"\n\s*\n", md)
                  if b.strip().startswith("|")]
        headers = [b.splitlines()[0] for b in blocks]
        t2 = next((h for h in headers if "TABLE 2" in h), None)
        t4 = next((h for h in headers if "TABLE 4" in h), None)
        self.assertIsNotNone(t2, headers)
        self.assertIsNotNone(t4, headers)
        # le due intestazioni non devono stare nella stessa riga-tabella
        self.assertNotIn("TABLE 4", t2)
        self.assertNotIn("TABLE 2", t4)


@unittest.skipUnless(_OK, "pymupdf/ir_layout non disponibili")
class RotatedTableTests(unittest.TestCase):
    """Tabella **ruotata** 90°: segnalata dal gate e resa come immagine.

    `corpus2/fe23.pdf` p342 (idx 341): la tabella ha testo verticale
    (`dir` non orizzontale) → non linearizzabile. Il gate la segnala
    ("tabella-ruotata") ma l'IR la rende come immagine, quindi la pipeline
    completa non ricade su `current`.
    """

    _PDF = _ROOT / "corpus2" / "fe23.pdf"
    _IDX = 341

    def test_gate_flags_rotated_table(self):
        if not self._PDF.exists():
            self.skipTest("corpus2/fe23.pdf assente")
        import pymupdf
        import main
        doc = pymupdf.open(self._PDF)
        try:
            _t, els = ir_layout._elements_from_chunk(
                ir_layout.page_chunk(doc, self._IDX))
            self.assertTrue(main._rotated_table_rects(doc[self._IDX], els))
            ok, reason = main._ir_gate(doc[self._IDX], "x", els)
            self.assertFalse(ok)
            self.assertEqual(reason, "tabella-ruotata")
        finally:
            doc.close()

    def test_pipeline_handles_rotated_table(self):
        if not self._PDF.exists():
            self.skipTest("corpus2/fe23.pdf assente")
        import tempfile
        import main
        with tempfile.TemporaryDirectory() as d:
            md, _raw, gate = main._apply_ir_on_page(
                str(self._PDF), self._IDX, figures_dir=Path(d))
        self.assertTrue(gate)  # gestita, non fallback
        self.assertIn("data:image/jpeg", md)  # resa come immagine

    def test_normal_table_not_flagged_rotated(self):
        pdf = _ROOT / "corpus2" / "fe22.pdf"
        if not pdf.exists():
            self.skipTest("corpus2/fe22.pdf assente")
        import pymupdf
        import main
        doc = pymupdf.open(pdf)
        try:
            _t, els = ir_layout._elements_from_chunk(
                ir_layout.page_chunk(doc, 1100))
            self.assertEqual(main._rotated_table_rects(doc[1100], els), [])
        finally:
            doc.close()


if __name__ == "__main__":
    unittest.main()
