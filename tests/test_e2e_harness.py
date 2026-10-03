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

    def test_strip_base64_for_prompt(self):
        md = ("prima ![FIG 1](data:image/jpeg;base64,AAAA////) dopo "
              "e grezzo data:image/png;base64,BBBB fine")
        out = e2e._strip_base64_for_prompt(md)
        self.assertNotIn("base64", out)
        self.assertIn("prima", out)
        self.assertIn("dopo", out)
        self.assertIn("[FIGURA: FIG 1]", out)
        self.assertIn("fine", out)

    def test_collect_defects_structured(self):
        rec = {
            "pdf": "x.pdf", "page_idx": 0, "page_ui": 1, "pipeline": "ir",
            "engine": "ir", "flags": ["tables: celle recall 0.46"],
            "arbitration": {"verdict": {
                "text_ok": False, "order_ok": False, "figures_ok": False,
                "tables_ok": True,
                "defects": [
                    {"kind": "figure_duplicate", "severity": "high",
                     "note": "2 marker per 1 figura"},
                    {"kind": "marginalia", "severity": "low",
                     "note": "numero pagina"},
                    {"kind": "boh", "severity": "medium", "note": "sconosciuto"},
                ],
                "notes": "",
            }},
        }
        defs = e2e._collect_defects([rec])
        kinds = {d["kind"] for d in defs}
        self.assertIn("table_content", kinds)       # auto -> tassonomia
        self.assertIn("figure_duplicate", kinds)
        self.assertIn("marginalia", kinds)
        self.assertIn("other", kinds)               # kind ignoto normalizzato
        marg = [d for d in defs if d["kind"] == "marginalia"]
        self.assertTrue(marg and marg[0]["real"] is False)
        self.assertTrue(all(d["kind"] != "marginalia"
                            for d in defs if d["real"]))

    def test_collect_defects_fallback_booleans(self):
        rec = {
            "pdf": "x.pdf", "page_idx": 0, "page_ui": 1, "pipeline": "ir",
            "engine": "ir", "flags": [],
            "arbitration": {"verdict": {
                "text_ok": False, "order_ok": True, "figures_ok": True,
                "tables_ok": False, "missing": ["Fig 3"], "notes": "x",
            }},
        }
        kinds = {d["kind"] for d in e2e._collect_defects([rec])}
        self.assertIn("text_missing", kinds)
        self.assertIn("table_structure", kinds)
        self.assertIn("other", kinds)

    def test_collect_defects_ignores_advisor_error(self):
        rec = {
            "pdf": "x.pdf", "page_idx": 0, "page_ui": 1, "pipeline": "ir",
            "flags": [], "arbitration": {"verdict": {"error": "HTTP 401"}},
        }
        self.assertEqual(e2e._collect_defects([rec]), [])


@unittest.skipUnless(_OK, "pymupdf/e2e non disponibili")
class EngineAttributionTests(unittest.TestCase):
    """`_engine_used` dice quale pipeline ha prodotto il testo (ir|current)."""

    def _index_pdf(self, path: Path) -> None:
        doc = pymupdf.open()
        p = doc.new_page(width=595, height=842)
        for i in range(25):
            p.insert_text((50, 40 + i * 20), f"term {i}, {100 + i}", fontsize=9)
        doc.save(str(path))
        doc.close()

    def test_engine_ir_on_prose(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "s.pdf"
            _synthetic_pdf(pdf)
            engine, ok = e2e._engine_used(str(pdf), 0, Path(d) / "fig")
            self.assertEqual(engine, "ir")
            self.assertTrue(ok)

    def test_engine_current_on_index(self):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "idx.pdf"
            self._index_pdf(pdf)
            engine, _ok = e2e._engine_used(str(pdf), 0, Path(d) / "fig")
            self.assertEqual(engine, "current")

    def test_make_record_engine_defaults_to_pipe(self):
        truth = {"elements": [], "page_width": 595.0, "pdf_text": "",
                 "ntexty": 0, "expected_figs": 0, "table_text": "", "is_index": False}
        rec = e2e._make_record("x.pdf", 0, "ir", truth, "text", "text",
                               1.0, None, False, "auto")
        self.assertEqual(rec["engine"], "ir")

    def test_collect_defects_has_engine(self):
        rec = {"pdf": "x.pdf", "page_idx": 0, "page_ui": 1, "pipeline": "ir",
               "engine": "current", "flags": ["text: recall 0.80"],
               "arbitration": None}
        defs = e2e._collect_defects([rec])
        self.assertEqual(defs[0]["engine"], "current")


@unittest.skipUnless(_OK, "pymupdf/e2e non disponibili")
class AdvisorAllPagesTests(unittest.TestCase):
    """L'advisor giudica **tutte** le pagine richieste (non solo le flaggate)."""

    def _recs(self, n: int) -> list[dict]:
        return [
            {"pdf": "x.pdf", "page_idx": i, "page_ui": i + 1, "pipeline": "ir",
             "flags": ["text: recall 0.80"] if i == 0 else []}
            for i in range(n)
        ]

    def test_all_pages_and_cap(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            doc = pymupdf.open()
            for _ in range(3):
                doc.new_page()
            verdict = {"text_ok": True, "order_ok": True, "figures_ok": True,
                       "tables_ok": True, "missing": [], "notes": ""}
            with mock.patch.object(e2e, "_advisor_judge", return_value=verdict) as m:
                e2e._run_advisor(Path(d), self._recs(3), doc, "m", "k", 0)
                self.assertEqual(m.call_count, 3)  # 0 = tutte
                m.reset_mock()
                e2e._run_advisor(Path(d), self._recs(3), doc, "m", "k", 1)
                self.assertEqual(m.call_count, 1)  # cap
            doc.close()


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

    def test_retries_on_429(self):
        calls = {"n": 0}

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                calls["n"] += 1
                if calls["n"] < 3:  # primi due tentativi: rate limited
                    body = b'{"error":{"message":"rate limited"}}'
                    self.send_response(429)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                content = ('{"text_ok": true, "order_ok": true, '
                           '"figures_ok": true, "tables_ok": true, '
                           '"missing": [], "notes": ""}')
                body = json.dumps(
                    {"choices": [{"message": {"content": content}}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                pass

        srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{srv.server_address[1]}/chat"
            verdict = e2e._advisor_judge(
                b"x", "md", "m", "k", url=url, retries=3, base_delay=0.01)
            self.assertTrue(verdict["text_ok"])
            self.assertEqual(calls["n"], 3)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
