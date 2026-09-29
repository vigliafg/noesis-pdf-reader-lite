"""Pack 2 — table fixes (``rebuild_tables`` / ``normalize_table_captions`` /
``fix_empty_cells``).

Unit tests are fully synthetic (pymupdf + markdown strings), so they run in CI.
The real-corpus checks (co23/p931 empty cells, ha22/p2240 lost grid, ce24/p489
one-column table, pa23/p743 caption as heading) skip when the PDFs are absent —
the corpus lives outside the repo.
"""

import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(_ROOT, "tests") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "tests"))

import pymupdf  # noqa: E402

import layout_engine  # noqa: E402
from layout_engine import (  # noqa: E402
    LayoutProfile,
    _fix_empty_cells_md,
    _normalize_table_captions_md,
    _rebuild_tables_md,
    plan_fixes,
    profile_page,
)

from test_fixes import _add_table, _new_page  # noqa: E402

_GOLD_DIR = os.environ.get(
    "NOESIS_GOLD_PDF_DIR",
    os.path.normpath(os.path.join(_ROOT, "..", "noesis-pdf-cloner-service", "pdfs")),
)


def _gold_pdf(name):
    for cand in (os.path.join(_ROOT, name), os.path.join(_GOLD_DIR, name)):
        if os.path.exists(cand):
            return cand
    return None


def _profile(tables=True) -> LayoutProfile:
    return LayoutProfile(
        columns=1, splits=(), columns_overlap=False, has_tables=tables,
        full_width_tables=1 if tables else 0, has_small_text=False,
        has_references=False, has_index=False, body_blocks=5,
    )


class NormalizeTableCaptionsTests(unittest.TestCase):
    def test_demotes_heading_caption_and_bolds_label(self):
        self.assertEqual(
            _normalize_table_captions_md("## Table 18.5 Classification of Diabetes"),
            "**TABLE 18.5** Classification of Diabetes",
        )

    def test_keeps_existing_bold_caption_idempotent(self):
        once = _normalize_table_captions_md(
            "# **TABLE 46-10** TOPICS THAT SHOULD BE DISCUSSED"
        )
        self.assertEqual(once, "**TABLE 46-10** TOPICS THAT SHOULD BE DISCUSSED")
        self.assertEqual(_normalize_table_captions_md(once), once)

    def test_strips_control_bytes_and_tabs(self):
        self.assertEqual(
            _normalize_table_captions_md("**TABLE 3\t** \u2002 \x07Risk Factors"),
            "**TABLE 3** Risk Factors",
        )

    def test_body_sentence_starting_with_table_is_untouched(self):
        md = "Table 2 shows that the PSI score is higher."
        self.assertEqual(_normalize_table_captions_md(md), md)

    def test_table_rows_are_not_touched(self):
        md = "|**TABLE 2**Pneumonia Severity Index||\n|---|\n| a | b |"
        self.assertEqual(_normalize_table_captions_md(md), md)

    def test_fenced_code_is_protected(self):
        md = "```\n## Table 18.5 x\n```"
        self.assertEqual(_normalize_table_captions_md(md), md)


class FixEmptyCellsTests(unittest.TestCase):
    def test_drops_trailing_empty_cells(self):
        md = "| A | B |\n| --- | --- |\n| x | y |\n| z |  |"
        out = _fix_empty_cells_md(md)
        self.assertIn("| z |", out)
        self.assertNotIn("| z |  |", out)

    def test_drops_column_empty_in_every_row(self):
        md = "| A |  |\n| --- | --- |\n| x |  |\n| y |  |"
        out = _fix_empty_cells_md(md)
        self.assertIn("| A |", out)
        self.assertIn("| --- |", out)
        self.assertNotIn("||", out)

    def test_realigns_numeric_last_column(self):
        # pymupdf merges the sign+value into the label cell on some rows.
        md = (
            "| Risk factor | Points |\n| --- | --- |\n"
            "| Nursing home resident | +10 |\n"
            "| Liver disease | +20 |\n"
            "| Active neoplasm +30 |  |\n"
        )
        out = _fix_empty_cells_md(md)
        self.assertIn("| Active neoplasm | +30 |", out)

    def test_does_not_move_unsigned_numbers(self):
        # ">30" is part of the text, not a points value: leave it alone.
        md = (
            "| Risk factor | Points |\n| --- | --- |\n"
            "| Nursing home resident | +10 |\n"
            "| Liver disease | +20 |\n"
            "| Age in years 10 |  |\n"
        )
        out = _fix_empty_cells_md(md)
        self.assertIn("| Age in years 10 |", out)

    def test_non_table_text_untouched(self):
        md = "A normal paragraph with | no table structure"
        self.assertEqual(_fix_empty_cells_md(md), md)


class RebuildTablesTests(unittest.TestCase):
    def test_rebuilds_matching_table_with_caption(self):
        doc, page = _new_page()
        _add_table(
            page,
            (50, 80, 300, 160),
            [["Name", "Value"], ["Alpha", "42"], ["Beta", "7"]],
        )
        md = (
            "Intro line.\n\n"
            "| WRONG | GRID |\n| --- | --- |\n| a | b |\n| c |  |\n\n"
            "Outro line."
        )
        out = _rebuild_tables_md(md, page)
        self.assertIn("Intro line.", out)
        self.assertIn("| Name | Value |", out)
        self.assertIn("| Alpha | 42 |", out)
        self.assertNotIn("WRONG", out)
        self.assertIn("Outro line.", out)
        doc.close()

    def test_count_mismatch_leaves_text_unchanged(self):
        doc, page = _new_page()
        _add_table(page, (50, 80, 300, 160), [["Name", "Value"], ["A", "1"]])
        md = "| t1 | b |\n| --- | --- |\n| x | y |\n\n| t2 | c |\n| --- | --- |\n| z | w |"
        self.assertEqual(_rebuild_tables_md(md, page), md)
        doc.close()

    def test_well_formed_table_is_not_rebuilt(self):
        # A table pymupdf4llm rendered fine must be kept as-is: find_tables can
        # be worse (merged header) and rebuilding would lose content.
        doc, page = _new_page()
        _add_table(page, (50, 80, 300, 160), [["Name", "Value"], ["A", "1"]])
        md = "| INDICATION | DRUG |\n| --- | --- |\n| IBS-C | Linaclotide |"
        self.assertEqual(_rebuild_tables_md(md, page), md)
        doc.close()

    def test_no_tables_leaves_text_unchanged(self):
        doc, page = _new_page()
        page.insert_textbox(pymupdf.Rect(50, 120, 250, 200), "Body.", fontsize=10)
        md = "| a | b |\n| --- | --- |\n| x | y |"
        self.assertEqual(_rebuild_tables_md(md, page), md)
        doc.close()


class TableFixPlanTests(unittest.TestCase):
    def test_table_page_includes_the_three_fixes(self):
        ids = [f.id for f in plan_fixes(_profile(True), "PyMuPDF4LLM ⚡", mode="auto")]
        self.assertIn("rebuild_tables", ids)
        self.assertIn("normalize_table_captions", ids)
        self.assertIn("fix_empty_cells", ids)
        # rebuild before normalize before empty cells
        self.assertLess(ids.index("rebuild_tables"), ids.index("normalize_table_captions"))
        self.assertLess(ids.index("normalize_table_captions"), ids.index("fix_empty_cells"))

    def test_no_table_page_skips_them(self):
        ids = [f.id for f in plan_fixes(_profile(False), "PyMuPDF4LLM ⚡", mode="auto")]
        self.assertNotIn("rebuild_tables", ids)
        self.assertNotIn("fix_empty_cells", ids)

    def test_overlapping_two_column_skips_rebuild(self):
        # reorder_columns already rebuilds tables on such pages: rebuild_tables
        # must stay off to avoid pairing a caption with the wrong grid.
        profile = LayoutProfile(
            columns=2, splits=(100.0,), columns_overlap=True, has_tables=True,
            full_width_tables=1, has_small_text=False, has_references=False,
            has_index=False, body_blocks=5,
        )
        ids = [f.id for f in plan_fixes(profile, "PyMuPDF4LLM ⚡", mode="auto")]
        self.assertNotIn("rebuild_tables", ids)
        self.assertIn("normalize_table_captions", ids)
        self.assertIn("fix_empty_cells", ids)

    def test_fixes_are_toggleable(self):
        ids = [
            f.id for f in plan_fixes(
                _profile(True), "PyMuPDF4LLM ⚡",
                overrides={"disable": ["rebuild_tables", "fix_empty_cells"]},
            )
        ]
        self.assertNotIn("rebuild_tables", ids)
        self.assertNotIn("fix_empty_cells", ids)


# ── gold su PDF reali (skip se assenti) ──────────────────────────────────────

def _engine(pdf, page_no):
    import pymupdf4llm

    raw = pymupdf4llm.to_markdown(pdf, pages=[page_no - 1])
    import main

    with pymupdf.open(pdf) as doc:
        out, _ = main._apply_engine_on_page(doc[page_no - 1], raw)
    return raw, out


def _table_rows(text):
    return [ln for ln in text.split("\n") if ln.strip().startswith("|")]


@unittest.skipUnless(_gold_pdf("co23.pdf"), "co23.pdf non presente (gold tabelle)")
class GoldEmptyCellsCo23Tests(unittest.TestCase):
    def test_numeric_points_realigned_and_no_adjacent_empty_cells(self):
        _raw, out = _engine(_gold_pdf("co23.pdf"), 931)
        rows = _table_rows(out)
        # Points moved back to their column on rows where pymupdf merged them.
        self.assertIn("| Nursing home resident | +10 |", rows)
        self.assertIn("| Liver disease | +20 |", rows)
        # No adjacent empty cells left inside a table row.
        self.assertFalse(any(re.search(r"\|\s*\|", r) for r in rows))


@unittest.skipUnless(_gold_pdf("ce24.pdf"), "ce24.pdf non presente (gold tabelle)")
class GoldOneColumnCe24Tests(unittest.TestCase):
    def test_caption_on_its_own_line_and_not_a_heading(self):
        _raw, out = _engine(_gold_pdf("ce24.pdf"), 489)
        self.assertRegex(out, r"(?m)^\*\*TABLE 46-10\*\* TOPICS")
        self.assertFalse(
            any(ln.lstrip().startswith("#") and "TABLE 46-10" in ln
                for ln in out.split("\n"))
        )


@unittest.skipUnless(_gold_pdf("pa23.pdf"), "pa23.pdf non presente (gold tabelle)")
class GoldCaptionHeadingPa23Tests(unittest.TestCase):
    def test_table_caption_is_not_a_heading(self):
        _raw, out = _engine(_gold_pdf("pa23.pdf"), 743)
        self.assertFalse(
            any(ln.lstrip().startswith("#") and "Table 18.5" in ln
                for ln in out.split("\n"))
        )
        self.assertIn("Table 18.5", out)


@unittest.skipUnless(_gold_pdf("ha22.pdf"), "ha22.pdf non presente (gold tabelle)")
class GoldLostGridHa22Tests(unittest.TestCase):
    """B/p2240: ``find_tables`` detects nothing (bordered box), so the grid is
    genuinely lost and ``rebuild_tables`` must be a no-op. The fix guarantees it
    adds no further loss: the ordered one-column list survives, without the
    phantom empty cells.

    (The caption/``Transudative`` line is dropped by the pre-existing reorder on
    this page — that is a ``_column_aware_markdown`` limitation, not a Pack 2
    regression; verified against the pre-Pack-2 output.)
    """

    def test_content_kept_in_ordered_single_column(self):
        _raw, out = _engine(_gold_pdf("ha22.pdf"), 2240)
        rows = _table_rows(out)
        self.assertIn("| 1. Congestive heart failure |", rows)
        self.assertIn("| 10. Meigs’ syndrome |", rows)
        self.assertFalse(any(re.search(r"\|\s*\|", r) for r in rows))


if __name__ == "__main__":
    unittest.main()
