"""Pack 9 — colonne e testo-dentro-le-figure.

Difetto E2E (ha22, pagina UI 101 / stampata 60): pagina a due colonne con una
figura a tutta larghezza in alto (FIG. 10-1) più una figura a fine colonna 1
(FIG. 10-2). Il testo **dentro** le figure (etichette degli assi) è stretto e
non sta in una tabella: faceva da "ponte" tra le colonne, il rilevatore
collassava la pagina a una colonna e l'ordine di lettura restava intrecciato
(la colonna 2 sembrava "mancare").

Fix: ``figure_text_regions`` (layout_engine) + esclusione di quel testo dal
rilevamento colonne in ``profile_page``/``_column_aware_markdown``. Il testo
resta però nell'output (le legende delle mappe sono contenuto — gold Pack 3 su
ha22/p1230).

Sintetico (CI-safe) + gold su corpus1/ha22.pdf (skip se assente).
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import main  # noqa: E402
import layout_engine as L  # noqa: E402

_HA22 = Path(_ROOT) / "corpus1" / "ha22.pdf"


def _blk(x0, y0, x1, y1, size, text):
    span = {"text": text, "size": size, "bold": False, "italic": False,
            "x0": x0, "x1": x1}
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "max_size": size,
            "lines": [[span]]}


class _FakePage:
    rect = type("R", (), {"width": 612.0, "height": 792.0})()


class ColumnBridgeTests(unittest.TestCase):
    """Il testo-figura non deve collassare una pagina a due colonne."""

    def setUp(self):
        # riga-ponte (etichette assi) larga quanto le due colonne, poi le colonne
        self.blocks = [
            _blk(90, 142, 330, 152, 8.0, "1985 1995 2005 Year"),  # riga-ponte
            _blk(40, 220, 240, 300, 10.0, "Left column text with words."),
            _blk(330, 220, 530, 300, 10.0, "Right column text with words."),
            _blk(40, 320, 240, 400, 10.0, "Left column second paragraph."),
            _blk(330, 320, 530, 400, 10.0, "Right column second paragraph."),
        ]
        self._orig_collect = main._collect_blocks
        self._orig_figs = main._figure_regions
        main._collect_blocks = lambda page, exclude=(): list(self.blocks)
        self.addCleanup(self._restore)

    def _restore(self):
        main._collect_blocks = self._orig_collect
        main._figure_regions = self._orig_figs

    def test_bridge_collapses_to_one_column(self):
        main._figure_regions = lambda *a, **k: []
        self.assertEqual(L.profile_page(_FakePage()).columns, 1)

    def test_figure_text_excluded_restores_two_columns(self):
        main._figure_regions = lambda *a, **k: [
            {"rect": (80, 130, 340, 160), "caption": "FIGURE 1 x",
             "caption_rect": (80, 130, 340, 160)}
        ]
        self.assertEqual(L.profile_page(_FakePage()).columns, 2)

    def test_plain_body_is_not_excluded(self):
        # Nessuna figura: i blocchi di corpo non vengono esclusi.
        main._figure_regions = lambda *a, **k: []
        self.assertEqual(L.figure_text_regions(_FakePage()), [])


@unittest.skipUnless(_HA22.is_file(), "corpus1/ha22.pdf assente (gold)")
class GoldTwoColumnFigureTests(unittest.TestCase):
    def test_column2_present_and_ordered(self):
        import tempfile

        import pymupdf

        path, idx = str(_HA22), 100  # pagina UI 101 = stampata 60
        raw = main._extract_pymupdf4llm(path, idx, "eng")
        with tempfile.TemporaryDirectory() as d:
            with pymupdf.open(path) as doc:
                out, _ = main._apply_engine_on_page(
                    doc[idx], raw, figures_dir=Path(d), page_num=idx,
                    figure_mode="embed")
        s = re.sub(r"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+", "", out)
        # la colonna 2 c'è (prima sembrava mancare)
        self.assertIn("the health care system", s)
        self.assertIn("ROOT CAUSES OF DISPARITIES", s)
        # ordine: colonna 1 prima della colonna 2
        self.assertLess(s.find("race/ethnicity, education"),
                        s.find("the health care system"))
        self.assertLess(s.find("In addition to racial"),
                        s.find("the health care system"))
        # entrambe le figure embedded
        self.assertEqual(out.count("data:image/jpeg;base64,"), 2)


if __name__ == "__main__":
    unittest.main()
