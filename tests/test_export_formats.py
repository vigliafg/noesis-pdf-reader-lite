"""Nuovi formati di esportazione: conversione contenuto × impacchettamento.

Copre la logica condivisa desktop/CLI (``markdown_to_html`` / ``markdown_to_text``
/ ``build_epub`` / ``pandoc_convert``) e la matrice output del batch
(``markdown``/``html``/``text``/``epub`` + pandoc ``docx``/``odt``/``rtf``/
``latex``) su ogni impacchettamento (``merged``/``zip``/``folder``/``vault``).
I formati pandoc saltano se pandoc non è installato.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication, QWidget
    _QT = True
except Exception:  # noqa: BLE001
    _QT = False

_HDR = "── pagina 1 │ auto ──\n\n"
_MD = ("# Titolo\n\nTesto **bold** e [x](http://y).\n\n"
       "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
       "![fig](data:image/png;base64,"
       "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=)\n")

_PANDOC_FORMATS = ("docx", "odt", "rtf", "latex")
_TEXT_FORMATS = ("markdown", "html", "text")


class _FakeThread:
    def __init__(self, results):
        self.results = results


class CoreConversionTests(unittest.TestCase):
    def test_content_format_for_ext(self):
        self.assertEqual(main.content_format_for_ext(".HTML"), "html")
        self.assertEqual(main.content_format_for_ext(".docx"), "docx")
        self.assertEqual(main.content_format_for_ext(".tex"), "latex")
        self.assertEqual(main.content_format_for_ext(""), "markdown")

    def test_packaging_matrix(self):
        for fmt in _TEXT_FORMATS:
            for pack in ("merged", "zip", "folder", "vault"):
                self.assertTrue(main.packaging_allows(fmt, pack), (fmt, pack))
        self.assertTrue(main.packaging_allows("epub", "merged"))
        self.assertFalse(main.packaging_allows("epub", "folder"))
        self.assertFalse(main.packaging_allows("epub", "zip"))
        self.assertFalse(main.packaging_allows("epub", "vault"))
        for fmt in _PANDOC_FORMATS:
            self.assertTrue(main.packaging_allows(fmt, "merged"))
            self.assertTrue(main.packaging_allows(fmt, "folder"))
            self.assertFalse(main.packaging_allows(fmt, "vault"))

    def test_html_is_self_contained(self):
        h = main.markdown_to_html(_MD, title="Titolo", lang="it")
        self.assertIn("<!DOCTYPE html>", h)
        self.assertIn("<table>", h)
        self.assertIn("data:image/png;base64,", h)  # figure embedded
        self.assertIn("<title>Titolo</title>", h)
        self.assertIn('lang="it"', h)

    def test_text_strips_markup(self):
        t = main.markdown_to_text(_MD)
        self.assertIn("Titolo", t)
        self.assertNotIn("#", t)
        self.assertNotIn("**", t)
        self.assertNotIn("![", t)
        self.assertTrue(t.endswith("\n"))

    def test_epub_structure(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "book.epub"
            main.build_epub([("pagina 1", _MD), ("pagina 2", "solo testo")],
                            title="Libro", out_path=path, lang="it",
                            stem="libro")
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                self.assertEqual(names[0], "mimetype")
                self.assertEqual(
                    zf.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
                self.assertEqual(zf.read("mimetype"), b"application/epub+zip")
                self.assertIn("META-INF/container.xml", names)
                self.assertIn("OEBPS/content.opf", names)
                self.assertIn("OEBPS/nav.xhtml", names)
                self.assertEqual(
                    [n for n in names if n.startswith("OEBPS/images/")],
                    ["OEBPS/images/img0001.png"])
                opf = zf.read("OEBPS/content.opf").decode()
                self.assertIn("<dc:title>Libro</dc:title>", opf)
                xhtml = zf.read("OEBPS/chapter0001.xhtml").decode()
                self.assertIn("<img", xhtml)   # XHTML void tag self-closed
                self.assertIn("/>", xhtml)

    @unittest.skipUnless(main.pandoc_available(), "pandoc non installato")
    def test_pandoc_convert_to_docx(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.docx"
            main.pandoc_convert(_MD, "docx", out)
            self.assertTrue(out.is_file() and out.stat().st_size > 0)
            with zipfile.ZipFile(out) as zf:
                self.assertIn("word/document.xml", zf.namelist())

    def test_render_bytes_per_page_rejects_container(self):
        with self.assertRaises(ValueError):
            main.render_bytes(_MD, "epub")


@unittest.skipUnless(_QT, "PyQt6 non disponibile")
class BatchFormatMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})

    def setUp(self):
        self.w = main.MainWindow()
        self.w._pdf_path = Path("doc.pdf")
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.w.close()
        self._tmp.cleanup()

    def _run(self, content_fmt, packaging):
        thread = _FakeThread(
            {0: {"original": _HDR + _MD}, 1: {"original": _HDR + _MD}})
        ext = main.content_format_ext(content_fmt)
        d = Path(self._tmp.name)
        out_path = d / (f"out_{content_fmt}_{packaging}" + ext)
        out_folder = d / f"folder_{content_fmt}_{packaging}"
        path = self.w._write_batch_output(
            thread, [0, 1], "original", True, packaging, out_path,
            out_folder, content_fmt)
        data = Path(path).read_bytes() if Path(path).is_file() else None
        if packaging == "zip":
            with zipfile.ZipFile(path) as zf:
                names = zf.namelist()
                contents = {n: len(zf.read(n)) for n in names}
        elif packaging in ("folder", "vault"):
            names = sorted(p.name for p in Path(path).iterdir())
            contents = {
                p.name: p.stat().st_size for p in Path(path).iterdir()}
        else:
            names = [Path(path).name]
            contents = {Path(path).name: len(data or b"")}
        return Path(path), names, contents

    def test_merged_all_formats(self):
        for fmt in _TEXT_FORMATS + ("epub",) + _PANDOC_FORMATS:
            if (main.content_format_kind(fmt) == "pandoc"
                    and not main.pandoc_available()):
                continue
            path, names, contents = self._run(fmt, "merged")
            self.assertTrue(path.is_file(), fmt)
            self.assertTrue(path.stat().st_size > 0, fmt)
            self.assertTrue(names[0].endswith(main.content_format_ext(fmt)), fmt)

    def test_per_page_all_formats(self):
        for fmt in _TEXT_FORMATS + _PANDOC_FORMATS:
            if (main.content_format_kind(fmt) == "pandoc"
                    and not main.pandoc_available()):
                continue
            ext = main.content_format_ext(fmt)
            for packaging in ("zip", "folder"):
                _path, names, contents = self._run(fmt, packaging)
                self.assertEqual(
                    names, [f"doc_pag0001{ext}", f"doc_pag0002{ext}"],
                    f"{fmt}/{packaging}")
                self.assertTrue(all(v > 0 for v in contents.values()),
                                f"{fmt}/{packaging}")

    def test_vault_index_and_front_matter(self):
        path, names, _ = self._run("markdown", "vault")
        self.assertIn("index.md", names)
        self.assertIn("doc_pag0001.md", names)
        page = (path / "doc_pag0001.md").read_text(encoding="utf-8")
        self.assertTrue(page.startswith("---\n"))
        self.assertIn('title: "doc', page)
        index = (path / "index.md").read_text(encoding="utf-8")
        self.assertIn("doc_pag0001.md", index)

    def test_html_merged_has_body(self):
        path, _names, _ = self._run("html", "merged")
        self.assertIn("<!DOCTYPE html>", path.read_text(encoding="utf-8"))

    def test_invalid_combo_raises(self):
        with self.assertRaises(ValueError):
            self._run("epub", "folder")


@unittest.skipUnless(_QT, "PyQt6 non disponibile")
class SingleWindowExportTests(unittest.TestCase):
    """Esportazione della singola pagina corrente (finestra testo)."""

    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})

    def setUp(self):
        self.w = main.MainWindow()
        self._tmp = tempfile.TemporaryDirectory()
        self.w.text_panel._doc_stem = "doc"

    def tearDown(self):
        self.w.close()
        self._tmp.cleanup()

    def _export(self, ext):
        dest = str(Path(self._tmp.name) / ("out" + ext))
        panel = self.w.text_panel.origin_panel
        with unittest.mock.patch.object(
                panel, "toPlainText", return_value="# T\n\nbody text"), \
             unittest.mock.patch.object(
                main.QFileDialog, "getSaveFileName", return_value=(dest, "")):
            self.w.text_panel._export_window(panel)
        return Path(dest)

    def test_single_md_html_txt(self):
        self.assertIn("# T", self._export(".md").read_text(encoding="utf-8"))
        self.assertIn("<!DOCTYPE html>",
                      self._export(".html").read_text(encoding="utf-8"))
        self.assertNotIn("#", self._export(".txt").read_text(encoding="utf-8"))

    def test_single_epub(self):
        with zipfile.ZipFile(self._export(".epub")) as zf:
            self.assertEqual(zf.namelist()[0], "mimetype")

    @unittest.skipUnless(main.pandoc_available(), "pandoc non installato")
    def test_single_docx(self):
        with zipfile.ZipFile(self._export(".docx")) as zf:
            self.assertIn("word/document.xml", zf.namelist())


@unittest.skipUnless(_QT, "PyQt6 non disponibile")
class WizardAxesTests(unittest.TestCase):
    """Il wizard espone i due assi: formato contenuto × impacchettamento."""

    class _FakeParent(QWidget):
        def __init__(self, page_count=10, current=0, name="doc.pdf"):
            super().__init__()
            self._page_count = page_count
            self._current_page = current
            self._pdf_path = Path("/tmp") / name

        def _batch_cached_count(self, pages, content, engine, target):
            return 0

    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])
        main.init_config(
            main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})

    def _wizard(self):
        return main.ExportWizardDialog(self._FakeParent())

    def test_default_axes(self):
        w = self._wizard()
        self.assertEqual(w.chosen_content_format(), "markdown")
        self.assertEqual(w.chosen_packaging(), "merged")
        self.assertEqual(w.chosen_format(), "merged")  # alias
        self.assertTrue(str(w.chosen_path()).endswith(".md"))
        w.close()

    def test_html_extension(self):
        w = self._wizard()
        w._contentfmt_buttons["html"].setChecked(True)
        self.assertTrue(str(w.chosen_path()).endswith(".html"))
        for rb in (w._fmt_merged, w._fmt_zip, w._fmt_folder, w._fmt_vault):
            self.assertTrue(rb.isEnabled())
        w.close()

    def test_epub_only_merged(self):
        w = self._wizard()
        w._fmt_folder.setChecked(True)
        w._contentfmt_buttons["epub"].setChecked(True)
        self.assertTrue(w.chosen_packaging() == "merged")
        self.assertTrue(w._fmt_merged.isEnabled())
        self.assertFalse(w._fmt_zip.isEnabled())
        self.assertFalse(w._fmt_folder.isEnabled())
        self.assertFalse(w._fmt_vault.isEnabled())
        w.close()

    @unittest.skipUnless(main.pandoc_available(), "pandoc non installato")
    def test_pandoc_format_disables_vault(self):
        w = self._wizard()
        w._contentfmt_buttons["docx"].setChecked(True)
        self.assertEqual(w.chosen_content_format(), "docx")
        self.assertTrue(str(w.chosen_path()).endswith(".docx"))
        self.assertFalse(w._fmt_vault.isEnabled())
        self.assertTrue(w._fmt_merged.isEnabled())
        self.assertTrue(w._fmt_folder.isEnabled())
        w.close()


if __name__ == "__main__":
    unittest.main()
