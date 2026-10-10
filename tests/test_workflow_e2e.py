"""Test E2E dell'**intero workflow** dell'app (headless, CI-safe, senza rete).

Percorre l'app dal vivo, in un solo modulo, così che tutto il flusso utente sia
verificabile in automatico con un solo comando (``tools/harness.py``):

    apri → estrai → naviga → cattura immagine → cattura e interpreta →
    zone (escludi/includi/rosso>verde/reset) → ▶ Estrai → toggle Markdown →
    traduzione (mock) → export pagina → export batch (mock) → reader → tema →
    impostazioni → retranslate (5 lingue) → FAB/overlay → screenshot.

Robustezza (stesse regole di ``tests/test_gui_e2e.py``):
- gira **headless** (``QT_QPA_PLATFORM=offscreen``) e si **salta** se Qt/pymupdf
  mancano;
- isola **config/dati/cache** in una cartella temporanea (``XDG_*``);
- **nessuna rete**: la traduzione è mockata, l'help non apre il browser;
- usa un **PDF sintetico** generato al volo (nessun corpus richiesto).
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

# ── Isolamento ambiente PRIMA di importare Qt/main ──────────────────────────
_TMP = tempfile.mkdtemp(prefix="noesis_wf_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(_TMP, "cache")

_IMP_OK = False
_IMP_ERR = ""
try:  # import protetti: se manca qualcosa, i test si saltano
    import pymupdf  # noqa: E402
    from PyQt6.QtWidgets import QApplication  # noqa: E402

    _app = QApplication.instance() or QApplication([])
    import main  # noqa: E402
    import theme  # noqa: E402

    _IMP_OK = True
except Exception as e:  # noqa: BLE001
    _IMP_ERR = repr(e)


def wait_until(pred, timeout: float = 60.0, step: float = 0.02) -> bool:
    """Processa gli eventi Qt finché ``pred()`` è vero o scade il timeout."""
    end = time.time() + timeout
    while time.time() < end:
        _app.processEvents()
        if pred():
            return True
        time.sleep(step)
    _app.processEvents()
    return bool(pred())


def _make_workflow_pdf(path: Path) -> None:
    """PDF sintetico a 4 pagine: prosa, figura, tabella nativa, pagina semplice."""
    doc = pymupdf.open()

    p0 = doc.new_page(width=612, height=792)
    p0.insert_text((72, 80), "Introduction", fontsize=20)
    for i, line in enumerate([
        "The quick brown fox jumps over the lazy dog.",
        "Results show a 25 percent improvement over the baseline.",
        "A short third sentence for the adaptive layout engine.",
    ]):
        p0.insert_text((72, 120 + i * 18), line, fontsize=11)

    p1 = doc.new_page(width=612, height=792)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 60))
    pix.set_rect(pix.irect, (120, 120, 200))
    p1.insert_image(pymupdf.Rect(100, 100, 300, 250), stream=pix.tobytes("png"))
    p1.insert_text((100, 270), "FIG. 1 A synthetic test figure.", fontsize=10)
    p1.insert_text((72, 330), "Body text that follows the synthetic figure.",
                   fontsize=11)

    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((72, 80), "Table page", fontsize=18)
    tx, ty = 72, 130
    rows = [["Item", "Qty", "Price"], ["Widget", "12", "4.50"],
            ["Bolt", "120", "0.20"]]
    for ri, row in enumerate(rows):
        for ci, cell in enumerate(row):
            p2.insert_text((tx + ci * 110 + 4, ty + ri * 22 + 15), cell,
                           fontsize=11)
        p2.draw_line((tx, ty + (ri + 1) * 22), (tx + 3 * 110, ty + (ri + 1) * 22))
    for ci in range(4):
        p2.draw_line((tx + ci * 110, ty), (tx + ci * 110, ty + len(rows) * 22))

    p3 = doc.new_page(width=612, height=792)
    p3.insert_text((72, 80), "Third page", fontsize=18)
    p3.insert_text((72, 120), "Plain text on the last synthetic page.",
                   fontsize=11)

    doc.save(str(path))
    doc.close()


def setUpModule():  # noqa: N802
    """Inizializza la config (isolata) una volta sola."""
    if not _IMP_OK:
        return
    try:
        cfg = main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        main.set_language(cfg["lang"])
    except Exception:  # noqa: BLE001 — la config non deve mai far fallire i test
        pass


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
class FullWorkflowTests(unittest.TestCase):
    """L'intero workflow dell'app, passo per passo, headless."""

    @classmethod
    def setUpClass(cls):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
        cls.pdf = Path(_TMP) / "workflow.pdf"
        _make_workflow_pdf(cls.pdf)

    def setUp(self):
        main.set_setting("remember_tab", False)
        main.set_setting("last_tab", "original")
        main.set_language("it")
        self.w = main.MainWindow()
        self.w._resume_last_page = False
        self.w.show()
        self.w._open_pdf(self.pdf)
        self.assertTrue(self._wait_page(0), "estrazione pagina 0 non completata")

    def tearDown(self):
        try:
            self.w.close()
        except Exception:  # noqa: BLE001
            pass

    # ── helpers ────────────────────────────────────────────────────────────

    def _wait_page(self, page: int, timeout: float = 60.0) -> bool:
        return wait_until(
            lambda: any(k[0] == page for k in self.w._final_text_cache),
            timeout)

    def _goto(self, page: int) -> None:
        self.w._set_page(page)
        self.assertTrue(self._wait_page(page), f"pagina {page} non pronta")

    def _body(self) -> str:
        return self.w.text_panel._page_body or ""

    def _label(self) -> str:
        return (self.w._last_result or ("", "", 0))[1]

    def _scene(self, pdf_rect):
        s = self.w._render_scale or 1.0
        return tuple(v * s for v in pdf_rect)

    # ── 01 · apertura + estrazione ─────────────────────────────────────────

    def test_01_open_and_extract(self):
        self.assertIn("Introduction", self._body())
        self.assertEqual(self._label(), "auto")
        self.assertEqual(self.w._page_count, 4)
        self.assertTrue(self.w.btn_export_batch.isEnabled())
        self.assertTrue(self.w.btn_reader.isEnabled())

    # ── 02 · navigazione + cache ───────────────────────────────────────────

    def test_02_navigation_and_cache(self):
        first = self._body()
        self._goto(3)
        self.assertIn("Third page", self._body())
        self._goto(0)
        self.assertEqual(self._body(), first)  # dalla cache, identico

    # ── 03 · figura embedded in galleria ───────────────────────────────────

    def test_03_figure_in_gallery(self):
        self._goto(1)
        self.assertIn("FIG. 1", self._body())
        self.assertTrue(
            any(u.startswith("data:image") for u in self.w._current_images),
            "galleria vuota: figura embedded assente",
        )

    # ── 04 · cattura immagine (solo immagine) ──────────────────────────────

    def test_04_capture_image_only(self):
        self._goto(0)
        tp = self.w.text_panel
        before = len(self.w._current_images)
        self.w._begin_capture(False)
        self.assertTrue(self.w._capture_active)
        self.w._on_region_selected(*self._scene((60, 70, 560, 170)))
        self.assertEqual(len(self.w._current_images), before + 1)
        # oggetto immagine, senza testo interpretato
        self.assertTrue(tp._captures)
        self.assertTrue(all(c.kind == "image" for c in tp._captures.values()))
        self.assertTrue(all(not c.text for c in tp._captures.values()))

    # ── 05 · cattura e interpreta (nativo) ─────────────────────────────────

    def test_05_capture_interpret_native(self):
        self._goto(0)
        tp = self.w.text_panel
        self.w._begin_capture(True)
        self.w._on_region_selected(*self._scene((60, 70, 560, 170)))
        self.assertTrue(
            wait_until(lambda: any(c.text for c in tp._captures.values()), 30),
            "testo interpretato non allegato alla card")
        cap = next(c for c in tp._captures.values() if c.text)
        self.assertEqual(cap.kind, "text")
        self.assertIn("quick brown fox", cap.text)

    # ── 06 · cattura e interpreta (tabella nativa → markdown) ──────────────

    def test_06_capture_interpret_table(self):
        self._goto(2)
        tp = self.w.text_panel
        self.w._begin_capture(True)
        self.w._on_region_selected(*self._scene((70, 128, 410, 200)))
        self.assertTrue(
            wait_until(lambda: any(c.text for c in tp._captures.values()), 30))
        cap = next(c for c in tp._captures.values() if c.text)
        self.assertEqual(cap.kind, "table")
        self.assertIn("|", cap.text)
        self.assertIn("Widget", cap.text)

    # ── 07 · zone: escludi / includi / rosso>verde / reset ─────────────────

    def test_07_zones_exclude_include_reset(self):
        self._goto(0)
        auto = self._body()
        self.w._on_region_excluded(*self._scene((60, 70, 560, 110)))
        self.assertTrue(wait_until(lambda: self._label() == "manual", 30))
        self.assertTrue(self.w._excluded_zones.get(0))
        self.assertNotEqual(self._body(), auto)

        self.w._on_region_included(*self._scene((60, 60, 560, 200)))
        self.assertTrue(self.w._inclusion_zones.get(0))

        # il rosso prevale sul verde: la fascia esclusa non compare
        self.assertNotIn("Introduction", self._body())

        self.w._on_reset_zones()
        self.assertTrue(wait_until(lambda: self._label() == "auto", 30))
        self.assertFalse(self.w._excluded_zones.get(0))
        self.assertFalse(self.w._inclusion_zones.get(0))
        self.assertEqual(self._body(), auto)

    # ── 08 · ▶ Estrai conclude l'editing e riesegue ────────────────────────

    def test_08_extract_with_zones(self):
        self._goto(0)
        self.w._on_region_included(*self._scene((60, 60, 560, 200)))
        self.assertTrue(self.w.btn_extract_zones.isEnabled())
        self.w._extract_with_zones()
        # editing concluso
        self.assertFalse(self.w._capture_active)
        self.assertFalse(self.w.btn_exclude.isChecked())
        self.assertFalse(self.w.btn_include.isChecked())
        # la pagina è stata rieseguita (cache ripopolata)
        self.assertTrue(self._wait_page(0), "riestrazione non completata")
        self.assertTrue(self.w._inclusion_zones.get(0))

    # ── 09 · toggle Markdown ───────────────────────────────────────────────

    def test_09_toggle_markdown(self):
        before = self.w._render_md
        self.w._toggle_markdown()
        self.assertNotEqual(self.w._render_md, before)
        self.w._toggle_markdown()
        self.assertEqual(self.w._render_md, before)

    # ── 10 · traduzione (mock) ─────────────────────────────────────────────

    def test_10_translation_mocked(self):
        orig = main.translate_text
        main.translate_text = lambda text, *a, **k: "TRADOTTO"
        try:
            self.w.text_panel._on_show_translated()
            ok = wait_until(
                lambda: "TRADOTTO" in (
                    self.w.text_panel._translated_text
                    or self.w.text_panel.translated_panel._render_source))
            self.assertTrue(ok, "traduzione (mockata) non comparsa")
        finally:
            main.translate_text = orig

    # ── 11 · export pagina (mock file dialog) ──────────────────────────────

    def test_11_export_page_mocked(self):
        dest = Path(_TMP) / "export_page.md"
        orig = main.QFileDialog.getSaveFileName
        main.QFileDialog.getSaveFileName = lambda *a, **k: (str(dest), "")
        try:
            self.w.text_panel._export_window(self.w.text_panel.origin_panel)
        finally:
            main.QFileDialog.getSaveFileName = orig
        self.assertTrue(dest.exists())
        self.assertIn("Introduction", dest.read_text(encoding="utf-8"))

    # ── 12 · export batch completo (wizard mockato) ────────────────────────

    def test_12_batch_export_full(self):
        out_dir = Path(_TMP) / "batch_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "batch.md"

        class _FakeCombo:
            def __init__(self, data):
                self._data = data

            def currentData(self):
                return self._data

        class _FakeWizard:
            def __init__(self, parent):
                self._src_combo = _FakeCombo("en")
                self._dst_combo = _FakeCombo("it")

            def exec(self):
                return main.QDialog.DialogCode.Accepted

            def chosen_pages(self):
                return [0, 2]

            def chosen_content(self):
                return "both"

            def _current_engine(self):
                return "google"

            def translate_missing(self):
                return True

            def include_images(self):
                return False

            def chosen_format(self):
                return "merged"

            def chosen_path(self):
                return dest

            def chosen_folder(self):
                return out_dir

            def chosen_name(self):
                return "batch"

        orig_wiz = main.ExportWizardDialog
        orig_tr = main.translate_text
        main.ExportWizardDialog = _FakeWizard
        main.translate_text = lambda text, *a, **k: "TRADOTTO\n" + text
        try:
            self.w._on_export_batch()
            self.assertIsNotNone(self.w._batch_thread)
            ok = wait_until(lambda: self.w._batch_thread is None, 120)
            self.assertTrue(ok, "export batch non terminato")
        finally:
            main.ExportWizardDialog = orig_wiz
            main.translate_text = orig_tr
        self.assertTrue(dest.exists(), "output batch non scritto")
        body = dest.read_text(encoding="utf-8")
        self.assertIn("pagina 1", body)
        self.assertIn("TRADOTTO", body)

    # ── 13 · reader detached ───────────────────────────────────────────────

    def test_13_reader(self):
        self._goto(1)
        self.w._open_reader()
        reader = self.w._reader_window
        self.assertIsNotNone(reader)
        self.assertEqual(reader._page, 1)
        self.assertEqual(len(reader._doc), 4)
        reader.close()

    # ── 14 · tema chiaro/scuro ─────────────────────────────────────────────

    def test_14_theme_switch(self):
        try:
            main.apply_theme_mode("light")
            self.w.apply_theme()
            self.assertIn(theme.LIGHT["bg"], self.w.styleSheet())
            main.apply_theme_mode("dark")
            self.w.apply_theme()
            self.assertIn(theme.DARK["bg"], self.w.styleSheet())
        finally:
            main.apply_theme_mode("dark")
            self.w.apply_theme()

    # ── 15 · impostazioni applicate a caldo ────────────────────────────────

    def test_15_settings_apply(self):
        try:
            self.w._apply_settings({
                "lang": "it", "theme": "light", "font_size": 14,
                "render_md": False, "src_lang": "en", "dst_lang": "it",
                "engine": "google",
            })
            self.assertFalse(self.w._render_md)
            self.assertIn(theme.LIGHT["bg"], self.w.styleSheet())
            self.assertEqual(self.w.text_panel.origin_panel._font_size, 14)
        finally:
            self.w._apply_settings({
                "lang": "it", "theme": "dark", "font_size": 12,
                "render_md": True, "src_lang": "en", "dst_lang": "it",
                "engine": "google",
            })

    # ── 16 · retranslate su tutte le lingue UI ─────────────────────────────

    def test_16_retranslate_all_languages(self):
        seen = set()
        try:
            for code in ("it", "en", "fr", "de", "es"):
                main.set_language(code)
                self.w._retranslate_all()
                self.assertEqual(self.w.btn_open.text(), main.T("toolbar.open"))
                self.assertEqual(
                    self.w.act_capture_image.text(),
                    main.T("page_toolbar.capture.image"))
                seen.add(self.w.btn_open.text())
        finally:
            main.set_language("it")
            self.w._retranslate_all()
        self.assertGreater(len(seen), 1, "il cambio lingua non ha cambiato le stringhe")

    # ── 17 · FAB + overlay ─────────────────────────────────────────────────

    def test_17_fab_and_overlay(self):
        tp = self.w.text_panel
        tp._set_active_tab(tp._btn_original)
        self.assertTrue(wait_until(lambda: tp._fab_origin.isVisible(), 30))
        tp.show_extraction_overlay("test")
        _app.processEvents()
        self.assertTrue(tp._overlay_origin.isVisible())
        tp.hide_extraction_overlay()
        self.assertTrue(
            wait_until(lambda: not tp._overlay_origin.isVisible(), 2))

    # ── 18 · screenshot della finestra ─────────────────────────────────────

    def test_18_window_screenshot(self):
        out = Path(_TMP) / "window.png"
        self.assertTrue(self.w.grab().save(str(out)))
        self.assertGreater(out.stat().st_size, 1000)


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
class WorkflowEdgeCasesTests(unittest.TestCase):
    """Casi limite del workflow (nessun PDF aperto, PDF vuoto)."""

    @classmethod
    def setUpClass(cls):
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})

    def setUp(self):
        main.set_setting("remember_tab", False)
        main.set_language("it")

    def test_capture_without_document_does_not_crash(self):
        w = main.MainWindow()
        w._resume_last_page = False
        try:
            self.assertIsNone(w._pdf_path)
            w._begin_capture(True)      # nessun crash senza documento
            w._set_select_mode(False)   # e si può uscire dal modo cattura
            self.assertFalse(w._capture_active)
        finally:
            w.close()

    def test_extract_with_zones_no_document(self):
        w = main.MainWindow()
        try:
            w._extract_with_zones()  # no-op sicuro
            w._on_reset_zones()      # no-op sicuro
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()
