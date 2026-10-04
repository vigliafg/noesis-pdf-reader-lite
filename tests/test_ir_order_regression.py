#!/usr/bin/env python3
"""Regressione d'ordine su una pagina a due colonne con tabelle affiancate.

Caso guida `corpus1/ha22.pdf` p1977 (page_ui 1977, idx 1976): due tabelle
**affiancate in colonne diverse** (TABLE 257-4 a sinistra, TABLE 257-5 a
destra) più prosa a due colonne. Prima del fix l'euristica `_multi_header_table`
fondeva le due tabelle e risucchiava il corpo pagina, interlacciando le colonne
riga per riga. Ora l'ordine deve essere **colonna-major**.

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

_PDF = _ROOT / "corpus1" / "ha22.pdf"
_PAGE = 1976

try:
    import pymupdf  # noqa: E402

    import ir_layout  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)


@unittest.skipUnless(_OK, "pymupdf/ir_layout non disponibili")
class TwoColumnTablesOrderTests(unittest.TestCase):
    def _md(self) -> str:
        if not _PDF.exists():
            self.skipTest("corpus1/ha22.pdf assente")
        doc = pymupdf.open(_PDF)
        try:
            chunk = ir_layout.page_chunk(doc, _PAGE)
            return ir_layout.build_markdown(doc[_PAGE], doc, _PAGE,
                                            embed_figures=False, chunk=chunk)
        finally:
            doc.close()

    def test_column_major_order(self):
        md = self._md()
        order = ["TABLE 257-4", "dyspnea, and dyspnea at rest",
                 "TABLE 257-5", "Symptoms of Reduced Perfusion",
                 "PHYSICAL EXAMINATION"]
        pos = [md.find(s) for s in order]
        for s, p in zip(order, pos):
            self.assertGreaterEqual(p, 0, f"contenuto mancante: {s}")
        self.assertEqual(pos, sorted(pos),
                         f"ordine non colonna-major: {list(zip(order, pos))}")

    def test_tables_not_interleaved(self):
        md = self._md()
        # il vecchio difetto fondeva le due tabelle: "Myocardial" (tabella
        # destra) incollato alla prosa sinistra.
        self.assertNotIn("Myocardial dyspnea", md)
        self.assertNotIn("CLASS LIMITATION CLINICAL ASSESSMENT   Excess", md)
        # la tabella destra sta dopo la prosa della colonna sinistra
        self.assertLess(md.find("TABLE 257-4"), md.find("TABLE 257-5"))


if __name__ == "__main__":
    unittest.main()
