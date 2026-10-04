#!/usr/bin/env python3
"""Test del censimento rumore e delle pulizie cosmetiche (Stadio E.0).

CI-safe: i test sulle funzioni pure non richiedono il corpus; il test di
regressione su pagina reale salta se `corpus2/ox2.pdf` è assente.
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
    import layout_engine  # noqa: E402

    import ir_layout  # noqa: E402
    import noise_census  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)


@unittest.skipUnless(_OK, "dipendenze non disponibili")
class CountNoiseTests(unittest.TestCase):
    def test_orphan_bullet(self):
        md = "- voce uno\n\n-\n\n- voce due\n"
        nz = noise_census.count_noise(md)
        self.assertEqual(nz["orphan_bullet"], 1)

    def test_glued_digit_and_double_space(self):
        # digit **dentro** la parola (bleed); l'apice "Nifurtimox1" non è rumore
        nz = noise_census.count_noise("ther60apies and  more text")
        self.assertEqual(nz["glued_digit"], 1)
        self.assertEqual(nz["double_space"], 1)

    def test_superscript_not_counted(self):
        nz = noise_census.count_noise("Nifurtimox1 and Echinacea7 were given.")
        self.assertEqual(nz["glued_digit"], 0)

    def test_empty_cell_and_ragged(self):
        md = "| a | b |\n|---|---|\n| x |  |\n| y | z | extra |\n"
        nz = noise_census.count_noise(md)
        self.assertGreaterEqual(nz["empty_cell"], 1)
        self.assertEqual(nz["table_ragged"], 1)

    def test_clean_text_has_no_noise(self):
        nz = noise_census.count_noise("Una frase pulita, senza artefatti.\n")
        self.assertEqual(nz["total"], 0)


@unittest.skipUnless(_OK, "dipendenze non disponibili")
class CosmeticCleanupTests(unittest.TestCase):
    def test_drop_orphan_bullets(self):
        md = "- a\n\n-\n\n- b"
        out = layout_engine._drop_orphan_bullets(md)
        self.assertNotRegex(out, r"(?m)^\s*[-*+]\s*$")
        self.assertIn("- a", out)
        self.assertIn("- b", out)

    def test_expand_ligature_with_space(self):
        # la legatura "fi" (U+FB01) + spazio spurio
        self.assertEqual(ir_layout._expand_ligatures("De\ufb01 nition"), "Definition")

    def test_expand_ligature_does_not_touch_plain_ff(self):
        # "staff in" NON è una legatura: non va toccato
        self.assertEqual(ir_layout._expand_ligatures("staff in"), "staff in")


@unittest.skipUnless(_OK, "dipendenze non disponibili")
class RealPageCleanupTests(unittest.TestCase):
    _PDF = _ROOT / "corpus2" / "ox2.pdf"
    _TO = _ROOT / "corpus2" / "to22.pdf"

    def test_definition_and_no_orphan_bullets(self):
        if not self._PDF.exists():
            self.skipTest("corpus2/ox2.pdf assente")
        import pymupdf
        doc = pymupdf.open(self._PDF)
        try:
            chunk = ir_layout.page_chunk(doc, 583)
            md, _ = ir_layout.build_markdown(doc[583], doc, 583,
                                             embed_figures=False,
                                             return_meta=True, chunk=chunk)
        finally:
            doc.close()
        import main
        md = main._cosmetic_ir(md)
        self.assertIn("Definition", md)
        self.assertNotIn("Defi nition", md)
        self.assertNotRegex(md, r"(?m)^\s*[-*+]\s*$")

    def test_heading_split_word_fixed_from_page(self):
        # to22 p780: il motore spezza "Differences" in "Dif f erences"; il testo
        # di pagina è pulito → l'header viene ricostruito.
        if not self._TO.exists():
            self.skipTest("corpus2/to22.pdf assente")
        import pymupdf
        import main
        doc = pymupdf.open(self._TO)
        try:
            chunk = ir_layout.page_chunk(doc, 779)
            md, _ = ir_layout.build_markdown(doc[779], doc, 779,
                                             embed_figures=False,
                                             return_meta=True, chunk=chunk)
        finally:
            doc.close()
        md = main._cosmetic_ir(md)
        self.assertIn("Physical Differences", md)
        self.assertNotIn("Dif f erences", md)


if __name__ == "__main__":
    unittest.main()
