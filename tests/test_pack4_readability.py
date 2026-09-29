"""Pack 4 — leggibilita' del markdown (fix 1-7).

Sette fix di presentazione, tutti locali e conservativi:

1. ``_norm_noise`` ignora prefissi markdown (``##``, ``- ``) → header/numero di
   pagina non "travestiti" non trapelano piu'.
2. ``_drop_decor_lines`` toglie i glifi-bullet promossi a heading (``### »``).
3. ``_normalize_headings`` ricompone anche i titoli spezzati su una riga bold.
4. ``_normalize_caps_runins`` separa i titoletti MAIUSCOLI fusi col paragrafo.
5. ``_fix_empty_cells_md`` ripristina in testa la cella-header vuota.
6. ``_merge_caption_fragments_md`` ricompone le didascalie spezzate su piu' righe.
7. ``_link_figures`` toglie il testo interno alla figura (duplicato col PNG).

I test sono sintetici (CI-safe); i gold su PDF reali si saltano se assenti.
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

import layout_engine as L  # noqa: E402
from layout_engine import (  # noqa: E402
    _fix_empty_cells_md,
    _is_decor_line,
    _is_noise_line,
    _merge_caption_fragments_md,
    _normalize_caps_runins,
    _normalize_headings,
    _norm_noise,
)

from main import _link_figures  # noqa: E402

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


def _engine(pdf, page_no, dest=None):
    import pymupdf4llm

    import main

    raw = pymupdf4llm.to_markdown(pdf, pages=[page_no - 1])
    with pymupdf.open(pdf) as doc:
        out, _ = main._apply_engine_on_page(
            doc[page_no - 1], raw, figures_dir=dest, page_num=page_no - 1
        )
    return out


class NoisePrefixTests(unittest.TestCase):
    def test_norm_noise_ignores_heading_and_list_prefix(self):
        self.assertEqual(_norm_noise("## **ENDOCRINE DISORDERS**"), "endocrine disorders")
        self.assertEqual(_norm_noise("- 1123"), "1123")
        self.assertEqual(_norm_noise("### **1624**"), "1624")

    def test_headered_and_bulleted_noise_lines_are_recognized(self):
        cands = {"endocrine disorders", "1123"}
        self.assertTrue(_is_noise_line("## **ENDOCRINE DISORDERS**", cands))
        self.assertTrue(_is_noise_line("- 1123", cands))
        self.assertFalse(_is_noise_line("## Real Section Heading", cands))

    def test_two_piece_header_fused_by_reorder_is_noise(self):
        cands = {"chapter 221", "medical issues in pregnancy"}
        self.assertTrue(
            _is_noise_line("CHAPTER** 221** **Medical Issues in Pregnancy**", cands)
        )
        self.assertFalse(
            _is_noise_line("Medical issues in pregnancy are common.", cands)
        )


class DecorGlyphTests(unittest.TestCase):
    def test_decor_lines_recognized(self):
        for ln in ("### »", "### º", "- ›", "»", "◆"):
            self.assertTrue(_is_decor_line(ln), ln)
        for ln in ("### TREATMENT", "---", "| --- | --- |", "» text"):
            self.assertFalse(_is_decor_line(ln), ln)

    def test_normalize_headings_drops_decor_lines(self):
        md = "### »\n\n### **HYPOPARATHYROIDISM &**\n\n**PSEUDOHYPOPARATHYROIDISM**"
        out = _normalize_headings(md)
        self.assertNotIn("»", out)
        self.assertIn("**HYPOPARATHYROIDISM & PSEUDOHYPOPARATHYROIDISM**", out)

    def test_leading_decor_stripped_from_text_line(self):
        self.assertEqual(
            L._strip_leading_decor("»** Treatment of Other Thyroid Malignancies**"),
            "** Treatment of Other Thyroid Malignancies**",
        )
        self.assertEqual(L._strip_leading_decor("» "), "» ")  # riga di solo glifo: invariata


class SplitHeadingTests(unittest.TestCase):
    def test_merges_bold_continuation(self):
        md = "### **D. Radioactive Iodine (131I) Therapy for**\n\n**Differentiated Thyroid Cancer (DTC)**"
        out = _normalize_headings(md)
        self.assertIn("**D. Radioactive Iodine (131I) Therapy for Differentiated Thyroid Cancer (DTC)**", out)
        self.assertEqual(out.count("###"), 1)

    def test_merges_title_case_wrap(self):
        md = "### **B. Active Surveillance for Papillary Thyroid**\n\n**Microcarcinoma**"
        self.assertIn("Papillary Thyroid Microcarcinoma", _normalize_headings(md))

    def test_does_not_merge_all_caps_headings(self):
        for md in ("# INTRODUCTION\n\n# METHODS",
                   "### **PROGNOSIS**\n\n**TREATMENT AND PREVENTION**",
                   "### **LABORATORY**\n\n**FINDINGS**"):
            self.assertEqual(_normalize_headings(md), md)

    def test_does_not_merge_short_title_case_headings(self):
        self.assertEqual(_normalize_headings("# Introduction\n\n# Methods"),
                         "# Introduction\n\n# Methods")

    def test_merges_all_caps_wrap(self):
        md = "### **DEPRESSIVE, BIPOLAR, AND RELATED MOOD**\n\n**DISORDERS**"
        self.assertIn("DEPRESSIVE, BIPOLAR, AND RELATED MOOD DISORDERS", _normalize_headings(md))

    def test_does_not_merge_all_caps_section_label(self):
        md = "### **TREATMENT AND PREVENTION**\n\n**PROGNOSIS**"
        self.assertEqual(_normalize_headings(md), md)

    def test_empty_heading_joined_with_bold_below(self):
        out = L._apply_cleanup("### \n\n**FURTHER READING**\n\nBody text.", None, None)
        self.assertIn("### **FURTHER READING**", out)
        self.assertNotIn("### \n", out)


class CapsRuninTests(unittest.TestCase):
    def test_splits_caps_runin(self):
        out = _normalize_caps_runins(
            "**HYPERTENSIVE DISORDERS OF PREGNANCY** The hypertensive disorders complicate 5%."
        )
        self.assertEqual(
            out,
            "**HYPERTENSIVE DISORDERS OF PREGNANCY**\n\nThe hypertensive disorders complicate 5%.",
        )

    def test_ignores_single_word_runin(self):
        md = "**TREATMENT** Malaria"
        self.assertEqual(_normalize_caps_runins(md), md)

    def test_ignores_normal_bold_lead_in(self):
        md = "**In pregnant women with thyroid cancer**, surgery is performed."
        self.assertEqual(_normalize_caps_runins(md), md)

    def test_ignores_numbered_caption(self):
        md = "**TABLE 221-5** SELECTED LABORATORY VALUES IN PREGNANCY"
        self.assertEqual(_normalize_caps_runins(md), md)


class CaptionFragmentTests(unittest.TestCase):
    def test_merges_fragments_and_dedupes(self):
        md = (
            "**MEDICATIONS TO AVOID IN WOMEN OF**\n\n"
            "**CHILDBEARING AGE CONSIDERING**\n\n"
            "**PREGNANCY**\n\n"
            "**TABLE 221-3**\n\n"
            "**MEDICATIONS TO AVOID IN WOMEN OF CHILDBEARING AGE CONSIDERING PREGNANCY**"
        )
        self.assertEqual(
            _merge_caption_fragments_md(md),
            "**TABLE 221-3** MEDICATIONS TO AVOID IN WOMEN OF CHILDBEARING AGE CONSIDERING PREGNANCY",
        )

    def test_does_not_merge_two_distinct_captions(self):
        md = "**FIGURE 1 A chart**\n\n**FIGURE 2 Another chart**"
        self.assertEqual(_merge_caption_fragments_md(md), md)

    def test_does_not_merge_markerless_bold_run(self):
        md = "**THYROID DISEASE**\n\n**DIABETES MELLITUS**"
        self.assertEqual(_merge_caption_fragments_md(md), md)

    def test_idempotent(self):
        md = "**PREGNANCY**\n\n**TABLE 221-3**\n\n**PREGNANCY**"
        once = _merge_caption_fragments_md(md)
        self.assertEqual(_merge_caption_fragments_md(once), once)


class HeaderPadTests(unittest.TestCase):
    def test_restores_missing_leading_header_cell(self):
        md = (
            "| 1st TRIMESTER | 2nd TRIMESTER | 3rd TRIMESTER | NONPREGNANT |\n"
            "| --- | --- | --- | --- |\n"
            "| Hematocrit (%) | 31.0-41.0 | 30.0-39.0 | 28.0-40.0 | 35.4-44.4 |"
        )
        out = _fix_empty_cells_md(md)
        self.assertIn("|  | 1st TRIMESTER | 2nd TRIMESTER | 3rd TRIMESTER | NONPREGNANT |", out)
        self.assertNotIn("| Hematocrit (%) |  |", out)

    def test_well_formed_header_untouched(self):
        md = "| A | B |\n| --- | --- |\n| x | y |"
        self.assertEqual(_fix_empty_cells_md(md), md)


def _page_with_chart_labels():
    doc, page = _new_page()
    page.draw_rect(pymupdf.Rect(60, 60, 550, 200), color=(0, 0, 0), fill=(0.9, 0.9, 0.9))
    for i in range(5):
        page.draw_rect(
            pymupdf.Rect(80 + i * 90, 90, 130 + i * 90, 195),
            color=(0, 0, 0), fill=(0.4, 0.6, 0.8),
        )
    # etichette interne al grafico (assi): finiscono dentro la regione
    page.insert_textbox(pymupdf.Rect(62, 150, 300, 195), "5 6 7 8 9 HbgA1c", fontsize=8)
    page.insert_textbox(pymupdf.Rect(60, 240, 550, 270), "FIGURE 7 A bar chart.", fontsize=9)
    page.insert_textbox(pymupdf.Rect(60, 300, 550, 360), "Body text after.", fontsize=10)
    return doc, page


class LinkFigureInternalTextTests(unittest.TestCase):
    def test_internal_label_removed_when_image_linked(self):
        doc, page = _page_with_chart_labels()
        with tempfile.TemporaryDirectory() as d:
            md = "5 6 7 8 9 HbgA1c\n\nFIGURE 7 A bar chart.\n\nBody text after."
            out = _link_figures(md, page, Path(d), 0)
            self.assertEqual(len(list(Path(d).glob("*.png"))), 1)
        self.assertIn("![figura 1](", out)
        self.assertNotIn("HbgA1c", out)          # etichetta interna: nell'immagine
        self.assertIn("FIGURE 7 A bar chart.", out)
        self.assertIn("Body text after.", out)
        doc.close()

    def test_no_caption_no_removal(self):
        doc, page = _page_with_chart_labels()
        with tempfile.TemporaryDirectory() as d:
            md = "5 6 7 8 9 HbgA1c\n\nNo caption."
            self.assertEqual(_link_figures(md, page, Path(d), 0), md)
        doc.close()

    def test_caption_with_italic_suffix_is_matched(self):
        # "**Figure 19.3**  _Continued_" deve combaciare con "Figure 19.3 ■ Continued".
        doc, page = _page_with_chart_labels()
        page.insert_textbox(
            pymupdf.Rect(60, 240, 550, 270), "Figure 1 \u25a0 Continued", fontsize=9
        )
        with tempfile.TemporaryDirectory() as d:
            md = "**Figure 1**  _Continued_ \n\nBody text after."
            out = _link_figures(md, page, Path(d), 0)
        self.assertRegex(out, r"!\[figura \d+\]\(file://")
        self.assertLess(out.index("![figura"), out.index("**Figure 1**"))
        doc.close()


# ── gold su PDF reali (skip se assenti) ──────────────────────────────────────

@unittest.skipUnless(_gold_pdf("cu25.pdf"), "cu25.pdf non presente (gold Pack 4)")
class GoldReadabilityCu25Tests(unittest.TestCase):
    def test_running_header_and_page_number_do_not_leak(self):
        out = _engine(_gold_pdf("cu25.pdf"), 1154)
        lines = [ln for ln in out.split("\n")]
        self.assertFalse(any(ln.strip() == "## **ENDOCRINE DISORDERS**" for ln in lines))
        self.assertFalse(any(ln.strip() == "- 1119" for ln in lines))
        self.assertFalse(any(ln.lstrip().startswith("#") and "ENDOCRINE DISORDERS" in ln
                             for ln in lines))

    def test_split_heading_is_merged(self):
        out = _engine(_gold_pdf("cu25.pdf"), 1154)
        self.assertIn("Active Surveillance for Papillary Thyroid Microcarcinoma", out)
        self.assertNotIn(
            "**B. Active Surveillance for Papillary Thyroid**\n\n**Microcarcinoma**", out
        )

    def test_decor_glyph_is_not_a_heading(self):
        out = _engine(_gold_pdf("cu25.pdf"), 1158)
        self.assertFalse(any(
            ln.lstrip().startswith("#") and ln.strip().strip("#* ").strip() in ("»", "º", "›")
            for ln in out.split("\n")
        ))


@unittest.skipUnless(_gold_pdf("ce24.pdf"), "ce24.pdf non presente (gold Pack 4)")
class GoldReadabilityCe24Tests(unittest.TestCase):
    def test_fused_two_piece_header_removed(self):
        out = _engine(_gold_pdf("ce24.pdf"), 2590)
        self.assertFalse(any("CHAPTER" in ln and "Medical Issues" in ln
                             for ln in out.split("\n")))

    def test_table_header_aligned_with_data(self):
        out = _engine(_gold_pdf("ce24.pdf"), 2590)
        blocks = [r for r in out.split("\n") if r.strip().startswith("|")]
        blocks = [r for r in blocks if not set(r.replace("|", "").strip()) <= set("-: ")]
        if not blocks:
            self.skipTest("nessuna tabella")
        def nc(r):
            return len(r.strip().strip("|").split("|"))
        header, sep = nc(blocks[0]), nc(blocks[1])
        self.assertEqual(header, sep, "header e separatore disallineati")
        # il difetto era l'header PIÙ CORTO delle righe dati (colonne slittate)
        self.assertLessEqual(max(nc(r) for r in blocks[2:]), header)


@unittest.skipUnless(_gold_pdf("ce24.pdf"), "ce24.pdf non presente (gold Pack 4)")
class GoldCaptionFragmentsCe24Tests(unittest.TestCase):
    def test_caption_fragments_recomposed(self):
        out = _engine(_gold_pdf("ce24.pdf"), 2584)
        self.assertIn(
            "**TABLE 221-3** MEDICATIONS TO AVOID IN WOMEN OF CHILDBEARING AGE CONSIDERING PREGNANCY",
            out,
        )
        self.assertNotIn("**MEDICATIONS TO AVOID IN WOMEN OF**", out)


if __name__ == "__main__":
    unittest.main()

