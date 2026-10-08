#!/usr/bin/env python3
"""Test CI-safe dei detector (nuove tipologie, HANDOFF §7).

Coprono i segnali **puri** aggiunti per allargare la copertura:
- figura **bianca/piatta** (artefatto di rendering, non "tiny");
- struttura tabella (righe con conteggio celle diverso dall'header);
- duplicazione di righe lunghe.

Nessun corpus, nessuna rete.
"""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "tools")
for _p in (_ROOT, _TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import pymupdf  # noqa: E402

    import detectors  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)


def _img(kind: str) -> bytes:
    """PNG di una pagina di test: 'white' (piatta) o 'stripes' (con contenuto)."""
    doc = pymupdf.open()
    p = doc.new_page(width=100, height=100)
    if kind == "white":
        p.draw_rect(pymupdf.Rect(0, 0, 100, 100), color=(1, 1, 1),
                    fill=(1, 1, 1))
    else:
        for i in range(0, 100, 10):
            p.draw_line((i, 0), (i, 100), color=(0, 0, 0), width=2)
    pix = p.get_pixmap()
    data = pix.tobytes("png")
    doc.close()
    return data


@unittest.skipUnless(_OK, "detectors/pymupdf non disponibili")
class ImageStatsTests(unittest.TestCase):
    def test_white_is_blank(self):
        s = detectors.image_stats(_img("white"))
        self.assertIsNotNone(s)
        self.assertTrue(s["blank"])
        self.assertLess(s["std"], detectors.IMG_BLANK_STD)

    def test_stripes_not_blank(self):
        s = detectors.image_stats(_img("stripes"))
        self.assertIsNotNone(s)
        self.assertFalse(s["blank"])
        self.assertGreater(s["std"], detectors.IMG_BLANK_STD)

    def test_undecodable_returns_none(self):
        self.assertIsNone(detectors.image_stats(b"not an image at all"))

    def test_figure_render_counts_blanks(self):
        rep = detectors.figure_render([_img("white"), _img("stripes"),
                                       _img("white")])
        self.assertEqual(rep["n"], 3)
        self.assertEqual(rep["blank"], 2)
        self.assertEqual(rep["blank_idx"], [0, 2])


class DuplicateTests(unittest.TestCase):
    def test_no_duplicates(self):
        md = "una riga lunga abbastanza da contare come parola\naltra riga qui"
        self.assertEqual(detectors.duplicate_lines(md), 0)

    def test_repeated_long_line(self):
        ln = " ".join(f"w{i}" for i in range(10))
        md = f"{ln}\n{ln}\n{ln}"
        self.assertEqual(detectors.duplicate_lines(md), 2)

    def test_short_lines_ignored(self):
        self.assertEqual(detectors.duplicate_lines("a b\n a b\n a b"), 0)


class TableMisalignTests(unittest.TestCase):
    def test_ok_table(self):
        md = "|a|b|\n|---|---|\n|c|d|\n"
        self.assertEqual(detectors.table_misalign(md), 0)

    def test_misaligned_row(self):
        md = "|a|b|\n|---|---|\n|c|\n"
        self.assertEqual(detectors.table_misalign(md), 1)


if __name__ == "__main__":
    unittest.main()
