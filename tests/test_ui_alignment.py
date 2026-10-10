"""Test dell'allineamento UI (FAB, wizard batch, tema, overlay, reader).

Gira headless (``QT_QPA_PLATFORM=offscreen``) e si salta se Qt/pymupdf mancano.
Isola config/dati/cache in una cartella temporanea e non usa mai la rete.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_TMP = tempfile.mkdtemp(prefix="noesis_ui_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(_TMP, "cache")

_IMP_OK = False
_IMP_ERR = ""
try:
    import pymupdf  # noqa: E402
    from PyQt6.QtWidgets import QApplication  # noqa: E402

    _app = QApplication.instance() or QApplication([])
    import main  # noqa: E402
    import theme  # noqa: E402

    _IMP_OK = True
except Exception as e:  # noqa: BLE001
    _IMP_ERR = repr(e)


def wait_until(pred, timeout: float = 60.0, step: float = 0.02) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        _app.processEvents()
        if pred():
            return True
        time.sleep(step)
    _app.processEvents()
    return bool(pred())


def _make_pdf(path: Path) -> None:
    doc = pymupdf.open()
    for i in range(3):
        p = doc.new_page(width=612, height=792)
        p.insert_text((72, 80), f"Page {i + 1} heading", fontsize=18)
        p.insert_text((72, 120), f"Body text of page {i + 1}.", fontsize=11)
    doc.save(str(path))
    doc.close()


class _FakeParent:
    """Minimal stand-in for MainWindow for the wizard-logic tests."""

    def __init__(self, page_count=10, current=4, name="doc.pdf"):
        self._page_count = page_count
        self._current_page = current
        self._pdf_path = Path("/tmp") / name

    def _batch_cached_count(self, pages, content, engine, target):
        return 0


class _FakeWidgetParent(main.QWidget if _IMP_OK else object):
    """QWidget-backed parent for dialogs that require a real widget parent."""

    def __init__(self, page_count=10, current=4, name="doc.pdf"):
        super().__init__()
        self._page_count = page_count
        self._current_page = current
        self._pdf_path = Path("/tmp") / name

    def _batch_cached_count(self, pages, content, engine, target):
        return 0


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
class WizardLogicTests(unittest.TestCase):
    def setUp(self):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        main.set_language("it")

    def _wizard(self, **kw):
        return main.ExportWizardDialog(_FakeWidgetParent(**kw))

    def test_current_page_mode(self):
        w = self._wizard(current=4)
        self.assertTrue(w._mode_current.isChecked())
        self.assertEqual(w.chosen_pages(), [4])

    def test_range_mode(self):
        w = self._wizard()
        w._mode_range.setChecked(True)
        w._range_from.setValue(3)
        w._range_to.setValue(6)
        self.assertEqual(w.chosen_pages(), [2, 3, 4, 5])

    def test_free_mode_parses_and_reports_errors(self):
        w = self._wizard(page_count=20)
        w._mode_free.setChecked(True)
        w._free_edit.setText("1,3,7-9")
        self.assertEqual(w.chosen_pages(), [0, 2, 6, 7, 8])
        w._free_edit.setText("abc")
        self.assertEqual(w.chosen_pages(), [])
        self.assertTrue(w._free_err.text())

    def test_formats_and_filenames(self):
        w = self._wizard(name="ha22.pdf")
        w._name_edit.setText("out")
        w._fmt_merged.setChecked(True)
        self.assertEqual(w.chosen_format(), "merged")
        self.assertTrue(str(w.chosen_path()).endswith(".md"))
        w._fmt_zip.setChecked(True)
        self.assertEqual(w.chosen_format(), "zip")
        self.assertTrue(str(w.chosen_path()).endswith(".zip"))
        w._fmt_folder.setChecked(True)
        self.assertEqual(w.chosen_format(), "folder")

    def test_content_default_and_toggles(self):
        w = self._wizard()
        self.assertEqual(w.chosen_content(), "translated")
        w._content_both.setChecked(True)
        self.assertEqual(w.chosen_content(), "both")
        self.assertTrue(w.translate_missing())
        w._missing_check.setChecked(False)
        self.assertFalse(w.translate_missing())


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
class PurgeTests(unittest.TestCase):
    """Il "Ritraduci" del FAB purga SOLO la traduzione della pagina."""

    def setUp(self):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        self.w = main.MainWindow()

    def test_invalidate_page_keeps_extraction(self):
        tp = self.w.text_panel
        self.w._final_text_cache[(3, "eng", "()")] = ("text", "auto", 1.0, "ir")
        self.w._extraction_cache[(3, "eng")] = "raw"
        tp._page_translation_cache[(3, "google", "it")] = "trad"
        tp._page_translation_cache[(4, "google", "it")] = "other"
        tp.invalidate_page(3)
        self.assertNotIn((3, "google", "it"), tp._page_translation_cache)
        self.assertIn((4, "google", "it"), tp._page_translation_cache)
        self.assertIn((3, "eng", "()"), self.w._final_text_cache)
        self.assertIn((3, "eng"), self.w._extraction_cache)


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
class ThemeTests(unittest.TestCase):
    def test_apply_theme_switches_palette(self):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        w = main.MainWindow()
        main.apply_theme_mode("light")
        w.apply_theme()
        self.assertIn(theme.LIGHT["bg"], w.styleSheet())
        main.apply_theme_mode("dark")
        w.apply_theme()
        self.assertIn(theme.DARK["bg"], w.styleSheet())


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
class FabAndOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        cls.pdf = Path(_TMP) / "ui.pdf"
        _make_pdf(cls.pdf)

    def setUp(self):
        main.set_setting("remember_tab", False)
        main.set_setting("last_tab", "original")
        self.w = main.MainWindow()
        self.w.show()
        self.w._open_pdf(self.pdf)
        wait_until(lambda: bool(self.w._final_text_cache), timeout=60)

    def tearDown(self):
        self.w.close()

    def test_origin_fab_visible_after_extraction(self):
        tp = self.w.text_panel
        self.assertTrue(wait_until(lambda: tp._fab_origin.isVisible(), 30))
        # Il badge è visibile finché il menu non viene aperto.
        self.assertTrue(tp._fab_origin._badge.isVisible())
        # Cambiando tab il FAB dell'origine sparisce.
        tp._set_active_tab(tp._btn_images)
        self.assertFalse(tp._fab_origin.isVisible())

    def test_overlay_shown_during_and_hidden_after(self):
        tp = self.w.text_panel
        tp._set_active_tab(tp._btn_original)
        tp.show_extraction_overlay("test")
        _app.processEvents()
        self.assertTrue(tp._overlay_origin.isVisible())
        tp.hide_extraction_overlay()
        _app.processEvents()
        # finish() nasconde dopo ~200 ms.
        self.assertTrue(wait_until(lambda: not tp._overlay_origin.isVisible(), 2))

    def test_reader_opens_on_current_page(self):
        self.w._set_page(1)
        wait_until(lambda: self.w._current_page == 1, 5)
        self.w._open_reader()
        reader = self.w._reader_window
        self.assertIsNotNone(reader)
        self.assertEqual(reader._page, 1)
        self.assertEqual(len(reader._doc), 3)
        reader.close()


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
class ZoneActionsTests(unittest.TestCase):
    """Bottone ▶ Estrai (conclude l'editing) e 📸 Cattura (bloccato con zone)."""

    @classmethod
    def setUpClass(cls):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        cls.pdf = Path(_TMP) / "zones.pdf"
        _make_pdf(cls.pdf)

    def setUp(self):
        main.set_setting("remember_tab", False)
        self.w = main.MainWindow()
        self.w.show()
        self.w._open_pdf(self.pdf)
        wait_until(lambda: bool(self.w._final_text_cache), timeout=60)
        self.w._set_page(0)
        wait_until(lambda: self.w._current_page == 0, 5)

    def tearDown(self):
        self.w.close()

    def test_extract_button_enabled_only_with_zones(self):
        self.assertFalse(self.w.btn_extract_zones.isEnabled())
        self.w._excluded_zones.setdefault(0, []).append((10.0, 10.0, 100.0, 50.0))
        self.w._update_extract_button()
        self.assertTrue(self.w.btn_extract_zones.isEnabled())
        self.w._on_reset_zones()
        self.assertFalse(self.w.btn_extract_zones.isEnabled())

    def test_capture_blocked_when_zones_exist(self):
        self.w._excluded_zones.setdefault(0, []).append((10.0, 10.0, 100.0, 50.0))
        self.w._begin_capture(False)
        self.assertFalse(self.w._capture_active)
        self.assertIn("reset", self.w.status_bar.currentMessage().lower())

    def test_capture_allowed_without_zones(self):
        self.w._begin_capture(True)
        self.assertTrue(self.w._capture_active)
        self.assertTrue(self.w._capture_interpret)

    def test_extract_with_zones_purges_page_cache_and_ends_editing(self):
        page = self.w._current_page
        self.w._extraction_cache[(page, "eng")] = "raw"
        self.w._final_text_cache[(page, "eng", "()")] = ("t", "auto", 1.0, "ir")
        self.w._inclusion_zones.setdefault(page, []).append(
            (10.0, 10.0, 100.0, 50.0))
        self.w._extract_with_zones()
        self.assertNotIn((page, "eng"), self.w._extraction_cache)
        self.assertNotIn((page, "eng", "()"), self.w._final_text_cache)
        self.assertFalse(self.w.btn_exclude.isChecked())
        self.assertFalse(self.w.btn_include.isChecked())
        self.assertFalse(self.w._capture_active)


def _make_table_pdf(path: Path) -> None:
    """PDF con un blocco di prosa e una tabella nativa."""
    doc = pymupdf.open()
    p = doc.new_page(width=612, height=792)
    for i, ln in enumerate([
        "Introduction to the adaptive layout engine",
        "The quick brown fox jumps over the lazy dog",
        "Results show a 25 percent improvement over the baseline",
    ]):
        p.insert_text((72, 90 + i * 18), ln, fontsize=11)
    tx, ty = 72, 220
    rows = [["Item", "Qty", "Price"], ["Widget", "12", "4.50"], ["Bolt", "120", "0.20"]]
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            p.insert_text((tx + ci * 110 + 4, ty + ri * 22 + 15), cell, fontsize=11)
        p.draw_line((tx, ty + (ri + 1) * 22), (tx + 3 * 110, ty + (ri + 1) * 22))
    for ci in range(4):
        p.draw_line((tx + ci * 110, ty), (tx + ci * 110, ty + len(rows) * 22))
    doc.save(str(path))
    doc.close()


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
class CaptureInterpretTests(unittest.TestCase):
    """Cattura (solo immagine) e Cattura e interpreta (nativo/tabella/OCR)."""

    @classmethod
    def setUpClass(cls):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        cls.pdf = Path(_TMP) / "capture.pdf"
        _make_table_pdf(cls.pdf)

    def setUp(self):
        main.set_setting("remember_tab", False)
        self.w = main.MainWindow()
        self.w.show()
        self.w._open_pdf(self.pdf)
        wait_until(lambda: bool(self.w._final_text_cache), timeout=60)
        self.w._set_page(0)
        wait_until(lambda: self.w._current_page == 0, 5)

    def tearDown(self):
        self.w.close()

    def test_interpret_native_text(self):
        doc = pymupdf.open(str(self.pdf))
        try:
            text, method = main._interpret_region(
                doc[0], pymupdf.Rect(60, 80, 552, 140))
        finally:
            doc.close()
        self.assertEqual(method, "native")
        self.assertIn("layout engine", text)

    def test_interpret_native_table(self):
        doc = pymupdf.open(str(self.pdf))
        try:
            text, method = main._interpret_region(
                doc[0], pymupdf.Rect(70, 218, 410, 290))
        finally:
            doc.close()
        self.assertEqual(method, "table")
        self.assertIn("|", text)
        self.assertIn("Widget", text)

    def test_capture_image_does_not_interpret(self):
        tp = self.w.text_panel
        self.w._begin_capture(False)
        self.w._on_region_selected(200, 240, 1600, 420)
        # la cattura esiste, ma è un oggetto immagine senza testo
        self.assertTrue(tp._captures)
        self.assertTrue(all(c.kind == "image" for c in tp._captures.values()))
        self.assertTrue(all(not c.text for c in tp._captures.values()))

    def test_capture_interpret_attaches_text(self):
        tp = self.w.text_panel
        self.w._begin_capture(True)
        self.w._on_region_selected(200, 240, 1600, 420)
        self.assertTrue(
            wait_until(
                lambda: any(c.text for c in tp._captures.values()), timeout=30))
        cap = next(c for c in tp._captures.values() if c.text)
        self.assertIn("layout engine", cap.text)
        self.assertEqual(cap.kind, "text")


if __name__ == "__main__":
    unittest.main()
