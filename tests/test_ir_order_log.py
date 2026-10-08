#!/usr/bin/env python3
"""L'hook diagnostico ``order_log`` di ``build_markdown`` è **non distruttivo**.

Con ``order_log`` fornito il markdown emesso deve essere **identico** a quello
senza (l'hook registra solo l'ordine, non lo altera). CI-safe: salta se il
corpus è assente.
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
class OrderLogTests(unittest.TestCase):
    def test_order_log_nondestructive(self):
        if not _PDF.exists():
            self.skipTest("corpus1/ha22.pdf assente")
        doc = pymupdf.open(_PDF)
        try:
            chunk = ir_layout.page_chunk(doc, _PAGE)
            md_a = ir_layout.build_markdown(
                doc[_PAGE], doc, _PAGE, embed_figures=False, chunk=chunk)
            log: list = []
            md_b = ir_layout.build_markdown(
                doc[_PAGE], doc, _PAGE, embed_figures=False, chunk=chunk,
                order_log=log)
        finally:
            doc.close()
        self.assertEqual(md_a, md_b)  # l'hook non cambia l'output
        self.assertTrue(log)          # ...ma registra l'ordine
        self.assertTrue(all("class" in e and "bbox" in e for e in log))


if __name__ == "__main__":
    unittest.main()
