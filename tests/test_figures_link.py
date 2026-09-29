"""Figure linking (Pack 3b): a figure as "body image + caption".

PyMuPDF4LLM does not emit the figure body (only the text inside it), so a
figure ends up as loose pieces. ``_figure_regions`` finds the graphic region
above a ``FIGURE n`` caption (vector clusters + embedded images) and
``_link_figures`` renders it to a PNG and links it next to the caption.

Unit tests are synthetic (CI-safe); the real-corpus checks skip without PDFs.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "tests"))

import pymupdf  # noqa: E402

from main import _figure_regions, _link_figures  # noqa: E402

from test_fixes import _new_page  # noqa: E402

_GOLD_DIR = os.environ.get(
    "NOESIS_GOLD_PDF_DIR",
    os.path.normpath(os.path.join(_ROOT, "..", "noesis-pdf-cloner-service", "pdfs")),
)


def _gold_pdf(name):
    for cand in (os.path.join(_ROOT, name), os.path.join(_GOLD_DIR, name)):
        if os.path.exists(cand):
            return cand
    return None


def _page_with_figure():
    doc, page = _new_page()
    # A chart-like drawing above the caption.
    page.draw_rect(pymupdf.Rect(60, 60, 550, 260), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    for i in range(5):
        page.draw_rect(
            pymupdf.Rect(80 + i * 90, 150, 130 + i * 90, 250),
            color=(0, 0, 0), fill=(0.4, 0.6, 0.8),
        )
    page.insert_textbox(pymupdf.Rect(60, 270, 550, 300), "FIGURE 1 A bar chart.", fontsize=9)
    page.insert_textbox(
        pymupdf.Rect(60, 320, 550, 400), "Body text after the figure.", fontsize=10
    )
    return doc, page


class FigureRegionTests(unittest.TestCase):
    def test_region_detected_above_caption(self):
        doc, page = _page_with_figure()
        regs = _figure_regions(page)
        self.assertTrue(regs)
        r = regs[0]["rect"]
        self.assertLess(r[1], 260)  # above the caption
        self.assertGreater(r[3] - r[1], 50)
        doc.close()

    def test_no_caption_no_region(self):
        doc, page = _new_page()
        page.draw_rect(pymupdf.Rect(60, 60, 550, 260), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        page.insert_textbox(pymupdf.Rect(60, 270, 550, 300), "Just some text.", fontsize=9)
        self.assertEqual(_figure_regions(page), [])
        doc.close()

    def test_numberless_caption_is_detected(self):
        # "Figure: …" / "Figure A …" are captions too, but a body sentence
        # ("Figure shows that …") is not.
        doc, page = _new_page()
        page.draw_rect(pymupdf.Rect(60, 60, 550, 260), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
        for i in range(4):
            page.draw_rect(
                pymupdf.Rect(80 + i * 100, 150, 140 + i * 100, 250),
                color=(0, 0, 0), fill=(0.4, 0.6, 0.8),
            )
        page.insert_textbox(
            pymupdf.Rect(60, 270, 550, 300), "Figure: a schematic diagram.", fontsize=9
        )
        self.assertTrue(_figure_regions(page))
        doc.close()


class LinkFiguresTests(unittest.TestCase):
    def test_image_inserted_before_caption_and_png_saved(self):
        doc, page = _page_with_figure()
        with tempfile.TemporaryDirectory() as d:
            md = "Intro text.\n\nFIGURE 1 A bar chart.\n\nBody text after the figure."
            out = _link_figures(md, page, Path(d), 0)
            saved = sorted(Path(d).glob("*.png"))
            size = saved[0].stat().st_size if saved else 0
        self.assertIn("![figura 1](", out)
        self.assertLess(out.index("![figura 1]"), out.index("FIGURE 1"))
        self.assertEqual(len(saved), 1)
        # a valid, non-empty PNG
        self.assertGreater(size, 100)
        doc.close()

    def test_no_caption_returns_unchanged(self):
        doc, page = _page_with_figure()
        with tempfile.TemporaryDirectory() as d:
            md = "Intro text.\n\nNo figure caption here."
            self.assertEqual(_link_figures(md, page, Path(d), 0), md)
        doc.close()


def _engine(pdf, page_no, dest):
    import pymupdf4llm

    import main

    raw = pymupdf4llm.to_markdown(pdf, pages=[page_no - 1])
    with pymupdf.open(pdf) as doc:
        return main._apply_engine_on_page(
            doc[page_no - 1], raw, figures_dir=dest, page_num=page_no - 1
        )


@unittest.skipUnless(_gold_pdf("pa23.pdf"), "pa23.pdf non presente (gold figure)")
class GoldFigurePa23Tests(unittest.TestCase):
    def test_chart_linked_and_legend_not_duplicated(self):
        with tempfile.TemporaryDirectory() as d:
            out, _ = _engine(_gold_pdf("pa23.pdf"), 602, Path(d))
            self.assertRegex(out, r"!\[figura 1\]\(file://")
            self.assertIn("FIG. 14.10", out)
            # legend was inside the rendered region → removed, not duplicated
            self.assertNotIn("> INCUBATION", out)
            self.assertEqual(len(list(Path(d).glob("*.png"))), 1)


@unittest.skipUnless(_gold_pdf("ha22.pdf"), "ha22.pdf non presente (gold figure)")
class GoldFigureHa22Tests(unittest.TestCase):
    def test_map_linked_with_caption(self):
        with tempfile.TemporaryDirectory() as d:
            out, _ = _engine(_gold_pdf("ha22.pdf"), 1230, Path(d))
            self.assertRegex(out, r"!\[figura 1\]\(file://")
            self.assertIn("FIGURE 148-1", out)
            self.assertEqual(len(list(Path(d).glob("*.png"))), 1)


if __name__ == "__main__":
    unittest.main()
