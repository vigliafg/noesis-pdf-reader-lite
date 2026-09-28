"""Regression tests for word-spacing preservation in the layout engine.

The bug: ``_collect_blocks`` / ``_collect_lines`` dropped whitespace-only spans
(PyMuPDF splits text into spans even on spaces — on pa23/p301 they were 45% of
all spans) and then joined the remaining spans with ``""``, gluing words
("theyincreasewithareduction"). ``_trim_edge_spaces`` keeps internal spaces and
trims only the line edges.

A gold test on real PDFs is included but skipped when the files are absent
(the corpus lives outside the repo). Point ``NOESIS_GOLD_PDF_DIR`` at the folder
with the PDFs, or drop ``ha22.pdf`` in the project root.

Run with (from the project root):

    .venv/bin/python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pymupdf  # noqa: E402

import layout_engine  # noqa: E402
import main  # noqa: E402
from main import _block_to_md, _collect_blocks, _collect_lines, _trim_edge_spaces  # noqa: E402


def _span(text, size=10.0, flags=0, x0=0.0, x1=10.0):
    return {"text": text, "size": size, "flags": flags, "bbox": (x0, 0, x1, 10)}


class _StubPage:
    """Minimal stand-in for a pymupdf page exposing ``get_text("dict")``."""

    def __init__(self, blocks):
        self._blocks = blocks

    def get_text(self, kind):  # noqa: ARG002
        return {"blocks": self._blocks}


def _text_block(lines):
    return {
        "type": 0,
        "bbox": (0, 0, 100, 100),
        "lines": [{"bbox": (0, 0, 100, 10), "spans": spans} for spans in lines],
    }


class TrimEdgeSpacesTests(unittest.TestCase):
    def test_keeps_internal_space_removes_edges(self):
        spans = [_span("  "), _span("hello"), _span(" "), _span("world"), _span("  ")]
        out = _trim_edge_spaces(spans)
        self.assertEqual([s["text"] for s in out], ["hello", " ", "world"])

    def test_strips_edge_text_of_first_and_last_span(self):
        out = _trim_edge_spaces([_span("  hello "), _span(" world  ")])
        self.assertEqual([s["text"] for s in out], ["hello ", " world"])

    def test_all_whitespace_returns_empty(self):
        self.assertEqual(_trim_edge_spaces([_span("   "), _span(" ")]), [])


class CollectBlocksSpacingTests(unittest.TestCase):
    def test_whitespace_spans_are_preserved_between_words(self):
        # Simula lo split di PyMuPDF: "they", " ", "increase", " ", "with".
        page = _StubPage([_text_block([[
            _span("they"), _span(" "), _span("increase"), _span(" "), _span("with"),
        ]])])
        blocks = _collect_blocks(page)
        self.assertEqual(len(blocks), 1)
        text = _block_to_md(blocks[0], as_column=True)
        self.assertIn("they increase with", text)

    def test_collect_lines_preserves_internal_spaces(self):
        page = _StubPage([_text_block([[
            _span("obesity"), _span(" "), _span("related"),
        ]])])
        lines = _collect_lines(page)
        self.assertEqual(len(lines), 1)
        self.assertEqual(
            [s["text"] for s in lines[0]["spans"]],
            ["obesity", " ", "related"],
        )

    def test_geometric_gap_inserts_space_between_spans(self):
        # Nessuno span di spazio: lo spazio è solo un gap di 4pt tra le x.
        page = _StubPage([_text_block([[
            _span("they", x0=0.0, x1=20.0),
            _span("increase", x0=24.0, x1=60.0),
        ]])])
        text = _block_to_md(_collect_blocks(page)[0], as_column=True)
        self.assertIn("they increase", text)

    def test_no_gap_does_not_insert_space(self):
        page = _StubPage([_text_block([[
            _span("S.", x0=0.0, x1=10.0),
            _span("pyogenes", x0=10.0, x1=40.0),
        ]])])
        text = _block_to_md(_collect_blocks(page)[0], as_column=True)
        self.assertIn("S.pyogenes", text)

    def test_gap_below_threshold_does_not_insert_space(self):
        page = _StubPage([_text_block([[
            _span("they", x0=0.0, x1=20.0),
            _span("increase", x0=20.7, x1=60.0),  # gap 0.7 < 0.8
        ]])])
        text = _block_to_md(_collect_blocks(page)[0], as_column=True)
        self.assertIn("theyincrease", text)

    def test_gap_above_threshold_inserts_space(self):
        page = _StubPage([_text_block([[
            _span("they", x0=0.0, x1=20.0),
            _span("increase", x0=21.0, x1=60.0),  # gap 1.0 > 0.8
        ]])])
        text = _block_to_md(_collect_blocks(page)[0], as_column=True)
        self.assertIn("they increase", text)

    def test_whitespace_span_not_wrapped_in_markdown(self):
        # Uno spazio con flag bold non deve diventare "** **".
        bold = 16
        page = _StubPage([_text_block([[
            _span("word", flags=bold), _span(" ", flags=bold), _span("next", flags=bold),
        ]])])
        text = _block_to_md(_collect_blocks(page)[0], as_column=True)
        # Tolti i marker, il testo visibile è "word next" (nessun marker attorno
        # allo spazio, altrimenti resterebbe "word  next" o simili).
        self.assertEqual(text.replace("**", "").replace("### ", ""), "word next")


# ── gold test su PDF reali (skip se assenti) ─────────────────────────────────

_GOLD_DIR = os.environ.get(
    "NOESIS_GOLD_PDF_DIR",
    os.path.normpath(os.path.join(_ROOT, "..", "noesis-pdf-cloner-service", "pdfs")),
)


def _gold_pdf(name):
    for cand in (os.path.join(_ROOT, name), os.path.join(_GOLD_DIR, name)):
        if os.path.exists(cand):
            return cand
    return None


# (pdf, pagina 1-based) — pagine che mostravano la perdita di spazi.
@unittest.skipUnless(_gold_pdf("pa23.pdf"), "pa23.pdf non presente (gold test)")
class GoldSpacingTests(unittest.TestCase):
    """Il motore non deve distruggere gli spazi che il raw PyMuPDF4LLM ha."""

    def _spaces_per100(self, text):
        return 100 * text.count(" ") / max(len(text), 1)

    def test_engine_does_not_lose_word_spacing(self):
        import pymupdf4llm

        pdf = _gold_pdf("pa23.pdf")
        for page_no in (301, 431, 909, 743):
            with self.subTest(page=page_no):
                raw = pymupdf4llm.to_markdown(pdf, pages=[page_no - 1])
                with pymupdf.open(pdf) as doc:
                    text, _ = main._apply_engine_on_page(doc[page_no - 1], raw)
                self.assertGreaterEqual(
                    self._spaces_per100(text),
                    0.9 * self._spaces_per100(raw),
                    f"perdita di spazi su pa23 p{page_no}",
                )


@unittest.skipUnless(_gold_pdf("ce24.pdf"), "ce24.pdf non presente (gold cleanup)")
class GoldCleanupTests(unittest.TestCase):
    """Pack 1 end-to-end su pagine reali: header e titoli spezzati."""

    def _run(self, pdf, page_no):
        import pymupdf4llm

        raw = pymupdf4llm.to_markdown(pdf, pages=[page_no - 1])
        with pymupdf.open(pdf) as doc:
            text, _ = main._apply_engine_on_page(doc[page_no - 1], raw)
        return raw, text

    def test_running_header_removed(self):
        raw, text = self._run(_gold_pdf("ce24.pdf"), 489)
        self.assertIn("HEART FAIluRE", raw)  # sanity: il raw ce l'ha
        self.assertNotIn(
            "heart failure treatment and prognosis",
            layout_engine._norm_noise(text),
        )

    @unittest.skipUnless(_gold_pdf("pa23.pdf"), "pa23.pdf non presente")
    def test_chapter_header_and_page_number_removed(self):
        raw, text = self._run(_gold_pdf("pa23.pdf"), 743)
        self.assertIn("CHAPTER 18 Endocrine System", raw)
        norm = layout_engine._norm_noise(text)
        self.assertNotIn("chapter 18 endocrine system", norm)
        first = next(ln for ln in text.split("\n") if ln.strip())
        self.assertIsNone(
            layout_engine._STANDALONE_NUM_RE.match(first),
            f"numero di pagina rimasto in testa: {first!r}",
        )

    @unittest.skipUnless(_gold_pdf("ha22.pdf"), "ha22.pdf non presente")
    def test_split_heading_merged(self):
        raw, text = self._run(_gold_pdf("ha22.pdf"), 1237)
        self.assertIn("NUTRITIONALLY VARIANT STREPTOCOCCI", raw)
        norm = layout_engine._norm_noise(text)
        self.assertIn(
            "abiotrophia and granulicatella species (nutritionally variant streptococci)",
            norm,
        )


if __name__ == "__main__":
    unittest.main()
