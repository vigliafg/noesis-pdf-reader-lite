#!/usr/bin/env python3
"""Utility condivise dell'harness di valutazione PyMuPDF4LLM vs Xberg.

Il corpus è descritto in ``experimental/eval/corpus.json`` (generato da
``make_corpus.py``): una lista di *bunch*, ognuno con il PDF di origine e la
lista esplicita delle pagine da testare. Di default:

- ``A``/``B`` → ``ha22.pdf``, pagine 1230–1240 e 2230–2240 (fisse);
- ``<stem>_r1`` → 10 pagine casuali (seed fisso) oltre pagina 300 di ogni
  altro PDF in ``NOESIS_EVAL_PDF_DIR`` (default:
  ``../noesis-pdf-cloner-service/pdfs``).

Output in ``experimental/eval/out/``:
- ``slices/<id>.pdf``          ritagli (10–11 pagine) usati da entrambi i motori;
- ``<id>/pages/``              PNG a 150 DPI delle pagine;
- ``<id>/crops/``              ritagli a 300 DPI di tabelle e figure;
- ``<id>/<engine>/{raw,engine}/pXXXX.md``  output dei motori;
- ``metrics/``                 JSON con tempi, RAM e conteggi;
- ``manifest.json``, ``summary.md``, ``report.html``, ``scorecard.md``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
ROOT = EVAL_DIR.parent.parent
OUT_DIR = EVAL_DIR / "out"
CORPUS_PATH = EVAL_DIR / "corpus.json"
REPO_PDF = ROOT / "ha22.pdf"

DEFAULT_EXTERNAL_DIR = Path(
    os.environ.get(
        "NOESIS_EVAL_PDF_DIR",
        str(Path.home() / "Documenti/GitHub/noesis-pdf-cloner-service/pdfs"),
    )
)

ENGINES = ("pymupdf4llm", "xberg")
MODES = ("raw", "engine")

#: Etichette backend usate da layout_engine.
ENGINE_LABEL = {
    "pymupdf4llm": "PyMuPDF4LLM ⚡",
    "xberg": "Xberg",
}

IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def default_corpus() -> dict:
    """Corpus minimo (solo ha22) se ``corpus.json`` non esiste."""
    pdf = str(REPO_PDF if REPO_PDF.is_file() else DEFAULT_EXTERNAL_DIR / "ha22.pdf")
    return {
        "seed": 20260928,
        "bunches": [
            {"id": "A", "pdf": pdf, "pages": list(range(1230, 1241)),
             "origin": "fixed", "note": "tabella complessa + figure"},
            {"id": "B", "pdf": pdf, "pages": list(range(2230, 2241)),
             "origin": "fixed", "note": "prosa a due colonne"},
        ],
    }


def load_corpus(path: Path | None = None) -> dict:
    p = path or CORPUS_PATH
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("bunches"):
            return data
    return default_corpus()


#: Corpus caricato all'import (id → info del bunch).
CORPUS: dict = load_corpus()
BUNCHES: dict[str, dict] = {b["id"]: b for b in CORPUS["bunches"]}


def bunch_ids() -> list[str]:
    return [b["id"] for b in CORPUS["bunches"]]


def items() -> list[dict]:
    return list(CORPUS["bunches"])


def get(bunch: str) -> dict:
    try:
        return BUNCHES[bunch]
    except KeyError:
        raise SystemExit(f"bunch sconosciuto: {bunch!r} (attesi {bunch_ids()})") from None


def bunch_id_from_arg(value: str) -> str:
    if value not in BUNCHES:
        raise SystemExit(f"bunch sconosciuto: {value!r} (attesi {bunch_ids()})")
    return value


def pdf_path(bunch: str) -> Path:
    return Path(get(bunch)["pdf"])


def slice_stem(bunch: str) -> str:
    return bunch


def slice_path(bunch: str) -> Path:
    return OUT_DIR / "slices" / f"{slice_stem(bunch)}.pdf"


def bunch_pages(bunch: str) -> list[int]:
    """Numeri di pagina originali (1-based) del bunch."""
    return list(get(bunch)["pages"])


def original_page(bunch: str, local_index: int) -> int:
    return bunch_pages(bunch)[local_index]


def bunch_dir(bunch: str) -> Path:
    return OUT_DIR / bunch


def pages_dir(bunch: str) -> Path:
    return bunch_dir(bunch) / "pages"


def crops_dir(bunch: str) -> Path:
    return bunch_dir(bunch) / "crops"


def md_dir(bunch: str, engine: str, mode: str) -> Path:
    return bunch_dir(bunch) / engine / mode


def metrics_dir() -> Path:
    return OUT_DIR / "metrics"


def metrics_path(bunch: str, engine: str, run_pass: str) -> Path:
    return metrics_dir() / f"{bunch}_{engine}_{run_pass}.json"


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def text_metrics(text: str) -> dict:
    """Metriche automatiche grezze (proxy di qualità e struttura)."""
    stripped = text.strip()
    return {
        "chars": len(text),
        "words": len(re.findall(r"\S+", stripped)) if stripped else 0,
        "lines": text.count("\n") + 1 if stripped else 0,
        "replacement_chars": text.count("\ufffd"),
        "table_rows": len(re.findall(r"(?m)^\s*\|.*\|\s*$", text)),
        "images": len(IMAGE_RE.findall(text)),
        "headings": len(re.findall(r"(?m)^#{1,6}\s", text)),
    }
