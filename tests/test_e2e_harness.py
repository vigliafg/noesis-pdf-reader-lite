"""Test CI-safe dell'harness E2E (``tools/e2e.py``).

Coprono il *contratto* dell'harness senza corpus e **senza rete**:

- il CLI ``--mode auto`` su una pagina **sintetica** produce ``report.jsonl``
  (una voce per pipeline) e ``summary.md``;
- ``--mode visual`` produce la checklist ``review/index.md`` per l'arbitro;
- i controlli per tipo e i flag funzionano sui casi noti;
- ``_advisor_judge`` parla con un **mock HTTP locale** (nessuna rete esterna).

Le gold su corpus reale restano fuori (skippano se il corpus è assente).
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
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


def _synthetic_pdf(path: Path) -> None:
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((50, 30), "CHAPTER 18 Endocrine System", fontsize=9)
    p.insert_textbox(
        pymupdf.Rect(50, 120, 280, 360),
        "They increase with a reduction in obesity related risk factors and the "
        "detection of diabetes improves outcomes for patients.",
        fontsize=10,
    )
    p.insert_textbox(
        pymupdf.Rect(320, 120, 550, 360),
        "Right column opening sentence with several words that must stay separate.",
        fontsize=10,
    )
    doc.save(str(path))
    doc.close()


@unittest.skipUnless(_OK, "pymupdf/e2e non disponibili")
class HarnessCliTests(unittest.TestCase):
    def _run(self, pdf: Path, out: Path, *extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        return subprocess.run(
            [sys.executable, os.path.join(_TOOLS, "e2e.py"), str(pdf),
             "--pages", "0", "--out", str(out), *extra],
            env=env, capture_output=True, text=True, timeout=300,
        )

    def test_auto_direct_writes_report(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "s.pdf"
            _synthetic_pdf(pdf)
            out = Path(d) / "out"
            r = self._run(pdf, out, "--pipelines", "ir,current")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            recs = [
                json.loads(ln)
                for ln in (out / "report.jsonl").read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            self.assertEqual({x["pipeline"] for x in recs}, {"ir", "current"})
            for x in recs:
                self.assertGreater(x["body_len"], 0)
                self.assertIn("checks", x)
                self.assertIn("flags", x)
                self.assertIn(x["verdict"], ("auto-ok", "review"))
            self.assertTrue((out / "summary.md").exists())

    def test_visual_writes_review_index(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "s.pdf"
            _synthetic_pdf(pdf)
            out = Path(d) / "out"
            r = self._run(pdf, out, "--pipelines", "ir", "--mode", "visual")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertTrue((out / "review" / "index.md").exists())


@unittest.skipUnless(_OK, "e2e non disponibile")
class ChecksTests(unittest.TestCase):
    def _truth(self, **kw) -> dict:
        base = {"elements": [], "page_width": 595.0, "pdf_text": "",
                "ntexty": 0, "expected_figs": 0, "table_text": "",
                "is_index": False}
        base.update(kw)
        return base

    def test_text_recall_flag(self):
        truth = self._truth(
            pdf_text="medicine cardiology patients treatment diagnosis",
            ntexty=5,
        )
        checks = e2e._checks(truth, "medicine", "")
        self.assertFalse(checks["text"]["ok"])
        self.assertTrue(e2e._flags(checks, None))

    def test_figure_missing_flag(self):
        truth = self._truth(expected_figs=2)
        checks = e2e._checks(truth, "some prose without figures", "some prose")
        self.assertFalse(checks["figures"]["ok"])
        self.assertIn("figures", " ".join(e2e._flags(checks, None)))

    def test_table_recall_flag(self):
        truth = self._truth(
            table_text="alpha beta gamma delta epsilon zeta",
        )
        checks = e2e._checks(truth, "alpha", "alpha")
        self.assertFalse(checks["tables"]["ok"])

    def test_clean_page_has_no_flags(self):
        truth = self._truth(
            pdf_text="medicine cardiology patients treatment diagnosis",
            ntexty=5,
        )
        checks = e2e._checks(
            truth, "medicine cardiology patients treatment diagnosis", "same")
        self.assertEqual(e2e._flags(checks, None), [])


@unittest.skipUnless(_OK, "e2e non disponibile")
class AdvisorJudgeTests(unittest.TestCase):
    def test_parses_json_from_mock_server(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                content = (
                    'Ecco il verdetto: {"text_ok": true, "order_ok": false, '
                    '"figures_ok": true, "tables_ok": true, "missing": [], '
                    '"notes": "ordine da rivedere"}'
                )
                body = json.dumps(
                    {"choices": [{"message": {"content": content}}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):  # silenzia il log
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/chat"
            verdict = e2e._advisor_judge(b"\x89PNG\r\n", "markdown", "m", "k", url=url)
            self.assertTrue(verdict["text_ok"])
            self.assertFalse(verdict["order_ok"])
            self.assertEqual(verdict["notes"], "ordine da rivedere")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
