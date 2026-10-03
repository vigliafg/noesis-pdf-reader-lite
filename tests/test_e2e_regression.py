"""Regressione del harness E2E (P2d).

Due livelli:

- **meccanica** (CI-safe, nessun corpus): ``--update-baseline`` scrive il file e
  ``--baseline`` non segnala regressioni su una pagina sintetica; l'unit di
  ``_compare_baseline`` rileva un peggioramento forzato.
- **corpus** (gold): sulle pagine **pinned** in ``tests/data/e2e_baseline.json``,
  il run reale non deve regredire. **Skippa** se il corpus è assente (CI).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "tools")
for _p in (_ROOT, _TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import pymupdf  # noqa: E402

    import e2e  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)

_BASELINE = Path(_ROOT) / "tests" / "data" / "e2e_baseline.json"

# pagine pinned del corpus reale (vedi tests/data/e2e_baseline.json)
_CORPUS_PAGES = [
    ("corpus1/ha22.pdf", "100"),
    ("corpus2/fe22.pdf", "4446"),
    ("corpus3/arxiv_2609.33753.pdf", "11"),
]


def _synthetic_pdf(path: Path) -> None:
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_textbox(
        pymupdf.Rect(50, 120, 280, 360),
        "They increase with a reduction in obesity related risk factors and the "
        "detection of diabetes improves outcomes for patients.",
        fontsize=10,
    )
    doc.save(str(path))
    doc.close()


def _run(pdf: Path, out: Path, pages: str = "0",
         *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run(
        [sys.executable, os.path.join(_TOOLS, "e2e.py"), str(pdf),
         "--pages", pages, "--out", str(out), *extra],
        env=env, capture_output=True, text=True, timeout=300,
    )


@unittest.skipUnless(_OK, "pymupdf/e2e non disponibili")
class BaselineMechanicsTests(unittest.TestCase):
    def test_update_then_compare_no_regression(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "s.pdf"
            _synthetic_pdf(pdf)
            bl = Path(d) / "bl.json"
            r1 = _run(pdf, Path(d) / "out1", "0", "--update-baseline", str(bl))
            self.assertEqual(r1.returncode, 0, r1.stderr[-2000:])
            self.assertTrue(bl.exists())
            entries = json.loads(bl.read_text(encoding="utf-8"))["entries"]
            self.assertEqual(len(entries), 1)
            r2 = _run(pdf, Path(d) / "out2", "0", "--baseline", str(bl))
            self.assertEqual(r2.returncode, 0, r2.stderr[-2000:])
            self.assertIn("nessuna regressione", r2.stdout)

    def test_compare_detects_degradation(self):
        rec = {
            "pdf": "x.pdf", "pipeline": "ir", "page_idx": 0,
            "order_score": 1.0, "body_len": 1000,
            "checks": {
                "text": {"recall_pdf": 0.95},
                "figures": {"embedded": 1},
                "tables": {"recall": None},
            },
        }
        entries = {
            "x.pdf|ir|0": {
                "recall_pdf": 1.0, "order_score": 1.0, "body_len": 1000,
                "figures": 1, "table_recall": None,
            },
        }
        regs = e2e._compare_baseline([rec], entries)
        self.assertEqual(len(regs), 1)
        self.assertTrue(any("recall" in i for i in regs[0]["issues"]))


@unittest.skipUnless(_OK, "pymupdf/e2e non disponibili")
class CorpusRegressionTests(unittest.TestCase):
    def test_pinned_pages_no_regression(self):
        missing = [p for p, _ in _CORPUS_PAGES
                   if not (Path(_ROOT) / p).exists()]
        if missing or not _BASELINE.exists():
            self.skipTest(f"corpus/baseline assenti: {missing or _BASELINE}")
        with tempfile.TemporaryDirectory() as d:
            for rel, pages in _CORPUS_PAGES:
                r = _run(Path(_ROOT) / rel, Path(d) / Path(rel).stem, pages,
                         "--baseline", str(_BASELINE))
                self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1000:])


if __name__ == "__main__":
    unittest.main()
