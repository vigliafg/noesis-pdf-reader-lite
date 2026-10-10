"""Test del runner del harness (``tools/harness.py``) — nessuna rete, veloce.

Verificano il *contratto* del runner: elenco layer, esecuzione di un modulo
unittest con report scritto, e il writer del report. Il layer ``unit`` completo
è già coperto dalla suite stessa; qui non lo si rilancia.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = _ROOT / "tools"
for _p in (str(_ROOT), str(_TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_OK = False
_ERR = ""
try:
    import harness  # noqa: E402

    _OK = True
except Exception as e:  # noqa: BLE001
    _ERR = repr(e)


@unittest.skipUnless(_OK, f"harness non importabile: {_ERR}")
class HarnessCliTests(unittest.TestCase):
    def test_list_returns_zero(self):
        self.assertEqual(harness.main(["--list"]), 0)
        self.assertIn("unit", harness._LAYERS)
        self.assertIn("pipeline", harness._LAYERS)

    def test_module_run_writes_report(self):
        with tempfile.TemporaryDirectory() as d:
            rc = harness.main(
                ["--module", "tests.test_pages", "--out", d])
            self.assertEqual(rc, 0)
            report = Path(d) / "harness_report.json"
            self.assertTrue(report.exists())
            payload = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(payload["overall"], "pass")
            self.assertEqual(payload["layers"][0]["name"], "unit")
            self.assertEqual(payload["layers"][0]["status"], "pass")
            self.assertTrue((Path(d) / "harness_report.md").exists())

    def test_report_writer_marks_failures(self):
        results = [
            harness.LayerResult(name="unit", status="pass", detail="ok"),
            harness.LayerResult(name="pipeline", status="fail",
                                detail="boom", output_tail="trace"),
        ]

        class _Args:
            quick = False
            module = None
            corpus = False
            no_visual = True
            timeout = 1.0
            out = "x"

        with tempfile.TemporaryDirectory() as d:
            md = harness.write_report(results, Path(d), _Args())
            text = md.read_text(encoding="utf-8")
            self.assertIn("FAIL", text)
            self.assertIn("pipeline", text)


if __name__ == "__main__":
    unittest.main()
