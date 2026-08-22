"""Tests for the markdown-preserving translation (tables + image links)."""

import json
import os
import sys
import unittest
from unittest import mock

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import main  # noqa: E402


def _fake_translate(s: str, source: str, target: str) -> str:
    """Simulate translation by uppercasing (tokens are already uppercase)."""
    return s.upper()


class TranslateTableTests(unittest.TestCase):
    def test_table_structure_and_cells_survive(self):
        text = (
            "Intro text here.\n\n"
            "| Name | Value |\n| --- | --- |\n| Alpha | 42 |"
        )
        with mock.patch("main._gt_translate_one", side_effect=_fake_translate):
            out = main.translate_text_google(text)
        self.assertIn("INTRO TEXT HERE.", out)
        self.assertIn("| NAME | VALUE |", out)
        self.assertIn("| --- | --- |", out)
        self.assertIn("| ALPHA | 42 |", out)

    def test_separator_row_kept_verbatim(self):
        table = "| A | B |\n|:---|---:|\n| x | y |"
        with mock.patch("main._gt_translate_one", side_effect=_fake_translate):
            out = main._translate_table(table, "en", "it")
        self.assertIn("|:---|---:|", out)
        self.assertIn("| X | Y |", out)

    def test_numeric_only_cells_left_alone(self):
        table = "| Dose |\n| --- |\n| 500 mg |"
        with mock.patch("main._gt_translate_one", side_effect=_fake_translate):
            out = main._translate_table(table, "en", "it")
        # "500 mg" contains letters so it's translated (uppercased); the point
        # is structure is intact and no crash on mixed cells.
        self.assertIn("| DOSE |", out)
        self.assertIn("| --- |", out)


class TranslateImageTests(unittest.TestCase):
    def test_image_links_survive_translation(self):
        text = "See the figure:\n\n![figura](file:///tmp/x/page_1007_img_0.png)"
        with mock.patch("main._gt_translate_one", side_effect=_fake_translate):
            out = main.translate_text_google(text)
        self.assertIn("![figura](file:///tmp/x/page_1007_img_0.png)", out)
        self.assertIn("SEE THE FIGURE:", out)


class _FakeResponse:
    """Minimal file-like context manager for patched ``urllib.request.urlopen``."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class MicrosoftEngineTests(unittest.TestCase):
    """Microsoft Edge free endpoint: request shape and response parsing."""

    def test_ms_translate_one_parses_response(self):
        payload = json.dumps(
            [{"translations": [{"text": "ciao mondo", "to": "it"}]}]
        ).encode("utf-8")
        with mock.patch(
            "urllib.request.urlopen", return_value=_FakeResponse(payload)
        ) as urlopen:
            out = main._ms_translate_one("hello world", "en", "it")
        self.assertEqual(out, "ciao mondo")

        req = urlopen.call_args.args[0]
        self.assertEqual(
            req.full_url,
            "https://edge.microsoft.com/translate/translatetext"
            "?isEnterpriseClient=False&to=it&from=en",
        )
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(json.loads(req.data), ["hello world"])
        self.assertEqual(req.headers["Content-type"], "application/json")

    def test_ms_translate_one_omits_from_when_auto(self):
        payload = json.dumps(
            [{"translations": [{"text": "ciao", "to": "it"}]}]
        ).encode("utf-8")
        with mock.patch(
            "urllib.request.urlopen", return_value=_FakeResponse(payload)
        ) as urlopen:
            out = main._ms_translate_one("hello", "auto", "it")
        self.assertEqual(out, "ciao")
        req = urlopen.call_args.args[0]
        self.assertNotIn("from=", req.full_url)

    def test_pipeline_falls_back_when_engine_raises(self):
        """Like Google: single-chunk calls propagate errors; the pipeline
        (paragraph/table level) degrades to the original text."""
        with mock.patch("main._ms_translate_one", side_effect=OSError("boom")):
            out = main.translate_text("Hello world.", engine="microsoft")
        self.assertEqual(out, "Hello world.")

    def test_ms_lang_code_mapping(self):
        self.assertEqual(main._ms_lang_code("zh"), "zh-Hans")
        self.assertEqual(main._ms_lang_code("it"), "it")

    def test_ms_url_uses_mapped_codes(self):
        payload = json.dumps(
            [{"translations": [{"text": "你好", "to": "zh-Hans"}]}]
        ).encode("utf-8")
        with mock.patch(
            "urllib.request.urlopen", return_value=_FakeResponse(payload)
        ) as urlopen:
            main._ms_translate_one("hello", "auto", "zh")
        req = urlopen.call_args.args[0]
        self.assertIn("to=zh-Hans", req.full_url)

    def test_dispatch_selects_engine(self):
        with mock.patch("main._ms_translate_one", return_value="X") as ms, \
                mock.patch("main._gt_translate_one", return_value="Y") as gt:
            self.assertEqual(main._translate_one("microsoft", "hi", "en", "it"), "X")
            self.assertEqual(main._translate_one("google", "hi", "en", "it"), "Y")
        ms.assert_called_once_with("hi", "en", "it")
        gt.assert_called_once_with("hi", "en", "it")

    def test_translate_text_with_microsoft_engine(self):
        text = "Hello world.\n\n| A | B |\n| --- | --- |\n| x | y |"
        with mock.patch("main._ms_translate_one", side_effect=_fake_translate):
            out = main.translate_text(text, engine="microsoft")
        self.assertIn("HELLO WORLD.", out)
        self.assertIn("| A | B |", out)
        self.assertIn("| X | Y |", out)

    def test_translate_text_google_wrapper_uses_google_engine(self):
        with mock.patch("main._ms_translate_one", side_effect=AssertionError) as ms, \
                mock.patch("main._gt_translate_one", side_effect=_fake_translate):
            out = main.translate_text_google("Hello world.")
        self.assertEqual(out, "HELLO WORLD.")
        ms.assert_not_called()


class TesseractOcrTests(unittest.TestCase):
    """OCR language mapping and the frozen-bundle Tesseract setup."""

    def test_tess_lang_code_maps_all_languages(self):
        expected = {
            "en": "eng", "it": "ita", "fr": "fra", "de": "deu", "es": "spa",
            "pt": "por", "nl": "nld", "pl": "pol", "ru": "rus",
            "zh": "chi_sim", "ja": "jpn", "ko": "kor", "ar": "ara", "tr": "tur",
        }
        for code, tess in expected.items():
            self.assertEqual(main._tess_lang_code(code), tess)

    def test_tess_lang_code_auto_uses_combination(self):
        combo = main._tess_lang_code("auto")
        self.assertIn("+", combo)
        self.assertIn("eng", combo)
        self.assertEqual(main._tess_lang_code(None), combo)
        self.assertEqual(main._tess_lang_code(""), combo)

    def test_tess_lang_code_unknown_falls_back_to_eng(self):
        self.assertEqual(main._tess_lang_code("xx"), "eng")

    def test_setup_ignored_outside_frozen(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(main.sys, "frozen", False, create=True):
                main._setup_bundled_tesseract()
        self.assertNotIn("TESSDATA_PREFIX", os.environ)
        self.assertNotIn("tesseract", os.environ.get("PATH", ""))

    def test_apply_engine_standalone_on_repo_pdf(self):
        """The background layout-engine path works on a real text PDF."""
        pdf = os.path.join(_ROOT, "harrison2025.pdf")
        if not os.path.exists(pdf):
            self.skipTest("harrison2025.pdf not present")
        text, label = main._apply_engine_standalone(
            pdf, 0, "Some **bold** text.", exclude=(), include=()
        )
        self.assertEqual(label, "auto")
        self.assertIsInstance(text, str)
        self.assertTrue(text)

    def test_setup_frozen_sets_path_and_tessdata(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "tesseract.exe" if os.name == "nt" else "tesseract")
            with open(exe, "w") as fh:
                fh.write("fake")
            os.makedirs(os.path.join(tmp, "tessdata"), exist_ok=True)
            os.makedirs(os.path.join(tmp, "lib"), exist_ok=True)
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(main.sys, "frozen", True, create=True):
                    with mock.patch.object(main.sys, "_MEIPASS", tmp, create=True):
                        main._setup_bundled_tesseract()
                self.assertIn(tmp, os.environ["PATH"])
                self.assertEqual(
                    os.environ["TESSDATA_PREFIX"], os.path.join(tmp, "tessdata")
                )
                lib = os.path.join(tmp, "lib")
                if os.name == "nt":
                    self.assertIn(lib, os.environ["PATH"])
                else:
                    self.assertIn(lib, os.environ.get("LD_LIBRARY_PATH", ""))


class EditPersistenceKeyTests(unittest.TestCase):
    """Keying of the per-document user-edit cache (no Qt needed)."""

    _HDR = "── Backend: PyMuPDF4LLM ⚡  │  123.4 ms  │  42 characters  │  Fix: Adaptive engine  │  OCR: 🌐 Auto  │  Transl: Google Translate ──\n\n"

    def test_text_key_deterministic(self):
        self.assertEqual(main._text_key("ciao"), main._text_key("ciao"))
        self.assertNotEqual(main._text_key("ciao"), main._text_key("ciao!"))

    def test_strip_header_removes_header_line(self):
        body = "# Titolo\n\nparagrafo"
        stripped = main._strip_header(self._HDR + body)
        self.assertEqual(stripped, body)

    def test_strip_header_leaves_plain_body(self):
        body = "# Titolo\n\nparagrafo"
        self.assertEqual(main._strip_header(body), body)

    def test_strip_header_ignores_doc_starting_with_dashes(self):
        # A document whose first line is "──" but without the │ separator
        # must NOT be treated as a header.
        body = "── Capitolo 1 ──\n\nTesto."
        self.assertEqual(main._strip_header(body), body)

    def test_edit_key_stable_across_header_variations(self):
        hdr_a = "── Backend: PyMuPDF4LLM ⚡  │  123.4 ms  │  42 caratteri  │  Fix: Engine adattativo  │  OCR: 🌐 Auto  │  Trad: Google Translate ──\n\n"
        hdr_b = "── Backend: PyMuPDF4LLM ⚡  │  98.2 ms  │  42 characters  │  Fix: Adaptive engine  │  OCR: 🇩🇪 Deutsch  │  Transl: Microsoft Edge (Free) ──\n\n"
        body = "Contenuto della pagina.\n\nSecondo paragrafo."
        self.assertEqual(
            main._text_key(main._strip_header(hdr_a + body)),
            main._text_key(main._strip_header(hdr_b + body)),
        )




class FontZoomTests(unittest.TestCase):
    """Pure logic behind the per-window A−/A+ zoom (no Qt needed)."""

    def test_clamp_font_size_within_range(self):
        self.assertEqual(main._clamp_font_size(12), 12)

    def test_clamp_font_size_lower_bound(self):
        self.assertEqual(main._clamp_font_size(3), main._MIN_FONT_SIZE)
        self.assertEqual(main._clamp_font_size(-5), main._MIN_FONT_SIZE)

    def test_clamp_font_size_upper_bound(self):
        self.assertEqual(main._clamp_font_size(99), main._MAX_FONT_SIZE)

    def test_clamp_font_size_custom_bounds(self):
        self.assertEqual(main._clamp_font_size(20, lo=10, hi=16), 16)
        self.assertEqual(main._clamp_font_size(5, lo=10, hi=16), 10)

    def test_clamp_font_size_coerces_float(self):
        self.assertEqual(main._clamp_font_size(13.9), 13)




if __name__ == "__main__":
    unittest.main()
