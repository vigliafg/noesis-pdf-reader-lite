#!/usr/bin/env python3
"""Fase 2 — test degli **helper puri** dell'oracolo indipendente (CI-safe).

Non carica il modello ONNX (nessuna rete, nessun asset): verifica solo la
geometria di confronto usata dalla misura (``is_full_width`` / ``contains`` /
``iou``) e il contratto delle classi. Il prototipo DocLayout-YOLO resta
**opt-in** (dev/validazione), mai in CI.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TestIndependentZonesHelpers(unittest.TestCase):
    def setUp(self):
        import independent_zones as iz
        self.iz = iz

    def test_classes_docstructbench(self):
        self.assertEqual(len(self.iz.CLASSES), 10)
        self.assertEqual(self.iz.CLASSES[0], "title")
        self.assertEqual(self.iz.CLASSES[3], "figure")
        self.assertEqual(self.iz.CLASSES[4], "figure_caption")
        self.assertEqual(self.iz.CLASSES[5], "table")
        self.assertEqual(self.iz.CLASSES[8], "isolate_formula")

    def test_is_full_width(self):
        bw = 612.0
        self.assertTrue(self.iz.is_full_width((50, 50, 560, 200), bw))
        # troppo stretta
        self.assertFalse(self.iz.is_full_width((50, 50, 300, 200), bw))
        # troppo bassa (soglia 20pt)
        self.assertFalse(self.iz.is_full_width((50, 50, 560, 60), bw))

    def test_contains(self):
        box = (0, 0, 100, 100)
        self.assertTrue(self.iz.contains(box, (40, 40, 60, 60)))
        self.assertFalse(self.iz.contains(box, (90, 90, 130, 130)))  # centro fuori
        self.assertFalse(self.iz.contains(box, (101, 10, 110, 20)))

    def test_iou(self):
        a = (0, 0, 10, 10)
        self.assertAlmostEqual(self.iz.iou(a, a), 1.0)
        self.assertAlmostEqual(self.iz.iou(a, (10, 0, 20, 10)), 0.0)
        # metà sovrapposizione: inter 50 / unione 150
        self.assertAlmostEqual(self.iz.iou(a, (0, 5, 10, 15)), 50 / 150, places=6)

    def test_iou_disjoint_zero(self):
        self.assertEqual(self.iz.iou((0, 0, 5, 5), (100, 100, 105, 105)), 0.0)


if __name__ == "__main__":
    unittest.main()
