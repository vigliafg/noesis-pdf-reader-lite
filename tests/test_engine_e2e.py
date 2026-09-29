"""CI-safe end-to-end test of the production extraction path (T2.2).

The gold tests in ``test_text_spacing.py`` run on the real corpus and **skip**
when those PDFs are absent — so in CI nothing covers the
``pymupdf4llm.to_markdown`` → ``layout_engine`` chain that the application
actually uses. This module closes that hole with a **generated** two-column
page: no external file, only ``pymupdf`` + ``pymupdf4llm`` from
``requirements.txt``.

Why a generated page: PyMuPDF4LLM reads a page line by line, so on a real
two-column layout it interleaves the two columns before the engine reorders
them. Building the same shape with ``insert_textbox`` reproduces that raw
interleave deterministically (see ``test_raw_is_interleaved_before_fix``) and
then checks that the engine (a) restores the reading order, (b) does **not**
lose the spaces between words, and (c) strips the running header and the
standalone page number.

On reproducing a whitespace-only span: PyMuPDF keeps a whole generated string
(``"alpha   beta"``) as a single span, so a synthetic PDF cannot force
PyMuPDF's ``"a", " ", "b"`` span split. That specific case stays covered at the
unit level in ``test_text_spacing.py`` (``TrimEdgeSpacesTests`` /
``CollectBlocksSpacingTests``), as the plan allows.

Run with (from the project root):

    .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pymupdf  # noqa: E402

import main  # noqa: E402

try:
    import pymupdf4llm  # noqa: E402
except ImportError:  # pragma: no cover - pymupdf4llm is in requirements.txt
    pymupdf4llm = None

_LEFT = (
    "They increase with a reduction in obesity related risk factors and the "
    "detection of diabetes improves outcomes for patients."
)
_RIGHT = "Right column opening sentence with several words that must stay separate."


def _build_two_column_page(doc):
    """A two-column page with a running header and an isolated page number."""
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 30), "CHAPTER 18 Endocrine System", fontsize=9)
    page.insert_textbox(pymupdf.Rect(50, 120, 280, 360), _LEFT, fontsize=10)
    page.insert_textbox(
        pymupdf.Rect(50, 380, 280, 620),
        "Left second paragraph continues here with more words to fill the column.",
        fontsize=10,
    )
    page.insert_textbox(pymupdf.Rect(320, 120, 550, 360), _RIGHT, fontsize=10)
    page.insert_textbox(
        pymupdf.Rect(320, 380, 550, 620),
        "Right second paragraph with more content for the second column here.",
        fontsize=10,
    )
    page.insert_text((50, 820), "656", fontsize=9)
    return page


def _norm(text):
    return " ".join(text.split())


@unittest.skipUnless(pymupdf4llm is not None, "pymupdf4llm non installato")
class SyntheticTwoColumnE2ETests(unittest.TestCase):
    """``to_markdown`` → ``_apply_engine_on_page`` on a generated page."""

    @classmethod
    def setUpClass(cls):
        cls.doc = pymupdf.open()
        _build_two_column_page(cls.doc)
        cls.raw = pymupdf4llm.to_markdown(cls.doc, pages=[0])
        cls.out, cls.label = main._apply_engine_on_page(cls.doc[0], cls.raw)

    @classmethod
    def tearDownClass(cls):
        cls.doc.close()

    def test_raw_is_interleaved_before_fix(self):
        # Sanity: the fixture is meaningful only if the raw output really mixes
        # the two columns (otherwise the engine would be fixing nothing).
        self.assertIn("Right column opening sentence", _norm(self.raw))
        self.assertLess(
            _norm(self.raw).index("Right column opening sentence"),
            _norm(self.raw).index("Left second paragraph"),
        )

    def test_production_path_used_auto_plan(self):
        self.assertEqual(self.label, "auto")
        self.assertTrue(self.out.strip())

    def test_no_glued_words_and_spacing_preserved(self):
        norm = _norm(self.out)
        self.assertIn(_LEFT, norm)
        self.assertIn(_RIGHT, norm)
        self.assertNotIn("theyincrease", norm.lower())
        self.assertNotIn("obesityrelated", norm.lower())

    def test_columns_are_back_in_reading_order(self):
        norm = _norm(self.out)
        self.assertLess(norm.index("They increase"), norm.index("Right column"))
        self.assertLess(
            norm.index("Left second paragraph"), norm.index("Right second paragraph")
        )

    def test_running_header_and_page_number_removed(self):
        self.assertNotIn("CHAPTER 18 Endocrine System", self.out)
        self.assertNotIn("656", _norm(self.out))

    def test_include_zone_selects_one_column_in_order(self):
        # Manual "green" tool: a zone over the left column must whitelist just
        # its lines (spaces included) and leave the right column out.
        out, label = main._apply_engine_on_page(
            self.doc[0], self.raw, include=((40.0, 100.0, 290.0, 640.0),)
        )
        self.assertEqual(label, "manual")
        norm = _norm(out)
        self.assertIn("They increase with a reduction in obesity", norm)
        self.assertNotIn("Right column opening sentence", norm)


if __name__ == "__main__":
    unittest.main()
