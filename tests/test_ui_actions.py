"""Superficie d'azione della finestra destra: FAB + mini toolbar + card.

**Predisposizione per l'analisi** (vedi ``docs/ANALISI-UI-AZIONI.md``): verifica
automaticamente che il *workflow* dei bottoni flottanti sia corretto e stabile —

- il **FAB post-estrazione** compare sulla tab Originale dopo l'estrazione;
- il **FAB post-traduzione** compare sulla tab lingua dopo la traduzione;
- le **voci di menu** dei due FAB sono quelle attese;
- le azioni del menu sono **cablate** (trigger → effetto reale);
- l'**inventario** della superficie d'azione (``tools/ui_audit.py``) ha la forma
  attesa (tab, toolbar, FAB, azioni delle card).

Headless, isolato, senza rete.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TOOLS = os.path.join(_ROOT, "tools")
for _p in (_ROOT, _TOOLS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP = tempfile.mkdtemp(prefix="noesis_uiact_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(_TMP, "cache")

_OK = False
_ERR = ""
try:
    import pymupdf  # noqa: E402
    from PyQt6.QtWidgets import QApplication  # noqa: E402

    _app = QApplication.instance() or QApplication([])
    import main  # noqa: E402
    import ui_audit  # noqa: E402

    _OK = True
except Exception as e:  # noqa: BLE001
    _ERR = repr(e)


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


@unittest.skipUnless(_OK, f"Qt/pymupdf/ui_audit non disponibili: {_ERR}")
class FabWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        cls.pdf = Path(_TMP) / "actions.pdf"
        _make_pdf(cls.pdf)

    def setUp(self):
        main.set_language("it")
        main.set_setting("remember_tab", False)
        self.w = main.MainWindow()
        self.w._resume_last_page = False
        self.w.show()
        self.w._open_pdf(self.pdf)
        self.assertTrue(
            wait_until(lambda: bool(self.w._final_text_cache), 60))

    def tearDown(self):
        self.w.close()

    # ── workflow dei FAB ───────────────────────────────────────────────

    def test_origin_fab_appears_after_extraction(self):
        tp = self.w.text_panel
        tp._set_active_tab(tp._btn_original)
        self.assertTrue(
            wait_until(lambda: tp._fab_origin.isVisible(), 30),
            "FAB post-estrazione non compare sulla tab Originale")
        self.assertFalse(tp._fab_translated.isVisible())

    def test_translated_fab_appears_after_translation(self):
        tp = self.w.text_panel
        orig = main.translate_text
        main.translate_text = lambda text, *a, **k: "TRADOTTO"
        try:
            tp._on_show_translated()
            self.assertTrue(wait_until(lambda: tp._translated_text, 30))
            self.assertTrue(
                wait_until(lambda: tp._fab_translated.isVisible(), 10),
                "FAB post-traduzione non compare sulla tab lingua")
            self.assertFalse(tp._fab_origin.isVisible())
        finally:
            main.translate_text = orig

    def test_fab_labels_and_tips(self):
        tp = self.w.text_panel
        self.assertEqual(tp._fab_origin.text(), main.T("actions.origin.translate"))
        self.assertEqual(tp._fab_origin.toolTip(), main.T("actions.origin.cta.tip"))
        self.assertEqual(
            tp._fab_translated.text(), main.T("actions.translated.next"))
        self.assertEqual(
            tp._fab_translated.toolTip(), main.T("actions.translated.cta.tip"))

    def test_origin_fab_click_translates(self):
        tp = self.w.text_panel
        tp._set_active_tab(tp._btn_original)
        orig = main.translate_text
        main.translate_text = lambda text, *a, **k: "TRADOTTO"
        try:
            tp._fab_origin.click()
            self.assertTrue(tp._btn_translated.isChecked())
            self.assertTrue(wait_until(lambda: tp._translated_text, 30))
        finally:
            main.translate_text = orig

    def test_toolbar_copy_wired(self):
        tp = self.w.text_panel
        tp.origin_toolbar.btn_copy.click()
        self.assertIn("Page 1", QApplication.clipboard().text())

    def test_toolbar_export_wired(self):
        tp = self.w.text_panel
        dest = Path(_TMP) / "toolbar_export.md"
        orig = main.QFileDialog.getSaveFileName
        main.QFileDialog.getSaveFileName = lambda *a, **k: (str(dest), "")
        try:
            tp.origin_toolbar.btn_export.click()
        finally:
            main.QFileDialog.getSaveFileName = orig
        self.assertTrue(dest.exists())
        self.assertIn("Page 1", dest.read_text(encoding="utf-8"))

    def test_toolbar_reextract_wired(self):
        tp = self.w.text_panel
        tp.origin_toolbar.btn_action.click()
        self.assertTrue(
            wait_until(lambda: bool(self.w._final_text_cache), 30),
            "ri-estrazione non completata dopo il click sulla mini toolbar")


@unittest.skipUnless(_OK, f"Qt/pymupdf/ui_audit non disponibili: {_ERR}")
class UiInventoryTests(unittest.TestCase):
    """L'inventario della superficie d'azione (usato dall'analisi)."""

    @classmethod
    def setUpClass(cls):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        cls.pdf = Path(_TMP) / "inventory.pdf"
        _make_pdf(cls.pdf)

    def test_inventory_shape(self):
        main.set_language("it")
        main.set_setting("remember_tab", False)
        w = main.MainWindow()
        w._resume_last_page = False
        w.show()
        try:
            w._open_pdf(self.pdf)
            self.assertTrue(wait_until(lambda: bool(w._final_text_cache), 60))
            inv = ui_audit.inventory_from_window(w)
        finally:
            w.close()

        self.assertEqual(
            [t["key"] for t in inv["tabs"]],
            ["original", "translated", "images"])
        self.assertTrue(inv["fab"]["origin"]["cta"])
        self.assertEqual(
            inv["fab"]["origin"]["label"], main.T("actions.origin.translate"))
        self.assertTrue(inv["fab"]["translated"]["cta"])
        # le due mini toolbar di testo esistono con A−/A+/↺/💾
        for name in ("original", "translated"):
            texts = [b["text"] for b in inv["text_toolbars"][name]["buttons"]]
            self.assertIn("A−", texts)
            self.assertIn("A+", texts)
            self.assertIn("↺", texts)
            self.assertIn("💾", texts)
        # la tab Oggetti ha la sua mini toolbar (filtri + azioni bulk)
        tt = inv["images"]["tab_toolbar"]
        self.assertIn(main.T("objects.filter.all"), tt["filters"])
        self.assertEqual(tt["export_all"], main.T("objects.export_all"))
        # azioni card per tipo
        actions = inv["images"]["card_actions"]
        self.assertIn(main.T("gallery.save"), actions["image"])
        self.assertIn(main.T("objects.copy_md"), actions["table"])
        self.assertIn(main.T("capture.copy_text"), actions["text"])


@unittest.skipUnless(_OK, f"Qt/pymupdf/ui_audit non disponibili: {_ERR}")
class ObjectsTabTests(unittest.TestCase):
    """Tab 🗂️ Oggetti: raggruppamento per tipo, filtro, export/remove tutti."""

    @classmethod
    def setUpClass(cls):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        cls.pdf = Path(_TMP) / "objects.pdf"
        _make_pdf(cls.pdf)

    def setUp(self):
        main.set_language("it")
        main.set_setting("remember_tab", False)
        self.w = main.MainWindow()
        self.w._resume_last_page = False
        self.w.show()
        self.w._open_pdf(self.pdf)
        self.assertTrue(wait_until(lambda: bool(self.w._final_text_cache), 60))

    def tearDown(self):
        self.w.close()

    def _capture(self) -> str:
        s = self.w._render_scale or 1.0
        self.w._begin_capture(False)
        self.w._on_region_selected(72 * s, 70 * s, 560 * s, 170 * s)
        return self.w._current_images[-1]

    def test_groups_and_filter(self):
        tp = self.w.text_panel
        uri = self._capture()
        tp.set_capture(uri, "table", "| a | b |", "table", 0)
        groups = tp._object_groups()
        self.assertEqual(len(groups["table"]), 1)
        self.assertEqual(len(groups["image"]), 0)
        tp._on_objects_filter("table")
        self.assertEqual(tp._objects_filter, "table")
        tp._on_objects_filter("all")
        self.assertEqual(tp._objects_filter, "all")

    def test_remove_all(self):
        tp = self.w.text_panel
        self._capture()
        self.assertTrue(self.w._current_images)
        tp._remove_all_objects()
        self.assertFalse(self.w._current_images)
        self.assertFalse(tp._captures)

    def test_export_all(self):
        tp = self.w.text_panel
        uri = self._capture()
        tp.set_capture(uri, "table", "| a | b |", "table", 0)
        dest = Path(_TMP) / "objects_out"
        dest.mkdir(parents=True, exist_ok=True)
        orig = main.QFileDialog.getExistingDirectory
        main.QFileDialog.getExistingDirectory = lambda *a, **k: str(dest)
        try:
            tp._export_all_objects()
        finally:
            main.QFileDialog.getExistingDirectory = orig
        self.assertTrue(
            list(dest.glob("*.md")) or list(dest.glob("*.png")),
            "nessun oggetto esportato")


if __name__ == "__main__":
    unittest.main()
