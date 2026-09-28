"""Tests for the experimental Xberg adapter (``xberg_engine.py``).

Uses a fake ``xberg`` module injected in ``sys.modules`` so the suite runs
without the real (heavy) Rust/ONNX package. Covers availability detection,
sync/async extraction, per-page results, the content-split fallback, caching,
error degradation and the layout-engine backend predicate.

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


class _Page:
    def __init__(self, content):
        self.content = content


class _Result:
    def __init__(self, pages=None, content=None):
        if pages is not None:
            self.pages = [_Page(p) for p in pages]
        self.content = content


def _make_module(*, pages=None, content=None, async_only=False, raise_on_extract=False,
                 with_config=True):
    """Build a fake ``xberg`` module exposing the APIs the adapter probes."""
    mod = types.ModuleType("xberg")
    calls = {"count": 0}

    if with_config:
        class ExtractionConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class PageConfig:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        mod.ExtractionConfig = ExtractionConfig
        mod.PageConfig = PageConfig

    def _result():
        if raise_on_extract:
            raise RuntimeError("boom")
        return _Result(pages=pages, content=content)

    if not async_only:
        def extract_file_sync(path, config=None):
            calls["count"] += 1
            return _result()

        mod.extract_file_sync = extract_file_sync
    else:
        async def extract_file(path, config=None):
            calls["count"] += 1
            return _result()

        mod.extract_file = extract_file

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
        # ``None`` in sys.modules makes ``import xberg`` raise ImportError.
        sys.modules["xberg"] = None
        xberg_engine._reset_for_tests()
        self.assertFalse(xberg_engine.is_available())
        self.assertIsNone(xberg_engine.extract_page("x.pdf", 0))

    def test_extracts_pages_with_sync_api_and_caches_document(self):
        mod = _make_module(pages=["pagina 0", "pagina 1", "pagina 2"])
        self._inject(mod)

        self.assertTrue(xberg_engine.is_available())
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 0), "pagina 0")
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 2), "pagina 2")
        # L'intero documento è estratto una sola volta.
        self.assertEqual(mod._calls["count"], 1)

    def test_out_of_range_page_returns_none(self):
        self._inject(_make_module(pages=["solo una"]))
        self.assertIsNone(xberg_engine.extract_page("doc.pdf", 5))

    def test_content_split_fallback_when_no_pages(self):
        self._inject(_make_module(pages=None, content="prima\fseconda"))
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 0), "prima")
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 1), "seconda")

    def test_async_api_is_supported(self):
        self._inject(_make_module(pages=["async"], async_only=True))
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 0), "async")

    def test_module_without_config_still_extracts(self):
        self._inject(_make_module(pages=["no-config"], with_config=False))
        self.assertEqual(xberg_engine.extract_page("doc.pdf", 0), "no-config")

    def test_extraction_error_degrades_to_none(self):
        self._inject(_make_module(raise_on_extract=True))
        # Il primo tentativo popola la cache con [] (nessun crash).
        self.assertIsNone(xberg_engine.extract_page("doc.pdf", 0))
        self.assertIsNotNone(xberg_engine.last_error())

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
        self.assertFalse(
            layout_engine._when_reorder_columns(self._profile(), "Xberg")
        )

    def test_reorder_columns_still_applies_for_pymupdf4llm(self):
        self.assertTrue(
            layout_engine._when_reorder_columns(self._profile(), "PyMuPDF4LLM ⚡")
        )


if __name__ == "__main__":
    unittest.main()
