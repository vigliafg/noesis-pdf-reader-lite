"""Pipeline IR dietro flag: selezione mode + estrazione IR (content map).

La pipeline IR si attiva con ``NOESIS_PIPELINE=ir`` (default: ``current``). Qui
si verifica la selezione del mode e — su una pagina sintetica a due colonne —
che ``_apply_ir_on_page`` ricostruisca l'ordine colona-major.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import pymupdf  # noqa: E402

    import main  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)


@unittest.skipUnless(_OK, "pymupdf/main non disponibili")
class PipelineModeTests(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.pop("NOESIS_PIPELINE", None)

    def tearDown(self):
        if self._old is not None:
            os.environ["NOESIS_PIPELINE"] = self._old
        else:
            os.environ.pop("NOESIS_PIPELINE", None)

    def test_default_is_current(self):
        self.assertEqual(main._pipeline_mode(), "current")

    def test_env_selects_ir(self):
        os.environ["NOESIS_PIPELINE"] = "ir"
        self.assertEqual(main._pipeline_mode(), "ir")

    def test_unknown_falls_back_to_current(self):
        os.environ["NOESIS_PIPELINE"] = "boh"
        self.assertEqual(main._pipeline_mode(), "current")


@unittest.skipUnless(_OK, "pymupdf/main non disponibili")
class IrPipelineTests(unittest.TestCase):
    def _two_col_pdf(self, path):
        doc = pymupdf.open()
        p = doc.new_page(width=612, height=792)
        p.insert_text((72, 60), "PAGETITLE", fontsize=16)
        for i in range(8):
            y = 120 + i * 30
            p.insert_text((60, y), f"Left column line {i} with several words.", fontsize=10)
            p.insert_text((330, y), f"Right column line {i} with several words.", fontsize=10)
        doc.save(str(path))
        doc.close()

    def test_ir_builds_both_columns(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "p.pdf"
            self._two_col_pdf(pdf)
            md, _raw = main._apply_ir_on_page(str(pdf), 0)
        self.assertIn("Left column line", md)
        self.assertIn("Right column line", md)


if __name__ == "__main__":
    unittest.main()
