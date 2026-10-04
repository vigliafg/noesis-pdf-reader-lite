#!/usr/bin/env python3
"""Golden set di layout — **CI-safe** (Fase 0.1c).

Per ogni pagina in ``tests/data/golden/*.json`` ricalcola struttura e proxy dai
``page_boxes`` e verifica che **non regrediscano** oltre tolleranza rispetto al
golden (che è la fotografia accettata del comportamento attuale). Nessuna rete,
nessun advisor: l'arbitraggio (Fase 0.1b) aggiunge etichette di qualità in un
secondo momento.

Le pagine il cui PDF è assente vengono **saltate** (CI senza corpus).
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_GOLDEN = _ROOT / "tests" / "data" / "golden"

#: tolleranze di regressione (proxy e confidenza)
TOL_SCORE = 0.15
TOL_CONF = 0.20

try:
    import pymupdf  # noqa: E402

    import ir_layout  # noqa: E402
    import layout_proxies as lp  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)


def _load_pages() -> list[dict]:
    pages: list[dict] = []
    if not _GOLDEN.exists():
        return pages
    for path in sorted(_GOLDEN.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for p in data.get("pages", []):
            pages.append({**p, "_golden": path.name})
    return pages


@unittest.skipUnless(_OK, "pymupdf/ir_layout non disponibili")
class GoldenLayoutTests(unittest.TestCase):
    def test_golden_pages_no_regression(self):
        pages = _load_pages()
        if not pages:
            self.skipTest("nessun file golden")
        checked = 0
        missing = 0
        for p in pages:
            pdf = _ROOT / p["pdf"]
            if not pdf.exists():
                missing += 1
                continue
            with self.subTest(pdf=p["pdf"], page=p["page_ui"]):
                self._check_page(p)
            checked += 1
        if checked == 0:
            self.skipTest(f"corpus assente ({missing} pagine)")
        self.assertGreater(checked, 0)

    def _check_page(self, p: dict) -> None:
        doc = pymupdf.open(_ROOT / p["pdf"])
        try:
            idx = p["page"]
            self.assertLess(idx, len(doc), "pagina fuori range")
            page = doc[idx]
            chunk = ir_layout.page_chunk(doc, idx)
            _text, els = ir_layout._elements_from_chunk(chunk)
            prox = lp.all_proxies(els, page.rect.width)
            # l'IR deve produrre markdown non vuoto
            md = ir_layout.build_markdown(page, doc, idx, embed_figures=False,
                                          chunk=chunk)
        finally:
            doc.close()

        # struttura
        classes = {e.get("class") for e in els}
        st = p["structure"]
        self.assertEqual("table" in classes, st["has_table"], "tabella")
        self.assertEqual("picture" in classes, st["has_picture"], "figure")
        self.assertEqual("formula" in classes, st["has_formula"], "formula")
        self.assertEqual(prox["order"]["metrics"]["n_columns"], st["n_columns"],
                         "numero colonne")
        # classe di layout
        self.assertEqual(prox["layout_class"], p["layout_class"], "layout_class")
        # proxy: non peggiorare oltre tolleranza
        for k, expected in p["proxies"].items():
            if k == "confidence":
                self.assertGreaterEqual(prox["confidence"], expected - TOL_CONF,
                                        f"confidence {expected}->{prox['confidence']}")
            else:
                self.assertGreaterEqual(prox[k]["score"], expected - TOL_SCORE,
                                        f"proxy {k} {expected}->{prox[k]['score']}")
        self.assertTrue(md.strip(), "markdown IR vuoto")


if __name__ == "__main__":
    unittest.main()
