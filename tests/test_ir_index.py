#!/usr/bin/env python3
"""Regressione percorso **indici** IR (Stadio D.5 / Fase 3).

La content map di PyMuPDF4LLM **fonde** le voci d'indice in un unico paragrafo.
`ir_layout.index_markdown` ricostruisce la struttura riga-per-riga dalle righe
fisiche della pagina (colonne sx→dx, top→bottom), con lo stile e le continuazioni
di soli numeri di pagina riunite. Il gate `main._ir_gate` non forza più il
fallback a `current` sugli indici.

CI-safe: salta se il corpus è assente.
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
    import pymupdf  # noqa: E402

    import ir_layout  # noqa: E402
    import main  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)

_ENTRY = re.compile(r",\s*\d{1,4}(?:[–\-]\d{1,4})?[a-z]?\b")


@unittest.skipUnless(_OK, "pymupdf/ir_layout non disponibili")
class IndexPathTests(unittest.TestCase):
    _PDF = _ROOT / "corpus1" / "ha22.pdf"
    _IDX = 4076

    def _md(self):
        doc = pymupdf.open(self._PDF)
        try:
            chunk = ir_layout.page_chunk(doc, self._IDX)
            return ir_layout.build_markdown(doc[self._IDX], doc, self._IDX,
                                            embed_figures=False, chunk=chunk)
        finally:
            doc.close()

    def test_detected_as_index(self):
        if not self._PDF.exists():
            self.skipTest("corpus1/ha22.pdf assente")
        doc = pymupdf.open(self._PDF)
        try:
            _t, els = ir_layout.page_elements(doc, self._IDX)
            self.assertTrue(ir_layout._is_index_page(doc[self._IDX], els))
        finally:
            doc.close()

    def test_one_entry_per_line(self):
        if not self._PDF.exists():
            self.skipTest("corpus1/ha22.pdf assente")
        md = self._md()
        lines = [ln for ln in md.splitlines() if ln.strip()]
        # la content map fondeva ~185 voci in ~5 righe: ora sono quasi tutte
        # su righe separate
        self.assertGreater(len(lines), 100, f"solo {len(lines)} righe")
        entries = [ln for ln in lines if _ENTRY.search(ln)]
        self.assertGreater(len(entries), 120, f"solo {len(entries)} voci")
        # una voce nota deve stare su una riga propria
        self.assertIn("mitral regurgitation in, 3764", lines)
        self.assertIn("mumps in, 1617", lines)

    def test_columns_in_reading_order(self):
        if not self._PDF.exists():
            self.skipTest("corpus1/ha22.pdf assente")
        lines = [ln for ln in self._md().splitlines() if ln.strip()]
        # fine colonna 1 e inizio colonna 2 (dalla pagina reale)
        self.assertLess(lines.index("in heart failure, 1917, 1938"),
                        lines.index("in hypertrophic cardiomyopathy, 1917"))

    def test_gate_passes_index(self):
        if not self._PDF.exists():
            self.skipTest("corpus1/ha22.pdf assente")
        doc = pymupdf.open(self._PDF)
        try:
            chunk = ir_layout.page_chunk(doc, self._IDX)
            md = ir_layout.build_markdown(doc[self._IDX], doc, self._IDX,
                                          embed_figures=False, chunk=chunk)
            _t, els = ir_layout._elements_from_chunk(chunk)
            ok, reason = main._ir_gate(doc[self._IDX], md, els)
        finally:
            doc.close()
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
