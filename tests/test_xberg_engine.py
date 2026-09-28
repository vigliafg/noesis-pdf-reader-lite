"""Tests for the experimental Xberg adapter (``xberg_engine.py``).

Uses a fake ``xberg`` module injected in ``sys.modules`` so the suite runs
without the real (heavy) Rust/ONNX package. Mirrors the real Xberg 1.2.9 API:
``xberg.extract`` is **async**, takes an ``ExtractInput`` URI + ``ExtractionConfig``,
and returns an ``ExtractionResult`` whose ``results[0].pages`` is a list of
``PageContent`` (``page_number`` 1-based, ``content`` Markdown).

Run with (from the project root):

    .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import types
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import layout_engine  # noqa: E402
import xberg_engine  # noqa: E402


class _PageContent:
    def __init__(self, page_number, content):
        self.page_number = page_number
        self.content = content


class _ResultItem:
    def __init__(self, pages=None, content=None):
        if pages is not None:
            self.pages = [_PageContent(i + 1, p) for i, p in enumerate(pages)]
        self.content = content


class _ExtractionResult:
    def __init__(self, item=None, errors=None):
        self.results = [item] if item is not None else []
        self.errors = errors or []


def _make_module(*, pages=None, content=None, raise_on_extract=False, with_config=True):
    """Costruisce un finto modulo ``xberg`` con l'API 1.2.9."""
    mod = types.ModuleType("xberg")
    mod.__version__ = "fake-1.2.9"
    calls = {"count": 0, "config": None}

    class ExtractInput:
        def __init__(self, kind="uri", uri=None, mime_type=None, **kwargs):
            self.kind = kind
            self.uri = uri
            self.mime_type = mime_type

    mod.ExtractInput = ExtractInput

    if with_config:
        class ExtractionConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class PageConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class LayoutDetectionConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class PdfConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        mod.ExtractionConfig = ExtractionConfig
        mod.PageConfig = PageConfig
        mod.LayoutDetectionConfig = LayoutDetectionConfig
        mod.PdfConfig = PdfConfig

    async def extract(inp, config=None):
        calls["count"] += 1
        calls["config"] = config
        if raise_on_extract:
            raise RuntimeError("boom")
        return _ExtractionResult(_ResultItem(pages=pages, content=content))

    mod.extract = extract
    mod._calls = calls
    return mod


class FakeXbergTests(unittest.TestCase):
    def setUp(self):
        self._saved = sys.modules.get("xberg", "MISSING")
        xberg_engine._reset_for_tests()

    def tearDown(self):
        xberg_engine._reset_for_tests()
        if self._saved == "MISSING":
            sys.modules.pop("xberg", None)
        else:
            sys.modules["xberg"] = self._saved

    def _inject(self, mod):
        sys.modules["xberg"] = mod
        xberg_engine._reset_for_tests()

    def test_unavailable_when_not_installed(self):
        sys.modules["xberg"] = None  # import xberg -> ImportError
        xberg_engine._reset_for_tests()
        self.assertFalse(xberg_engine.is_available())
        self.assertIsNone(xberg_engine.extract_page("x.pdf", 0))

    def test_extracts_pages_and_caches_document(self):
        mod = _make_module(pages=["pagina 0", "pagina 1", "pagina 2"])
        self._inject(mod)

        self.assertTrue(xberg_engine.is_available())
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 0), "pagina 0")
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 2), "pagina 2")
        self.assertEqual(mod._calls["count"], 1)  # una sola estrazione documento

    def test_out_of_range_page_returns_none(self):
        self._inject(_make_module(pages=["solo una"]))
        self.assertIsNone(xberg_engine.extract_page("doc.pdf", 5))

    def test_content_split_fallback_when_no_pages(self):
        self._inject(_make_module(pages=None, content="prima\fseconda"))
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 0), "prima")
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 1), "seconda")

    def test_module_without_config_still_extracts(self):
        self._inject(_make_module(pages=["no-config"], with_config=False))
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 0), "no-config")

    def test_extraction_error_degrades_to_none(self):
        self._inject(_make_module(raise_on_extract=True))
        self.assertIsNone(xberg_engine.extract_page("doc.pdf", 0))
        self.assertIsNotNone(xberg_engine.last_error())

    def test_layout_enabled_by_default_and_disableable(self):
        mod = _make_module(pages=["x"])
        self._inject(mod)
        xberg_engine.extract_page("doc.pdf", 0)
        self.assertIn("layout", mod._calls["config"].kwargs)

        xberg_engine.clear_cache()
        xberg_engine.extract_page("doc.pdf", 0, use_layout=False)
        self.assertNotIn("layout", mod._calls["config"].kwargs)

    def test_clear_cache_forces_reextraction(self):
        mod = _make_module(pages=["a"])
        self._inject(mod)
        xberg_engine.extract_page("doc.pdf", 0)
        xberg_engine.clear_cache()
        xberg_engine.extract_page("doc.pdf", 0)
        self.assertEqual(mod._calls["count"], 2)


class LayoutBackendPredicateTests(unittest.TestCase):
    def _profile(self):
        return layout_engine.LayoutProfile(
            columns=2, splits=(300.0,), columns_overlap=True,
            has_tables=False, full_width_tables=0, has_small_text=False,
            has_references=False, has_index=False, body_blocks=4,
        )

    def test_reorder_columns_skipped_for_xberg(self):
        self.assertFalse(layout_engine._when_reorder_columns(self._profile(), "Xberg"))

    def test_reorder_columns_still_applies_for_pymupdf4llm(self):
        self.assertTrue(
            layout_engine._when_reorder_columns(self._profile(), "PyMuPDF4LLM ⚡")
        )


if __name__ == "__main__":
    unittest.main()
