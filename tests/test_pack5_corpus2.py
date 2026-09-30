"""Pack 5 — leggibilità su layout difficili (corpus2).

Fix mirati emersi dal test su 120 pagine (12 PDF, layout più impegnativi):

A. ``_normalize_html_tags``: via i tag HTML residui (<mark>, <sup>, <br>) → non
   impediscono più la rimozione di header/footer e non sporcano il markdown.
B. ``_normalize_replacement_chars``: ``U+FFFD`` (glifo assente) → spazio.
C. ``_rejoin_spaced_headings``: ricompone i titoli a lettere separate
   ("**D** **~~i~~ vert**" → "Diverticulum").
D. ``_norm_noise`` unisce lo spazio davanti a una lettera singola ("II n" ==
   "IIn") per gli header con apice.
E. ``detect_sideways_rotation``: rileva il testo a 90° per raddrizzare la pagina.
F. Rete di sicurezza del riordino: ``_content_retention`` / ``_fragment_lines``.

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


class HtmlTagTests(unittest.TestCase):
    def test_removes_mark_sup_and_converts_br(self):
        md = "<mark>8 The nervous sysTem</mark> <sup>n</sup> a<br>b"
        out = L._normalize_html_tags(md)
        self.assertNotIn("<mark>", out)
        self.assertNotIn("<sup>", out)
        self.assertNotIn("<br>", out)
        self.assertIn("8 The nervous sysTem", out)
        self.assertIn("a b", out)

    def test_norm_noise_ignores_tags(self):
        self.assertEqual(L._norm_noise("<mark>Role & responsibilities</mark>"),
                         L._norm_noise("Role & responsibilities"))


class ReplacementCharTests(unittest.TestCase):
    def test_fffd_becomes_space(self):
        out = L._normalize_replacement_chars("ATP synthesis\ufffd 108–9")
        self.assertNotIn("\ufffd", out)
        self.assertIn("synthesis 108", out)


class SoftHyphenTests(unittest.TestCase):
    def test_soft_hyphen_rejoins_word(self):
        out = L._normalize_soft_hyphens("dom\u00ad peridone and well\u00adknown")
        self.assertNotIn("\u00ad", out)
        self.assertIn("domperidone", out)
        self.assertIn("wellknown", out)


class SpacedHeadingTests(unittest.TestCase):
    def test_rejoins_letter_spaced_heading(self):
        self.assertEqual(
            L._rejoin_spaced_headings("# **Zenker's D** **~~i~~ vert** **~~i~~ culum**"),
            "# **Zenker's Diverticulum**",
        )
        self.assertEqual(
            L._rejoin_spaced_headings("# **Herpes S** **~~i~~ mplex**"),
            "# **Herpes Simplex**",
        )

    def test_does_not_touch_heading_with_separator(self):
        md = "### **Positive stress test** = **coronary angiography**"
        self.assertEqual(L._rejoin_spaced_headings(md), md)


class NormNoiseSingleLetterTests(unittest.TestCase):
    def test_superscript_header_matches(self):
        self.assertEqual(L._norm_noise("Section IIn Laboratory Values"),
                         L._norm_noise("Section II n Laboratory Values"))

    def test_running_head_with_page_prefix_is_noise(self):
        self.assertTrue(L._is_noise_line("48 CHAPTER 1 Cellular structure and function", set()))
        self.assertTrue(L._is_noise_line("**406 CHAPTER 6 Respiratory systems**", set()))
        # un titolo di capitolo senza numero-pagina davanti NON è rumore
        self.assertFalse(L._is_noise_line("CHAPTER 1 Cellular structure", set()))


class CaptionLetterNumberTests(unittest.TestCase):
    def test_letter_number_caption_matches(self):
        # "TABLE E2" (marcatore con lettera) deve essere riconosciuto
        block = ("|TABLE E2 Inter|national Classification of Diabetic Mac|"
                 "ular Edema (DME)|\n|---|---|---|\n| a | b | c |")
        self.assertTrue(L._caption_from_block(block))
        self.assertIn("E2", L._caption_from_block(block))


class ReorderGuardHelpersTests(unittest.TestCase):
    def test_content_retention(self):
        self.assertAlmostEqual(L._content_retention("alpha beta gamma", "alpha beta gamma"), 1.0)
        self.assertLess(L._content_retention("alpha beta gamma delta", "alpha"), 0.5)

    def test_fragment_lines(self):
        self.assertEqual(L._fragment_lines("- 2\n- d\n1.\n"), 3)
        self.assertEqual(L._fragment_lines("normal line of prose\n\n- real item text\n"), 0)


class SidewaysRotationTests(unittest.TestCase):
    def test_detects_vertical_text(self):
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_text((40, 40), "tiny horizontal", fontsize=9)
        page.insert_textbox(
            pymupdf.Rect(60, 80, 120, 800),
            "Vertical line of text drawn sideways " * 6,
            fontsize=11, rotate=90,
        )
        self.assertEqual(L.detect_sideways_rotation(page), 90)
        doc.close()

    def test_horizontal_page_returns_zero(self):
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 500),
                            "Normal horizontal text. " * 20, fontsize=11)
        self.assertEqual(L.detect_sideways_rotation(page), 0)
        doc.close()


def _corpus(pdf):
    path = os.path.join(_CORPUS2, f"{pdf}.pdf")
    return path if os.path.isfile(path) else None


@unittest.skipUnless(_corpus("fe23"), "corpus2/fe23.pdf assente")
class GoldRotatedTableTests(unittest.TestCase):
    """fe23/p64: pagina landscape; il raddrizzamento dà la tabella giusta."""

    def test_rotated_table_is_readable(self):
        import main  # noqa: E402
        with pymupdf.open(_corpus("fe23")) as doc:
            page = doc[64]
            raw = main._extract_pymupdf4llm(_corpus("fe23"), 64, ocr_language="eng")
            out, _ = main._apply_engine_on_page(page, raw)
        self.assertIn("TEST", out)
        self.assertIn("APRI", out)
        # le note a piè di tabella devono restare
        self.assertIn("markers of liver fibrosis", out.lower())
        # niente garbuglio da get_textbox su pagina ruotata
        self.assertNotIn("Non estinal", out)


@unittest.skipUnless(_corpus("oh2"), "corpus2/oh2.pdf assente")
class GoldMarkHeaderTests(unittest.TestCase):
    """oh2/p313: header in <mark> non deve trapelare."""

    def test_mark_header_removed(self):
        import main  # noqa: E402
        with pymupdf.open(_corpus("oh2")) as doc:
            page = doc[313]
            raw = main._extract_pymupdf4llm(_corpus("oh2"), 313, ocr_language="eng")
            out, _ = main._apply_engine_on_page(page, raw)
        self.assertNotIn("<mark>", out)
        self.assertNotIn("The nervous sysTem", out)
        self.assertIn("Myotonic dystrophy", out)


@unittest.skipUnless(_corpus("mw15"), "corpus2/mw15.pdf assente")
class GoldSpacedHeadingTests(unittest.TestCase):
    def test_spaced_heading_rejoined(self):
        import main  # noqa: E402
        with pymupdf.open(_corpus("mw15")) as doc:
            page = doc[159]
            raw = main._extract_pymupdf4llm(_corpus("mw15"), 159, ocr_language="eng")
            out, _ = main._apply_engine_on_page(page, raw)
        self.assertIn("Zenker's Diverticulum", out)


@unittest.skipUnless(_corpus("to22"), "corpus2/to22.pdf assente")
class GoldFalseTableTests(unittest.TestCase):
    """to22/p251: una lista definizionale non deve essere distrutta."""

    def test_definition_list_not_corrupted(self):
        import main  # noqa: E402
        import tempfile
        with pymupdf.open(_corpus("to22")) as doc:
            page = doc[251]
            raw = main._extract_pymupdf4llm(_corpus("to22"), 251, ocr_language="eng")
            with tempfile.TemporaryDirectory() as td:
                out, _ = main._apply_engine_on_page(
                    page, raw, figures_dir=td, page_num=251)
        for label in ("Adequacy", "Bones", "Cartilage", "Soft Tissues"):
            self.assertIn(label, out)
        # contenuto della Tabella 6 non deve sparire con il link figura
        self.assertIn("vertebral", out)
        self.assertNotRegex(out, r"(?m)^[ \t]*- [0-9d][ \t]*$")


if __name__ == "__main__":
    unittest.main()
