#!/usr/bin/env python3
"""Unit CI-safe dell'ordine di lettura dell'IR (Fase 1).

Testa ``ir_layout.reorder_boxes`` e la segmentazione a colonne robusta
(``ir_layout._column_splits``), senza Qt/pymupdf/rete: gli elementi sono dict
nella forma dei ``page_boxes``.
"""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ir_layout  # noqa: E402

PAGE_W = 600.0


def el(name: str, x0: float, y0: float, x1: float, y1: float,
       cls: str = "text") -> dict:
    return {"name": name, "class": cls, "bbox": (x0, y0, x1, y1),
            "w": x1 - x0, "y0": y0, "text": name}


def two_columns() -> list[dict]:
    out = []
    for i in range(5):
        y = 100 + i * 30
        out.append(el(f"L{i}", 50, y, 280, y + 20))
        out.append(el(f"R{i}", 320, y, 550, y + 20))
    return out


class ColumnSplitTests(unittest.TestCase):
    def test_two_columns(self):
        splits = ir_layout._column_splits(two_columns(), PAGE_W)
        self.assertEqual(len(splits), 1)
        self.assertTrue(280 < splits[0] < 320)

    def test_robust_to_bridging_box(self):
        # un box che "pontica" le colonne non deve cancellare il confine
        els = two_columns() + [el("BRIDGE", 200, 400, 400, 430)]
        splits = ir_layout._column_splits(els, PAGE_W)
        self.assertEqual(len(splits), 1)
        self.assertTrue(280 < splits[0] < 320)


class ReorderBoxesTests(unittest.TestCase):
    def test_column_major(self):
        els = two_columns()
        order = [e["name"] for e in ir_layout.reorder_boxes(els, PAGE_W)]
        self.assertEqual(order, ["L0", "L1", "L2", "L3", "L4",
                                 "R0", "R1", "R2", "R3", "R4"])

    def test_full_width_separator_splits_bands(self):
        els = two_columns()
        els.append(el("SEP", 50, 400, 550, 420))
        els.append(el("L9", 50, 450, 280, 470))
        order = [e["name"] for e in ir_layout.reorder_boxes(els, PAGE_W)]
        self.assertEqual(order[-2:], ["SEP", "L9"])
        self.assertLess(order.index("R4"), order.index("SEP"))

    def test_no_element_dropped(self):
        els = two_columns() + [el("SEP", 50, 400, 550, 420)]
        self.assertEqual(len(ir_layout.reorder_boxes(els, PAGE_W)), len(els))


if __name__ == "__main__":
    unittest.main()
