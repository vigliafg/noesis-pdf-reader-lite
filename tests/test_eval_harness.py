"""Tests for the evaluation harness helpers (``experimental/eval/evalutil.py``).

Pure and dependency-light: no pymupdf, no network, no external PDFs. Covers the
corpus loading/validation and the automatic text metrics.

Run with (from the project root):

    .venv/bin/python -m unittest discover -s tests -v
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EVAL = os.path.join(_ROOT, "experimental", "eval")
if _EVAL not in sys.path:
    sys.path.insert(0, _EVAL)

import evalutil  # noqa: E402


class CorpusTests(unittest.TestCase):
    def test_default_corpus_has_two_ha22_bunches(self):
        bunches = evalutil.default_corpus()["bunches"]
        by_id = {b["id"]: b for b in bunches}
        self.assertIn("A", by_id)
        self.assertIn("B", by_id)
        self.assertEqual(by_id["A"]["pages"], list(range(1230, 1241)))
        self.assertEqual(by_id["B"]["pages"], list(range(2230, 2241)))
        for b in bunches:
            self.assertEqual(len(b["pages"]), 11)

    def test_load_corpus_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.json"
            path.write_text(json.dumps({
                "seed": 1,
                "bunches": [{"id": "ce24_r1", "pdf": "/x/ce24.pdf",
                             "pages": [301, 500, 999], "origin": "random"}],
            }), encoding="utf-8")
            corpus = evalutil.load_corpus(path)
            self.assertEqual([b["id"] for b in corpus["bunches"]], ["ce24_r1"])
            self.assertEqual(corpus["bunches"][0]["pages"], [301, 500, 999])

    def test_load_corpus_falls_back_to_default_on_bad_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nope.json"
            self.assertEqual(
                len(evalutil.load_corpus(path)["bunches"]),
                len(evalutil.default_corpus()["bunches"]),
            )


class TextMetricsTests(unittest.TestCase):
    def test_empty_text(self):
        m = evalutil.text_metrics("")
        self.assertEqual(m["chars"], 0)
        self.assertEqual(m["words"], 0)
        self.assertEqual(m["lines"], 0)

    def test_counts_replacement_chars(self):
        self.assertEqual(evalutil.text_metrics("abc \ufffd\ufffd def")["replacement_chars"], 2)

    def test_counts_table_rows_and_images_and_headings(self):
        text = (
            "# Titolo\n\n"
            "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n"
            "![fig](x.png)\n"
        )
        m = evalutil.text_metrics(text)
        self.assertEqual(m["headings"], 1)
        self.assertEqual(m["table_rows"], 3)
        self.assertEqual(m["images"], 1)
        self.assertGreater(m["words"], 0)


if __name__ == "__main__":
    unittest.main()
