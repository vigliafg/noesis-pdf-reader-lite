"""Pack 7 — figure/flowchart embedding (D1 diagnosis + D2/D4 fixes).

Fix mirati:
1. ``_FIGURE_CAPTION_RE`` riconosce il prefisso "E-" delle appendici
   ("E-FIGURE 175-1").
2. la guardia di ``_link_figures`` accetta una lettera MAIUSCOLA dopo "fig."
   ("FIG. E3"), non una minuscola ("Figure shows that" no).
3. il match didascalia↔md è tollerante agli spazi rimossi dagli apici
   ("V PE" nella pagina vs "VPE" nel markdown).

I test sono sintetici (CI-safe); i gold su corpus1/corpus2 si saltano se assenti.
"""

import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pymupdf  # noqa: E402

import main  # noqa: E402

_CORPUS1 = os.path.join(_ROOT, "corpus1")
_CORPUS2 = os.path.join(_ROOT, "corpus2")


class CaptionRegexTests(unittest.TestCase):
    def test_e_figure_dash_prefix(self):
        self.assertTrue(main._FIGURE_CAPTION_RE.match(
            "E-FIGURE 175-1. Leptomeningeal metastases. A post-gadolinium"))

    def test_fig_letter_marker(self):
        self.assertTrue(main._FIGURE_CAPTION_RE.match(
            "FIG. E3 Injection for right tennis elbow"))

    def test_fig_dotted_number(self):
        self.assertTrue(main._FIGURE_CAPTION_RE.match(
            "Fig. 1.36 Importance of mammographic-sonographic correlation"))

    def test_body_sentence_is_not_caption(self):
        self.assertFalse(main._FIGURE_CAPTION_RE.match(
            "Figure shows that the data support the hypothesis"))


def _page_with_caption(caption: str):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 60, 40))
    pix.set_rect(pix.irect, (180, 180, 180))
    page.insert_image(pymupdf.Rect(50, 50, 250, 200), stream=pix.tobytes("png"))
    page.insert_text((50, 230), caption, fontsize=9)
    return doc, page


class LinkLetterCaptionTests(unittest.TestCase):
    def test_letter_marker_links_figure(self):
        caption = "FIG. E3 Injection for right tennis elbow (lateral epicondylar syndrome)"
        doc, page = _page_with_caption(caption)
        md = f"Some body text.\n\n{caption}\n\nMore text.\n"
        with tempfile.TemporaryDirectory() as td:
            out = main._link_figures(md, page, td, 0)
        self.assertIn("![", out)

    def test_e_figure_prefix_links_figure(self):
        caption = "E-FIGURE 175-1. Leptomeningeal metastases."
        doc, page = _page_with_caption(caption)
        md = f"Intro text.\n\n**{caption}**\n"
        with tempfile.TemporaryDirectory() as td:
            out = main._link_figures(md, page, td, 0)
        self.assertIn("![", out)

    def test_body_sentence_does_not_trigger_scan(self):
        md = "Figure shows that the data support the hypothesis.\n"
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        with tempfile.TemporaryDirectory() as td:
            out = main._link_figures(md, page, td, 0)
        self.assertNotIn("![", out)


def _corpus(name, pdf):
    p = os.path.join(name, f"{pdf}.pdf")
    return p if os.path.isfile(p) else None


def _engine_linked(pdf, pg):
    with tempfile.TemporaryDirectory() as figdir:
        with pymupdf.open(pdf) as doc:
            raw = main._extract_pymupdf4llm(pdf, pg, ocr_language="eng")
            out, _ = main._apply_engine_on_page(
                doc[pg], raw, figures_dir=figdir, page_num=pg)
        return out


@unittest.skipUnless(_corpus(_CORPUS1, "ce24"), "corpus1/ce24.pdf assente")
class GoldCecilEFigures(unittest.TestCase):
    def test_e_figure_page_links_image(self):
        out = _engine_linked(_corpus(_CORPUS1, "ce24"), 2003)
        self.assertIn("![", out)


@unittest.skipUnless(_corpus(_CORPUS2, "fe22"), "corpus2/fe22.pdf assente")
class GoldFerriLetterFigures(unittest.TestCase):
    def test_fig_letter_page_links_image(self):
        out = _engine_linked(_corpus(_CORPUS2, "fe22"), 1386)
        self.assertIn("![", out)


if __name__ == "__main__":
    unittest.main()
