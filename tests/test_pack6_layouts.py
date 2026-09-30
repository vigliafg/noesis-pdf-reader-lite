"""Pack 6 — leggibilità su layout difficili (segue Pack 5).

Fix mirati:
1. ``_demote_numbered_headings``: una voce numerata promossa a heading
   ("### **3**: …") torna paragrafo e la frase avvolta viene ricucita.
2. ``_split_trailing_bold_heading``: un titolo bold incollato in coda a una
   frase ("… heel stick. **Abnormal findings**") va su riga propria.
3. ``_despace_numbers``: numeri "spaziati" dal font ("3 0-6 0" -> "30-60").
4. ``_despace_blockquote_letters``: lettera iniziale separata nei box
   ("> V ancomycin E rythromycin" -> "> Vancomycin Erythromycin").

I test sono sintetici (CI-safe); i gold su corpus2 si saltano se assente.
"""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pymupdf  # noqa: E402

import layout_engine as L  # noqa: E402

_CORPUS2 = os.path.join(_ROOT, "corpus2")


class DemoteNumberedHeadingTests(unittest.TestCase):
    def test_numbered_heading_becomes_paragraph_and_joins_wrap(self):
        md = "### **3**: open mouth sufficiently to place\n\nincisors to see"
        out = L._demote_numbered_headings(md)
        self.assertFalse(out.startswith("#"))
        self.assertIn("**3**: open mouth sufficiently to place incisors to see", out)

    def test_normal_heading_untouched(self):
        md = "### **Treatment**"
        self.assertEqual(L._demote_numbered_headings(md), md)


class SplitTrailingBoldTests(unittest.TestCase):
    def test_trailing_bold_heading_split(self):
        md = "-  For pediatric patients, draw blood from a heel stick. **Abnormal findings**"
        out = L._split_trailing_bold_heading(md)
        self.assertIn("heel stick.\n\n**Abnormal findings**", out)

    def test_short_bold_end_not_split(self):
        md = "use this **carefully**"
        self.assertEqual(L._split_trailing_bold_heading(md), md)


class DespaceNumbersTests(unittest.TestCase):
    def test_range_rejoined(self):
        self.assertIn("30-60", L._despace_numbers("done within 3 0-6 0 minutes"))
        self.assertIn("90-110", L._despace_numbers("(9 0-1 10 mmHg)"))

    def test_plain_numbers_untouched(self):
        self.assertEqual(L._despace_numbers("normal 3 0 days"), "normal 3 0 days")


class DespaceBlockquoteTests(unittest.TestCase):
    def test_blockquote_letters_rejoined(self):
        out = L._despace_blockquote_letters("> V ancomycin E rythromycin (and x)")
        self.assertIn("Vancomycin Erythromycin", out)

    def test_legit_single_letter_words_kept(self):
        line = "> A diagnosis of X and Y"
        self.assertEqual(L._despace_blockquote_letters(line), line)


def _corpus2(name):
    p = os.path.join(_CORPUS2, f"{name}.pdf")
    return p if os.path.isfile(p) else None


def _engine(pdf, pg):
    import main
    with pymupdf.open(pdf) as doc:
        raw = main._extract_pymupdf4llm(pdf, pg, ocr_language="eng")
        out, _ = main._apply_engine_on_page(doc[pg], raw)
    return out


@unittest.skipUnless(_corpus2("ne17"), "corpus2/ne17.pdf assente")
class GoldNumberedHeadingTests(unittest.TestCase):
    def test_no_numbered_heading(self):
        out = _engine(_corpus2("ne17"), 24)
        self.assertNotRegex(out, r"(?m)^#{1,6}\s*\*\*3\*\*")
        self.assertIn("between the incisors to determine", out)


@unittest.skipUnless(_corpus2("mo21"), "corpus2/mo21.pdf assente")
class GoldGluedHeadingTests(unittest.TestCase):
    def test_bold_heading_not_glued(self):
        out = _engine(_corpus2("mo21"), 685)
        self.assertNotIn("heel stick. **Abnormal findings**", out)


@unittest.skipUnless(_corpus2("mw15"), "corpus2/mw15.pdf assente")
class GoldSpacedNumbersTests(unittest.TestCase):
    def test_no_spaced_number_ranges(self):
        import re
        for pg in (263, 293):
            out = _engine(_corpus2("mw15"), pg)
            self.assertIsNone(re.search(r"\d \d-\d \d", out))


@unittest.skipUnless(_corpus2("to22"), "corpus2/to22.pdf assente")
class GoldBoxDespaceTests(unittest.TestCase):
    def test_box_letter_spacing_rejoined(self):
        out = _engine(_corpus2("to22"), 762)
        self.assertIn("Chloramphenicol", out)


if __name__ == "__main__":
    unittest.main()
