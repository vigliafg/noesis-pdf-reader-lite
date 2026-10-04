#!/usr/bin/env python3
"""Test CI-safe del campionamento stratificato (Fase 0.1a).

Testa la sola logica **pura** di bucketing/sampling (nessun corpus, nessun GNN,
nessuna rete). La scansione reale è coperta dal run dell'agente.
"""

from __future__ import annotations

import os
import random
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "tools")
for _p in (_ROOT, _TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import e2e_sample  # noqa: E402


class SampleBucketsTests(unittest.TestCase):
    def test_picks_per_class_and_groups_by_corpus(self):
        buckets = {
            "prosa": [
                {"pdf": "corpus1/a.pdf", "page": 1, "layout_class": "prosa"},
                {"pdf": "corpus2/b.pdf", "page": 2, "layout_class": "prosa"},
                {"pdf": "corpus3/c.pdf", "page": 3, "layout_class": "prosa"},
            ],
            "tabelle": [
                {"pdf": "corpus1/d.pdf", "page": 4, "layout_class": "tabelle"},
            ],
        }
        sample, classes = e2e_sample._sample_buckets(buckets, 2, random.Random(1))
        self.assertEqual(classes["prosa"], 2)
        self.assertEqual(classes["tabelle"], 1)
        self.assertEqual(classes["indici"], 0)
        # raggruppati per corpus
        self.assertEqual(set(sample), set(e2e_sample._CORPORA))
        all_pdfs = [it["pdf"] for v in sample.values() for it in v]
        self.assertEqual(len(all_pdfs), 3)

    def test_shortage_is_ok(self):
        sample, classes = e2e_sample._sample_buckets({}, 8, random.Random(1))
        self.assertTrue(all(v == 0 for v in classes.values()))
        self.assertEqual(sum(len(v) for v in sample.values()), 0)

    def test_deterministic_with_same_seed(self):
        buckets = {"prosa": [
            {"pdf": f"corpus1/p{i}.pdf", "page": i, "layout_class": "prosa"}
            for i in range(10)]}
        a, _ = e2e_sample._sample_buckets(buckets, 3, random.Random(7))
        b, _ = e2e_sample._sample_buckets(buckets, 3, random.Random(7))
        self.assertEqual(a, b)

    def test_classes_are_the_six_of_the_plan(self):
        self.assertEqual(len(e2e_sample._LAYOUT_CLASSES), 6)
        self.assertIn("colonne/box-liste", e2e_sample._LAYOUT_CLASSES)


if __name__ == "__main__":
    unittest.main()
