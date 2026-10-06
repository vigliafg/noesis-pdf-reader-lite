#!/usr/bin/env python3
"""Golden Rule #1 — fedeltà del **flusso di lettura** (vedi ``REGOLE-TEST.md``).

Contenuto E ordine: un md con le colonne intrecciate è un difetto GRAVE a
prescindere dal recall. Qui si testa il rilevatore ``vp._flow_score`` (metrica
LIS/assegnazione crescente) e il rilevatore colonne robusto.

CI-safe: i test sintetici non usano il corpus; il caso guida salta se assente.
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import verify_pages as vp  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)

try:
    import ir_layout  # noqa: E402
    import pymupdf  # noqa: E402
    _IR = True
except Exception as e:  # noqa: BLE001
    _IR = False
    _IR_ERR = repr(e)

_PDF = _ROOT / "corpus1" / "ha22.pdf"
_PAGE = 102  # pagina 103 (stampata 62): figura a 1,5 colonne -> intreccio


def _el(cls: str, x0: float, y0: float, x1: float, y1: float, text: str) -> dict:
    return {"class": cls, "bbox": (x0, y0, x1, y1), "w": x1 - x0, "text": text}


# due colonne di prosa (page width 600): sinistra x 50-290, destra x 320-560
_L1 = "Alpha beta gamma delta epsilon zeta left one"
_L2 = "Eta theta iota kappa lambda mu left two"
_R1 = "Nu xi omicron pi rho sigma right one"
_R2 = "Tau upsilon phi chi psi omega right two"


def _two_col_elements(bridge: bool = False) -> list[dict]:
    els = [
        _el("text", 50, 50, 290, 120, _L1),
        _el("text", 50, 200, 290, 270, _L2),
        _el("text", 320, 50, 560, 120, _R1),
        _el("text", 320, 200, 560, 270, _R2),
    ]
    if bridge:  # blocco a ponte che attraversa il gutter
        els.append(_el("text", 50, 320, 350, 330,
                       "bridge block spanning the gutter here now"))
    return els


@unittest.skipUnless(_OK, "verify_pages non disponibile")
class FlowMetricTests(unittest.TestCase):
    def test_correct_column_major_scores_one(self):
        md = " ".join([_L1, _L2, _R1, _R2])
        fi = vp._flow_score(md, _two_col_elements(), 600.0)
        self.assertEqual(fi["flow"], 1.0)

    def test_interleaved_columns_scores_low(self):
        md = " ".join([_L1, _R1, _L2, _R2])  # colonne intrecciate
        fi = vp._flow_score(md, _two_col_elements(), 600.0)
        self.assertLess(fi["flow"], 0.95)

    def test_bridging_block_does_not_hide_columns(self):
        splits = vp._column_splits_robust(
            [{"x0": e["bbox"][0], "x1": e["bbox"][2],
              "y0": e["bbox"][1], "y1": e["bbox"][3]}
             for e in _two_col_elements(bridge=True)], 600.0)
        self.assertTrue(splits, "il blocco a ponte non deve nascondere le colonne")

    def test_single_column_is_neutral(self):
        els = [_el("text", 50, 50, 550, 120, _L1),
               _el("text", 50, 200, 550, 270, _L2)]
        fi = vp._flow_score(" ".join([_L1, _L2]), els, 600.0)
        self.assertEqual(fi["flow"], 1.0)


@unittest.skipUnless(_IR, "pymupdf/ir_layout non disponibili")
class GuidingCaseTests(unittest.TestCase):
    """Caso guida p103: il rilevatore DEVE segnalare l'intreccio.

    Nota: dopo il fix del motore (``build_markdown`` -> ordine colonna-major)
    questo test va aggiornato a ``flow >= 0.95``.
    """

    def test_p103_flow_detected(self):
        if not _PDF.exists():
            self.skipTest("corpus1/ha22.pdf assente")
        doc = pymupdf.open(_PDF)
        try:
            chunk = ir_layout.page_chunk(doc, _PAGE)
            md = ir_layout.build_markdown(doc[_PAGE], doc, _PAGE,
                                          embed_figures=False, chunk=chunk)
            _t, els = ir_layout.page_elements(doc, _PAGE)
            fi = vp._flow_score(md, els, doc[_PAGE].rect.width)
        finally:
            doc.close()
        self.assertLess(fi["flow"], 0.95,
                        f"p103: intreccio non rilevato (flow={fi['flow']})")


if __name__ == "__main__":
    unittest.main()
