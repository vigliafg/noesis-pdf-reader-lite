"""Step 1 — Test E2E dell'intera app (livello GUI) sul workflow **attuale**.

Coprono il percorso reale: apri PDF → Estrazione (in un thread) → tab Originale,
navigazione + cache, tab Traduzione (mockata), tab Immagini, zone manuali
(esclusione/inclusione/rosso>verde/reset), toggle Markdown, export (mockato),
screenshot della finestra.

Note di robustezza (CI-safe):
- girano **headless** (`QT_QPA_PLATFORM=offscreen`) e si **saltano** se Qt/pymupdf
  non sono disponibili o se la piattaforma offscreen non parte;
- isolano **config/dati/cache** in una cartella temporanea (XDG_*), così non
  toccano le impostazioni reali;
- usano un **PDF sintetico** generato al volo (nessun corpus richiesto); un test
  opzionale usa `corpus2` se presente;
- la **rete** non viene mai usata: la traduzione è **mockata**.

I casi dipendenti dall'architettura figure embedded (base64) arriveranno in Step 2.
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
_TMP = tempfile.mkdtemp(prefix="noesis_e2e_")
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


def _make_pdf(path: Path) -> None:
    """PDF sintetico a 3 pagine: testo, pagina-con-figura, pagina semplice."""
    doc = pymupdf.open()
    p0 = doc.new_page(width=612, height=792)
    p0.insert_text((72, 80), "Introduction", fontsize=20)
    p0.insert_text((72, 120), "Synthetic page one for the E2E harness.",
                   fontsize=11)
    p0.insert_text((72, 150), "The quick brown fox jumps over the lazy dog.",
                   fontsize=11)

    p1 = doc.new_page(width=612, height=792)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 60))
    pix.set_rect(pix.irect, (120, 120, 200))
    p1.insert_image(pymupdf.Rect(100, 100, 300, 250), stream=pix.tobytes("png"))
    p1.insert_text((100, 270), "FIG. 1 A synthetic test figure.", fontsize=10)
    p1.insert_text((72, 330), "Body text that follows the synthetic figure.",
                   fontsize=11)

    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((72, 80), "Third page", fontsize=18)
    p2.insert_text((72, 120), "Plain text on the last synthetic page.",
                   fontsize=11)

    doc.save(str(path))
    doc.close()


_CORPUS2 = Path(_ROOT) / "corpus2"


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
class GuiE2E(unittest.TestCase):
    """Workflow completo sull'app reale, in modalità headless."""

    @classmethod
    def setUpClass(cls):
        cls.pdf = Path(_TMP) / "synthetic.pdf"
        _make_pdf(cls.pdf)
        cls.win = main.MainWindow()
        cls.win._resume_last_page = False
        cls.win.resize(1000, 700)
        cls.win.show()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.win.close()
        except Exception:  # noqa: BLE001
            pass

    # ── helpers ────────────────────────────────────────────────────────────

    def setUp(self):
        self.w = self.win
        self.w._open_pdf(self.pdf)
        self.assertTrue(self._wait_page(0), "estrazione pagina 0 non completata")

    def _wait_page(self, page: int) -> bool:
        return wait_until(
            lambda: any(k[0] == page for k in self.w._final_text_cache))

    def _goto(self, page: int):
        self.w._set_page(page)
        self.assertTrue(self._wait_page(page), f"pagina {page} non pronta")

    def _body(self) -> str:
        return self.w.text_panel._page_body or ""

    def _label(self) -> str:
        return (self.w._last_result or ("", "", 0))[1]

    def _zone_rect(self, pdf_rect):
        s = self.w._render_scale or 1.0
        return tuple(v * s for v in pdf_rect)

    def _exclude(self, pdf_rect):
        self.w._on_region_excluded(*self._zone_rect(pdf_rect))

    def _include(self, pdf_rect):
        self.w._on_region_included(*self._zone_rect(pdf_rect))

    # ── casi ───────────────────────────────────────────────────────────────

    def test_01_extraction_fills_original_tab(self):
        body = self._body()
        self.assertIn("Introduction", body)
        self.assertEqual(self._label(), "auto")
        self.assertIn("Introduction", self.w.text_panel.origin_panel._render_source)

    def test_02_navigation_and_cache(self):
        first = self._body()
        self._goto(2)
        self.assertIn("Third page", self._body())
        self._goto(0)
        self.assertEqual(self._body(), first)  # dalla cache, identico

    def test_03_figure_embedded_and_in_gallery(self):
        self._goto(1)
        body = self._body()
        self.assertIn("FIG. 1", body)
        self.assertIn("![", body)
        # Step 2: la figura è **embedded** (JPEG base64), non più un file://
        self.assertIn("data:image/jpeg;base64,", body)
        self.assertNotIn("file://", body)
        self.assertTrue(
            any(u.startswith("data:image") for u in self.w._current_images),
            "gallery vuota: figura embedded non trovata",
        )

    def test_04_exclude_zone_changes_md_and_label(self):
        auto = self._body()
        self._exclude((90, 90, 310, 300))  # zona su figura+didascalia
        self.assertTrue(wait_until(lambda: self._label() == "manual"))
        self.assertTrue(self.w._excluded_zones.get(0))
        self.assertNotEqual(self._body(), auto)

    def test_05_reset_zones_returns_to_auto(self):
        auto0 = self._body()
        self._exclude((90, 90, 310, 300))
        self.assertTrue(wait_until(lambda: self._label() == "manual"))
        self.w._on_reset_zones()
        self.assertTrue(wait_until(lambda: self._label() == "auto"))
        self.assertFalse(self.w._excluded_zones.get(0))
        self.assertEqual(self._body(), auto0)

    def test_06_include_zone_numbered_order(self):
        self._include((60, 60, 560, 200))
        self.assertTrue(wait_until(lambda: self._label() == "manual"))
        self.assertTrue(self.w._inclusion_zones.get(0))

    def test_07_red_wins_over_green(self):
        self._include((40, 40, 575, 400))       # zona verde ampia (pagina 0)
        self._exclude((60, 60, 560, 110))       # zona rossa dentro la verde
        self.assertTrue(wait_until(lambda: self._label() == "manual"))
        # l'esclusione prevale: il testo della fascia esclusa non deve comparire
        self.assertNotIn("Introduction", self._body())

    def test_08_toggle_markdown(self):
        before = self.w._render_md
        self.w._toggle_markdown()
        self.assertNotEqual(self.w._render_md, before)
        self.w._toggle_markdown()
        self.assertEqual(self.w._render_md, before)

    def test_09_translation_mocked(self):
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

    def test_10_export_mocked(self):
        dest = Path(_TMP) / "export.md"
        orig = main.QFileDialog.getSaveFileName
        main.QFileDialog.getSaveFileName = lambda *a, **k: (str(dest), "")
        try:
            self.w.text_panel._export_window(self.w.text_panel.origin_panel)
        finally:
            main.QFileDialog.getSaveFileName = orig
        self.assertTrue(dest.exists())
        self.assertIn("Introduction", dest.read_text(encoding="utf-8"))

    def test_11_window_screenshot(self):
        out = Path(_TMP) / "window.png"
        self.assertTrue(self.w.grab().save(str(out)))
        self.assertGreater(out.stat().st_size, 1000)

    def test_12_gallery_is_per_page(self):
        # pagina 0: nessuna figura → gallery vuota
        self.assertFalse(self.w._current_images)
        self._goto(1)
        self.assertTrue(self.w._current_images, "pagina 1: figura assente")
        fig1 = list(self.w._current_images)
        # pagina 2: nessuna figura → gallery vuota (NON cumulativa)
        self._goto(2)
        self.assertFalse(self.w._current_images, "gallery cumulativa tra pagine")
        # ritorno alla pagina 1: le stesse figure ricompaiono subito (dalla cache)
        self._goto(1)
        self.assertEqual(self.w._current_images, fig1)


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
@unittest.skipUnless((_CORPUS2 / "fe22.pdf").is_file(), "corpus2/fe22.pdf assente")
class GuiE2ECorpus(unittest.TestCase):
    """Un caso su una pagina reale di corpus2 (saltato se il corpus manca)."""

    def test_real_page_extraction(self):
        w = main.MainWindow()
        w._resume_last_page = False
        w.show()
        try:
            w._open_pdf(_CORPUS2 / "fe22.pdf")
            w._set_page(1386)
            self.assertTrue(wait_until(
                lambda: any(k[0] == 1386 for k in w._final_text_cache),
                timeout=120))
            self.assertTrue(w.text_panel._page_body.strip())
        finally:
            w.close()


_CORPUS1 = Path(_ROOT) / "corpus1"


def _sample_corpus1(n: int = 10, seed: int = 20261001):
    """n pagine a caso (deterministiche) dai PDF di corpus1."""
    import random

    pdfs = sorted(_CORPUS1.glob("*.pdf"))
    if not pdfs:
        return []
    rng = random.Random(seed)
    rng.shuffle(pdfs)
    sample = []
    i = 0
    while len(sample) < n and i < n * len(pdfs):
        pdf = pdfs[i % len(pdfs)]
        i += 1
        try:
            with pymupdf.open(pdf) as doc:
                pages = doc.page_count
        except Exception:
            continue
        if pages > 1:
            sample.append((pdf, rng.randrange(1, pages)))
    return sample


@unittest.skipUnless(_IMP_OK, f"Qt/pymupdf non disponibili: {_IMP_ERR}")
@unittest.skipUnless(list(_CORPUS1.glob("*.pdf")), "corpus1 assente")
class GuiE2ECorpus1(unittest.TestCase):
    """Workflow completo su 10 pagine a caso di corpus1 (libri reali)."""

    def test_10_random_pages_workflow(self):
        sample = _sample_corpus1(10)
        self.assertEqual(len(sample), 10)
        w = main.MainWindow()
        w._resume_last_page = False
        w.show()
        try:
            for pdf, page in sample:
                with self.subTest(pdf=pdf.name, page=page):
                    w._open_pdf(pdf)
                    w._set_page(page)
                    self.assertTrue(
                        wait_until(lambda p=page: any(
                            k[0] == p for k in w._final_text_cache), timeout=300),
                        f"{pdf.name} p{page}: estrazione non completata")
                    body = w.text_panel._page_body
                    self.assertTrue(body.strip(), f"{pdf.name} p{page}: md vuoto")
                    self.assertEqual(self._label(w), "auto")
                    if "![" in body:
                        # figure embedded (Step 2), nessun file:// nel md
                        self.assertIn("data:image/jpeg;base64,", body)
                        self.assertNotIn("file://", body)
                    # gallery per-pagina: è la lista di QUESTA pagina
                    self.assertIs(w._current_images, w._page_images.get(page))
                    n_md = body.count("data:image/jpeg;base64,")
                    n_gal = sum(
                        1 for u in w._current_images if u.startswith("data:image"))
                    self.assertGreaterEqual(n_gal, n_md)
        finally:
            w.close()

    @staticmethod
    def _label(w) -> str:
        return (w._last_result or ("", "", 0))[1]


if __name__ == "__main__":
    unittest.main()
