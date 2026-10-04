#!/usr/bin/env python3
"""Unit CI-safe dei proxy di layout (Fase 0.2).

Solo ``layout_proxies``: niente Qt, niente pymupdf, niente rete. Gli elementi
sono costruiti a mano nella forma dei ``page_boxes``.
"""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import layout_proxies as lp  # noqa: E402

PAGE_W = 600.0


def el(cls: str, x0: float, y0: float, x1: float, y1: float,
       text: str = "lorem ipsum dolor sit amet consectetur") -> dict:
    return {"class": cls, "bbox": (x0, y0, x1, y1), "w": x1 - x0,
            "y0": y0, "text": text}


def two_columns() -> list[dict]:
    """Due colonne pulite (4+ box di testo per lato)."""
    out = []
    for i in range(5):
        y = 100 + i * 30
        out.append(el("text", 50, y, 280, y + 20))
        out.append(el("text", 320, y, 550, y + 20))
    return out


class ColumnSplitTests(unittest.TestCase):
    def test_detects_two_columns(self):
        splits = lp.column_splits(two_columns(), PAGE_W)
        self.assertEqual(len(splits), 1)
        self.assertTrue(280 < splits[0] < 320)

    def test_single_column(self):
        els = [el("text", 50, 100 + i * 30, 550, 120 + i * 30) for i in range(6)]
        self.assertEqual(lp.column_splits(els, PAGE_W), [])

    def test_column_of(self):
        splits = [300.0]
        self.assertEqual(lp.column_of(el("text", 50, 0, 280, 10), splits), 0)
        self.assertEqual(lp.column_of(el("text", 320, 0, 550, 10), splits), 1)


class OrderProxyTests(unittest.TestCase):
    def test_clean_two_columns(self):
        p = lp.order_proxy(two_columns(), PAGE_W)
        self.assertEqual(p["score"], 1.0)
        self.assertEqual(p["anomalies"], [])
        self.assertEqual(p["metrics"]["n_columns"], 2)

    def test_cross_column_flagged(self):
        els = two_columns() + [el("text", 200, 400, 400, 430)]
        p = lp.order_proxy(els, PAGE_W)
        self.assertLess(p["score"], 1.0)
        self.assertIn("cross_column", {a["type"] for a in p["anomalies"]})

    def test_ref_interlace_flagged(self):
        els = [
            el("list-item", 50, 100, 280, 140, "1. alpha reference one"),
            el("list-item", 320, 110, 550, 150, "2. beta reference two"),
        ] + two_columns()
        types = {a["type"] for a in lp.order_proxy(els, PAGE_W)["anomalies"]}
        self.assertIn("ref_interlace", types)

    def test_duplicate_flagged(self):
        a = el("text", 50, 100, 280, 130, "same text here")
        b = el("text", 51, 101, 281, 131, "same text here")
        types = {x["type"] for x in lp.order_proxy([a, b], PAGE_W)["anomalies"]}
        self.assertIn("duplicate", types)


class TableProxyTests(unittest.TestCase):
    def test_good_grid(self):
        t = el("table", 50, 100, 300, 200, "| a | b |\n|---|---|\n| c | d |")
        p = lp.table_proxy([t])
        self.assertEqual(p["score"], 1.0)
        self.assertEqual(p["metrics"]["n_tables"], 1)

    def test_degenerate_one_column(self):
        t = el("table", 50, 100, 300, 200, "| only |\n|---|---|\n| a |")
        p = lp.table_proxy([t])
        self.assertLess(p["score"], 1.0)
        self.assertIn("table_degenerate", {a["type"] for a in p["anomalies"]})

    def test_no_markdown(self):
        t = el("table", 50, 100, 300, 200, "plain text without pipes")
        types = {a["type"] for a in lp.table_proxy([t])["anomalies"]}
        self.assertIn("table_no_markdown", types)

    def test_grid_degenerate_from_page(self):
        class _Page:
            layout_information = [{"table_grid": {"h_lines": [], "v_lines": [[0, 1]]}}]

        t = el("table", 50, 100, 300, 200, "| a | b |\n|---|---|\n| c | d |")
        types = {a["type"] for a in lp.table_proxy([t], _Page())["anomalies"]}
        self.assertIn("table_grid_degenerate", types)


class FigureProxyTests(unittest.TestCase):
    _IMG = "![fig](data:image/jpeg;base64,AAAA)"

    def test_missing_when_no_embedded(self):
        p = lp.figure_proxy([el("picture", 50, 100, 300, 300)], md="text only")
        self.assertIn("figure_missing", {a["type"] for a in p["anomalies"]})

    def test_present_when_embedded(self):
        p = lp.figure_proxy([el("picture", 50, 100, 300, 300)], md=self._IMG)
        self.assertNotIn("figure_missing", {a["type"] for a in p["anomalies"]})
        self.assertEqual(p["metrics"]["embedded"], 1)

    def test_duplicate_flagged(self):
        a = el("picture", 50, 100, 300, 300)
        b = el("picture", 51, 101, 301, 301)
        types = {x["type"] for x in lp.figure_proxy([a, b])["anomalies"]}
        self.assertIn("figure_duplicate", types)


class LayoutClassTests(unittest.TestCase):
    def test_prosa(self):
        self.assertEqual(lp.layout_class(two_columns()[:2], PAGE_W), "prosa")

    def test_columns(self):
        self.assertEqual(lp.layout_class(two_columns(), PAGE_W), "colonne/box-liste")

    def test_list_is_columns_box(self):
        els = [el("list-item", 50, 100, 300, 140, "a list entry")]
        self.assertEqual(lp.layout_class(els, PAGE_W), "colonne/box-liste")

    def test_tabelle(self):
        t = el("table", 50, 100, 300, 200, "| a | b |\n|---|---|\n| c | d |")
        self.assertEqual(lp.layout_class([t], PAGE_W), "tabelle")

    def test_equazioni(self):
        self.assertEqual(lp.layout_class([el("formula", 50, 100, 300, 120)], PAGE_W),
                         "equazioni")

    def test_figure(self):
        self.assertEqual(lp.layout_class([el("picture", 50, 100, 300, 300)], PAGE_W),
                         "figure")

    def test_indici(self):
        text = "\n".join(f"term{i} .... {i}" for i in range(20))
        self.assertEqual(lp.layout_class([el("text", 50, 100, 300, 500, text)], PAGE_W),
                         "indici")


class ConfidenceTests(unittest.TestCase):
    def test_clean_is_high(self):
        p = lp.all_proxies(two_columns(), PAGE_W)
        self.assertEqual(p["confidence"], 1.0)
        self.assertFalse(p["escalate"])

    def test_gate_failure_lowers(self):
        p = lp.all_proxies(two_columns(), PAGE_W, gate_ok=False,
                           gate_reason="recall 0.50")
        self.assertLessEqual(p["confidence"], 0.6)
        self.assertTrue(p["escalate"])

    def test_worst_proxy_dominates(self):
        t = el("table", 50, 100, 300, 200, "plain text without pipes")
        p = lp.all_proxies([t], PAGE_W)
        self.assertLess(p["confidence"], 1.0)

    def test_all_proxies_keys(self):
        p = lp.all_proxies(two_columns(), PAGE_W)
        self.assertEqual(set(p), {"order", "table", "figure", "text",
                                  "layout_class", "confidence", "escalate"})


if __name__ == "__main__":
    unittest.main()
