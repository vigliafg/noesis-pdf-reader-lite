"""Nomi dei file del batch export: suffisso ``-translated`` e split di "both".

Verifica la logica di ``_write_batch_output`` / ``_batch_output_units`` senza
eseguire l'estrazione (thread finto con ``results`` già pronti).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
    _QT = True
except Exception:  # noqa: BLE001
    _QT = False

_HDR = "── pagina 1 │ auto ──\n\n"


class _FakeThread:
    def __init__(self, results):
        self.results = results


@unittest.skipUnless(_QT, "PyQt6 non disponibile")
class BatchNamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})

    def setUp(self):
        self.w = main.MainWindow()
        self.w._pdf_path = Path("harrison2025.pdf")

    def tearDown(self):
        self.w.close()

    def test_output_units(self):
        self.assertEqual(
            main._batch_output_units("original"), [("", "original")])
        self.assertEqual(
            main._batch_output_units("translated"),
            [("-translated", "translated")])
        self.assertEqual(
            main._batch_output_units("both"),
            [("", "original"), ("-translated", "translated")])

    def test_folder_both_splits(self):
        thread = _FakeThread(
            {0: {"original": _HDR + "A", "translated": _HDR + "B"}})
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d) / "out"
            self.w._write_batch_output(
                thread, [0], "both", True, "folder", None, folder)
            self.assertEqual(
                (folder / "harrison2025_pag0001.md").read_text(encoding="utf-8"),
                "A")
            self.assertEqual(
                (folder / "harrison2025_pag0001-translated.md").read_text(
                    encoding="utf-8"),
                "B")

    def test_zip_translated_suffix(self):
        thread = _FakeThread({0: {"translated": _HDR + "B"}})
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "b.zip"
            self.w._write_batch_output(
                thread, [0], "translated", True, "zip", out, None)
            with zipfile.ZipFile(out) as zf:
                self.assertEqual(
                    zf.namelist(), ["harrison2025_pag0001-translated.md"])

    def test_merged_translated_suffix(self):
        thread = _FakeThread({0: {"translated": _HDR + "B"}})
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "harrison2025_estratto.md"
            self.w._write_batch_output(
                thread, [0], "translated", True, "merged", out, None)
            self.assertTrue(
                (Path(d) / "harrison2025_estratto-translated.md").is_file())
            self.assertFalse(out.is_file())


if __name__ == "__main__":
    unittest.main()
