"""Cache di estrazione: auto-invalidazione per revisione + reset manuale.

Un fix del motore deve propagarsi: la cache su disco salva anche il **markdown
finale** e, senza invalidazione, l'utente rivedrebbe l'output vecchio. Qui si
coprono:

- la **revisione** di cache (_CACHE_REVISION): al load i ``final`` con revisione
  diversa vengono scartati, i ``raw`` restano;
- ``_purge_page_cache``: svuota una sola pagina (raw/final/figure/traduzione);
- ``_regenerate_page``: svuota la pagina corrente e rilancia l'estrazione;
- ``_clear_document_cache``: svuota tutto il documento (con conferma).

Headless e con XDG isolato: nessun tocco ai dati reali. PDF sintetico.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_TMP = tempfile.mkdtemp(prefix="noesis_cache_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(_TMP, "cache")

_IMP_OK = False
_IMP_ERR = ""
try:
    import pymupdf  # noqa: E402
    from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

    _app = QApplication.instance() or QApplication([])
    import main  # noqa: E402

    _IMP_OK = True
except Exception as e:  # noqa: BLE001
    _IMP_ERR = repr(e)


def _wait(pred, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        _app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    _app.processEvents()
    return bool(pred())


def _make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for i in range(2):
        p = doc.new_page(width=612, height=792)
        p.insert_text((72, 80), f"Page {i + 1}", fontsize=20)
        p.insert_text((72, 140), "Some body text for extraction.", fontsize=11)
    doc.save(str(path))
    doc.close()


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
class CacheRevisionTests(unittest.TestCase):
    def test_old_revision_drops_finals_keeps_raw(self):
        w = main.MainWindow()
        f = Path(_TMP) / "rev.json"
        w._doc_fingerprint = "FP"
        w._extraction_cache_file = f
        f.write_text(json.dumps({
            "revision": 0, "fingerprint": "FP",
            "pages": {"5": {"eng": {"raw": "RAW", "final": ["OLD", "auto", 0.0]}}},
        }), encoding="utf-8")
        w._load_extraction_cache()
        self.assertEqual(w._extraction_cache.get((5, "eng")), "RAW")  # raw tenuto
        self.assertNotIn((5, "eng", "()"), w._final_text_cache)       # final scartato
        w.close()

    def test_current_revision_loads_finals(self):
        w = main.MainWindow()
        f = Path(_TMP) / "rev2.json"
        w._doc_fingerprint = "FP"
        w._extraction_cache_file = f
        f.write_text(json.dumps({
            "revision": main._CACHE_REVISION, "fingerprint": "FP",
            "pages": {"5": {"eng": {"raw": "RAW", "final": ["NEW", "auto", 0.0]}}},
        }), encoding="utf-8")
        w._load_extraction_cache()
        self.assertEqual(w._final_text_cache[(5, "eng", "()")][0], "NEW")
        w.close()

    def test_save_writes_revision(self):
        w = main.MainWindow()
        f = Path(_TMP) / "rev3.json"
        w._doc_fingerprint = "FP"
        w._extraction_cache_file = f
        w._extraction_cache[(5, "eng")] = "RAW"
        w._final_text_cache[(5, "eng", "()")] = ("T", "auto", 1.0)
        w._save_extraction_cache()
        data = json.loads(f.read_text(encoding="utf-8"))
        self.assertEqual(data.get("revision"), main._CACHE_REVISION)
        w.close()


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
class CacheResetTests(unittest.TestCase):
    def setUp(self):
        self._pdf = Path(_TMP) / "reset.pdf"
        if not self._pdf.exists():
            _make_pdf(self._pdf)

    def test_purge_clears_only_that_page(self):
        w = main.MainWindow()
        w._extraction_cache[(0, "eng")] = "r0"
        w._extraction_cache[(1, "eng")] = "r1"
        w._final_text_cache[(0, "eng", "()")] = ("A", "auto", 0.0)
        w._final_text_cache[(1, "eng", "()")] = ("B", "auto", 0.0)
        w._page_images = {0: ["x"], 1: ["y"]}
        w._current_page = 0
        w._current_images = w._page_images[0]
        w._purge_page_cache(0)
        self.assertNotIn((0, "eng"), w._extraction_cache)
        self.assertNotIn((0, "eng", "()"), w._final_text_cache)
        self.assertIn((1, "eng", "()"), w._final_text_cache)
        # la pagina corrente viene ricreata vuota (serve a _current_images)
        self.assertEqual(w._page_images.get(0), [])
        self.assertEqual(w._current_images, [])
        self.assertEqual(w._page_images.get(1), ["y"])
        w.close()

    def test_regenerate_reruns_current_page(self):
        w = main.MainWindow()
        w._resume_last_page = False
        w._open_pdf(self._pdf)
        lang = main._tess_lang_code(main.get_source_lang())
        self.assertTrue(_wait(lambda: (0, lang, "()") in w._final_text_cache),
                        "estrazione pagina 0 non completata")
        # sentinella "corrotta": dopo Rigenera non deve restare
        w._current_page = 0
        w._final_text_cache[(0, lang, "()")] = ("CORROTTO", "auto", 0.0)
        w._extraction_cache[(0, lang)] = "stale"
        w._last_result = ("CORROTTO", "auto", 0.0)
        w._regenerate_page()
        ok = _wait(lambda: w._final_text_cache.get((0, lang, "()"), ("",))[0] != "CORROTTO")
        self.assertTrue(ok, "Rigenera non ha rieseguito l'estrazione")
        w.close()

    def test_cache_diagnostics_lists_file_and_revision(self):
        w = main.MainWindow()
        w._resume_last_page = False
        w._open_pdf(self._pdf)
        txt = w._cache_diagnostics()
        self.assertIn("cache file", txt)
        self.assertIn(str(w._extraction_cache_file), txt)
        self.assertIn(f"revision attesa:{main._CACHE_REVISION}", txt)
        self.assertIn("pagina:", txt)
        w.close()

    def test_cache_info_dialog_builds(self):
        w = main.MainWindow()
        w._resume_last_page = False
        w._open_pdf(self._pdf)
        orig = main.QDialog.exec
        main.QDialog.exec = lambda self, *a, **k: 0  # non bloccare
        try:
            w._show_cache_info()
        finally:
            main.QDialog.exec = orig
        w.close()

    def test_clear_document_empties_caches(self):
        w = main.MainWindow()
        w._resume_last_page = False
        w._open_pdf(self._pdf)
        _wait(lambda: len(w._final_text_cache) >= 1)
        w._final_text_cache[(9, "eng", "()")] = ("t", "auto", 0.0)
        w._extraction_cache[(9, "eng")] = "r"
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(
            lambda *a, **k: QMessageBox.StandardButton.Yes)
        try:
            w._clear_document_cache()
        finally:
            QMessageBox.question = orig
        self.assertEqual(len(w._final_text_cache), 0)
        self.assertEqual(len(w._extraction_cache), 0)
        w.close()


if __name__ == "__main__":
    unittest.main()
