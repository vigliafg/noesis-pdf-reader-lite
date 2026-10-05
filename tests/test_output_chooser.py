#!/usr/bin/env python3
"""Test del chooser IR vs `current` (`main._choose_output` / `_select_page_output`).

Il gate IR è solo-recall e non credita le figure: su pagine con figure reali
l'IR può perdere un po' di recall (il testo finisce *dentro* l'immagine) e
veniva scartato in favore di `current`, che però **non incorpora le figure**.
Il chooser preferisce IR quando porta più figure senza perdere troppo testo.

CI-safe: il caso sintetico non richiede corpus; il caso reale salta se assente.
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
    import pymupdf  # noqa: E402

    import main  # noqa: E402
    _OK = True
except Exception as e:  # noqa: BLE001
    _OK = False
    _ERR = repr(e)

# 10 parole tutte ≥6 caratteri (il recall usa minlen 6).
_WORDS = ["medicine", "cardiology", "patients", "treatment", "diagnosis",
          "hospital", "clinical", "chronic", "disease", "therapy"]


@unittest.skipUnless(_OK, "pymupdf/main non disponibili")
class ChooserTests(unittest.TestCase):
    def _page(self, text: str):
        doc = pymupdf.open()
        page = doc.new_page(width=420, height=300)
        page.insert_textbox(pymupdf.Rect(10, 10, 410, 290), text, fontsize=9)
        return doc, page

    def test_figure_wins_within_slack(self):
        # IR: 9/10 parole + una figura; current: 10/10 senza figure -> IR
        doc, page = self._page(" ".join(_WORDS))
        ir = " ".join(_WORDS[:9]) + "\n\n![f](data:image/jpeg;base64,AAAA)"
        cur = " ".join(_WORDS)
        self.assertIs(main._choose_output(page, [], ir, cur), ir)
        doc.close()

    def test_clearly_worse_ir_keeps_current(self):
        # IR: 5/10 parole e nessuna figura -> resta current (mai peggio)
        doc, page = self._page(" ".join(_WORDS))
        ir = " ".join(_WORDS[:5])
        cur = " ".join(_WORDS)
        self.assertIs(main._choose_output(page, [], ir, cur), cur)
        doc.close()

    def test_better_recall_wins(self):
        # IR: 10/10, current: 8/10 (entrambi senza figure) -> IR
        doc, page = self._page(" ".join(_WORDS))
        ir = " ".join(_WORDS)
        cur = " ".join(_WORDS[:8])
        self.assertIs(main._choose_output(page, [], ir, cur), ir)
        doc.close()

    def test_figure_page_prefers_ir(self):
        # pagina di sole figure (nessuna prosa) -> IR
        doc, page = self._page("")
        ir = "![f](data:image/jpeg;base64,AAAA)"
        cur = ""
        self.assertIs(main._choose_output(page, [], ir, cur), ir)
        doc.close()


@unittest.skipUnless(_OK, "pymupdf/main non disponibili")
class FallbackPagesUseIrTests(unittest.TestCase):
    """Le pagine che prima ricadevano su `current` ora usano **IR** (con figure).

    Casi del run C: figure mancanti nel fallback (`ce24 p187`, `fe23 p385`,
    `ox2 p309`) e perdita di prosa su pagine formula (`arxiv …38133 p16`,
    `arxiv …37412 p39`).
    """

    _CASES = [
        ("corpus1/ce24.pdf", 186, 1),            # almeno 1 figura attesa
        ("corpus2/fe23.pdf", 384, 1),
        ("corpus2/ox2.pdf", 308, 1),
    ]

    def test_fallback_pages_now_ir(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            for rel, idx, minfig in self._CASES:
                pdf = _ROOT / rel
                if not pdf.exists():
                    self.skipTest(f"{rel} assente")
                _text, engine, _raw = main._select_page_output(
                    str(pdf), idx, figures_dir=Path(d))
                self.assertEqual(engine, "ir", f"{rel} p{idx+1}")


if __name__ == "__main__":
    unittest.main()
