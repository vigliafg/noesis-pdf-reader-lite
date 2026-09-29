"""Pack 3 — figure fixes.

``uncomment_picture_text`` turns the ``<!-- Start of picture text --> … <!--
End of picture text -->`` HTML comment (which the Markdown renderer hides) into
a visible blockquote, so map/chart labels survive.

``link_figures`` (``![figura](…)`` placeholders from ``get_image_info()``) is
NOT implemented yet: on the corpus pages that actually carry figure text
(ha22/p1230, pa23/p602) ``page.get_image_info()`` returns no images at all (the
figures are vector drawings, and the text comes from pymupdf4llm's own OCR), so
the feature cannot be validated where it was meant to help; and emitting a link
requires deciding the file name/URI and wiring it into the image gallery of the
UI. Deferred to a dedicated change.
"""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from layout_engine import _uncomment_picture_text_md  # noqa: E402

_GOLD_DIR = os.environ.get(
    "NOESIS_GOLD_PDF_DIR",
    os.path.normpath(os.path.join(_ROOT, "..", "noesis-pdf-cloner-service", "pdfs")),
)


def _gold_pdf(name):
    for cand in (os.path.join(_ROOT, name), os.path.join(_GOLD_DIR, name)):
        if os.path.exists(cand):
            return cand
    return None


class UncommentPictureTextTests(unittest.TestCase):
    def test_comment_becomes_visible_blockquote(self):
        md = (
            "body\n\n"
            "<!-- Start of picture text -->\nLegend text here<br>1.0 1.3\n"
            "<!-- End of picture text -->\n\n**FIGURE 1** caption"
        )
        out = _uncomment_picture_text_md(md)
        self.assertNotIn("<!--", out)
        self.assertIn("> Legend text here<br>1.0 1.3", out)
        self.assertIn("**FIGURE 1** caption", out)

    def test_multiline_body_each_line_quoted(self):
        md = "<!-- Start of picture text -->\nline one\nline two\n<!-- End of picture text -->"
        out = _uncomment_picture_text_md(md)
        self.assertEqual(out, "> line one\n> line two")

    def test_empty_body_is_removed(self):
        md = "a\n\n<!-- Start of picture text -->\n<!-- End of picture text -->\n\nb"
        out = _uncomment_picture_text_md(md)
        self.assertNotIn("picture text", out)
        self.assertIn("a", out)
        self.assertIn("b", out)

    def test_without_markers_unchanged(self):
        md = "just body text, no picture markers"
        self.assertEqual(_uncomment_picture_text_md(md), md)

    def test_idempotent(self):
        md = "<!-- Start of picture text -->\nx\n<!-- End of picture text -->"
        once = _uncomment_picture_text_md(md)
        self.assertEqual(_uncomment_picture_text_md(once), once)


def _engine(pdf, page_no):
    import pymupdf
    import pymupdf4llm

    import main

    raw = pymupdf4llm.to_markdown(pdf, pages=[page_no - 1])
    with pymupdf.open(pdf) as doc:
        out, _ = main._apply_engine_on_page(doc[page_no - 1], raw)
    return raw, out


@unittest.skipUnless(_gold_pdf("ha22.pdf"), "ha22.pdf non presente (gold figure)")
class GoldPictureTextHa22Tests(unittest.TestCase):
    def test_map_legend_text_is_visible(self):
        raw, out = _engine(_gold_pdf("ha22.pdf"), 1230)
        self.assertIn("<!-- Start of picture text -->", raw)  # sanity
        self.assertNotIn("<!--", out)
        self.assertIn("Presence of rheumatic heart disease", out)


@unittest.skipUnless(_gold_pdf("pa23.pdf"), "pa23.pdf non presente (gold figure)")
class GoldPictureTextPa23Tests(unittest.TestCase):
    def test_chart_labels_are_visible(self):
        raw, out = _engine(_gold_pdf("pa23.pdf"), 602)
        self.assertIn("<!-- Start of picture text -->", raw)
        self.assertNotIn("<!--", out)
        self.assertIn("INCUBATION", out)


if __name__ == "__main__":
    unittest.main()
