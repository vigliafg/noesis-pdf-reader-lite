"""Step 2 — Figure embedded (JPEG base64) + tetto 30% + gate OCR.

Copre:
- lo stadio Figure in modalità ``embed`` (default): ``_link_figures`` inserisce un
  **data URI JPEG base64** e NON un ``file://``;
- la modalità ``link`` (back-compat): scrive un PNG e inserisce ``file://``;
- il **tetto 30%** per figure senza testo e il **gate OCR** (recall) per quelle
  con testo;
- la propagazione di ``exclude`` (una figura in zona rossa non viene linkata);
- il **viewer** Qt: ``_ImageDocument.loadResource`` decodifica i data URI.

Sintetici (CI-safe) + un gold su corpus2 (skip se assente). Nessuna rete.
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf  # noqa: E402

import main  # noqa: E402

_CORPUS2 = Path(_ROOT) / "corpus2"

try:
    from PyQt6.QtCore import QUrl  # noqa: E402
    from PyQt6.QtWidgets import QApplication  # noqa: E402

    _qt_app = QApplication.instance() or QApplication([])
    _QT_OK = True
except Exception:  # noqa: BLE001
    _QT_OK = False


def _page_with_figure(with_text: bool = False):
    """Pagina con un'immagine sporgente + didascalia FIG. 1."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    # immagine "fotografica" (gradiente) per non avere un PNG degenere
    w, h = 200, 150
    samples = bytearray(w * h * 3)
    for y in range(h):
        for x in range(w):
            i = (y * w + x) * 3
            samples[i] = (x * 255) // w
            samples[i + 1] = (y * 255) // h
            samples[i + 2] = ((x + y) * 255) // (w + h)
    img = pymupdf.Pixmap(pymupdf.csRGB, w, h, bytes(samples), False)
    page.insert_image(pymupdf.Rect(100, 100, 300, 250), stream=img.tobytes("png"))
    if with_text:
        page.insert_text((110, 200), "Axis label 42", fontsize=9)
    page.insert_text((100, 270), "FIG. 1 A synthetic test figure.", fontsize=10)
    page.insert_text((72, 330), "Body text after the figure.", fontsize=11)
    return doc, page


_EMBED_MD = (
    "Intro text.\n\nFIG. 1 A synthetic test figure.\n\nBody text after the figure."
)


class FigureEmbedTests(unittest.TestCase):
    def test_data_uri_is_jpeg(self):
        doc, page = _page_with_figure()
        regs = main._figure_regions(page)
        self.assertTrue(regs)
        uri = main._figure_data_uri(page, regs[0]["rect"])
        self.assertIsNotNone(uri)
        self.assertTrue(uri.startswith("data:image/jpeg;base64,"))
        raw = base64.b64decode(uri.partition(",")[2])
        self.assertEqual(raw[:3], b"\xff\xd8\xff")  # magic JPEG
        doc.close()

    def test_link_figures_embed_default(self):
        doc, page = _page_with_figure()
        out = main._link_figures(_EMBED_MD, page, None, 0)
        self.assertIn("data:image/jpeg;base64,", out)
        self.assertNotIn("file://", out)
        self.assertIn("FIG. 1", out)  # didascalia conservata
        doc.close()

    def test_link_figures_link_mode_backcompat(self):
        doc, page = _page_with_figure()
        with tempfile.TemporaryDirectory() as d:
            out = main._link_figures(_EMBED_MD, page, Path(d), 0, mode="link")
            pngs = list(Path(d).glob("*.png"))
        self.assertRegex(out, r"!\[figura 1\]\(file://")
        self.assertEqual(len(pngs), 1)
        doc.close()

    def test_exclude_zone_suppresses_figure(self):
        doc, page = _page_with_figure()
        # zona rossa che copre l'immagine → nessuna figura linkata
        out = main._link_figures(_EMBED_MD, page, None, 0, exclude=((100, 100, 300, 250),))
        self.assertEqual(out, _EMBED_MD)
        doc.close()

    def test_ocr_token_recall(self):
        orig = main._tesseract_ocr_image
        try:
            main._tesseract_ocr_image = lambda data: "axis label 42"
            self.assertGreaterEqual(main._ocr_token_recall(b"x", "Axis label 42"), 0.99)
            main._tesseract_ocr_image = lambda data: ""
            self.assertEqual(main._ocr_token_recall(b"x", "Axis label 42"), 0.0)
        finally:
            main._tesseract_ocr_image = orig

    def test_jpeg_cache_persisted_and_reused(self):
        """G7: il JPEG è salvato su disco e al secondo giro NON si ri-encoda."""
        doc, page = _page_with_figure()
        with tempfile.TemporaryDirectory() as d:
            out1 = main._link_figures(_EMBED_MD, page, Path(d), 0)
            self.assertEqual(len(list(Path(d).glob("*.jpg"))), 1)
            calls = {"n": 0}
            orig = main._figure_jpeg

            def _count(*a, **k):
                calls["n"] += 1
                return orig(*a, **k)

            main._figure_jpeg = _count
            try:
                out2 = main._link_figures(_EMBED_MD, doc[0], Path(d), 0)
            finally:
                main._figure_jpeg = orig
        self.assertEqual(calls["n"], 0)  # cache hit (persistenza)
        self.assertEqual(out1, out2)
        doc.close()

    def test_figure_with_text_uses_gate_and_can_exceed_ratio(self):
        """Con testo la figura può superare il 30% (leggibilità vince)."""
        doc, page = _page_with_figure(with_text=True)
        regs = main._figure_regions(page)
        internal = main._figure_internal_text(page, regs[0])
        self.assertTrue(internal.strip())  # c'è testo dentro la figura
        jpg = main._figure_jpeg(page, regs[0]["rect"], internal)
        self.assertIsNotNone(jpg)
        self.assertEqual(jpg[:3], b"\xff\xd8\xff")
        doc.close()


@unittest.skipUnless(_CORPUS2.joinpath("fe22.pdf").is_file(),
                     "corpus2/fe22.pdf assente (gold 30%)")
class GoldEmbedRatioTests(unittest.TestCase):
    def test_no_text_figure_under_30pct(self):
        with pymupdf.open(_CORPUS2 / "fe22.pdf") as doc:
            page = doc[1386]
            regs = main._figure_regions(page)
            self.assertTrue(regs)
            rect = regs[0]["rect"]
            clip = pymupdf.Rect(rect[0] - 2, rect[1] - 2, rect[2] + 2, rect[3] + 2)
            pix = page.get_pixmap(clip=clip, matrix=pymupdf.Matrix(3.0, 3.0))
            png = len(pix.tobytes("png"))
            jpg = main._figure_jpeg(page, rect, main._figure_internal_text(page, regs[0]))
        self.assertIsNotNone(jpg)
        self.assertLessEqual(len(jpg), 0.30 * png)


@unittest.skipUnless(_QT_OK, "PyQt6 non disponibile")
class ImageDocumentTests(unittest.TestCase):
    def test_load_resource_decodes_data_uri(self):
        doc, page = _page_with_figure()
        regs = main._figure_regions(page)
        uri = main._figure_data_uri(page, regs[0]["rect"])
        doc.close()
        image_doc = main._ImageDocument()
        img = image_doc.loadResource(2, QUrl(uri))  # 2 = ImageResource
        self.assertFalse(img.isNull())

    def test_uri_qimage_from_data_uri(self):
        doc, page = _page_with_figure()
        regs = main._figure_regions(page)
        uri = main._figure_data_uri(page, regs[0]["rect"])
        doc.close()
        self.assertFalse(main._uri_qimage(uri).isNull())


if __name__ == "__main__":
    unittest.main()
