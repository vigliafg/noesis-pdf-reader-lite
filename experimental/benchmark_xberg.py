#!/usr/bin/env python3
"""Mini-benchmark Xberg vs PyMuPDF4LLM su PDF nativamente digitali.

Fa parte della Fase 1 del piano: serve a validare il backend alternativo sul
testo reale prima di fidarsene. Estrae ogni pagina con PyMuPDF4LLM e (se
installato) con Xberg, misura i tempi e confronta la similarità del testo
normalizzato.

Uso::

    python experimental/benchmark_xberg.py libro.pdf
    python experimental/benchmark_xberg.py libro.pdf --pages 0-9
    python experimental/benchmark_xberg.py libro.pdf --engine   # applica layout_engine

Richiede ``pymupdf4llm`` (tier lite) e, per il confronto, ``xberg``
(``pip install -r requirements-xberg.txt``).
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import xberg_engine  # noqa: E402

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Confronto robusto: minuscole, via punteggiatura, spazi compattati."""
    return _WS_RE.sub(" ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def _pymupdf4llm(path: str, page: int) -> str:
    import pymupdf4llm

    return pymupdf4llm.to_markdown(path, pages=[page])


def _parse_pages(spec: str | None, total: int) -> range:
    if not spec:
        return range(total)
    if "-" in spec:
        start_s, end_s = spec.split("-", 1)
        start = int(start_s) if start_s else 0
        end = int(end_s) if end_s else total
        return range(start, min(end, total))
    return range(int(spec), min(int(spec) + 1, total))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="PDF nativamente digitale da testare")
    parser.add_argument("--pages", default=None, help="intervallo pagine, es. 0-9 o 3")
    parser.add_argument("--engine", action="store_true", help="applica anche layout_engine")
    args = parser.parse_args(argv)

    path = str(Path(args.pdf).resolve())
    if not Path(path).is_file():
        print(f"ERRORE: file non trovato: {path}")
        return 1

    try:
        import pymupdf
    except ImportError:
        print("ERRORE: pymupdf non installato (tier lite).")
        return 1

    with pymupdf.open(path) as doc:
        total = doc.page_count
    pages = _parse_pages(args.pages, total)

    xberg_ok = xberg_engine.is_available()
    print(f"PDF    : {path}")
    print(f"Pagine : {total} (test: {pages.start}–{pages.stop - 1})")
    print(f"Xberg  : {'sì, ' + str(xberg_engine.version()) if xberg_ok else 'NON installato (solo PyMuPDF4LLM)'}")
    print()

    header = f"{'pag':>4} {'py4llm ms':>10} {'xberg ms':>9} {'chars py':>9} {'chars xb':>9} {'ratio':>6}"
    print(header)
    print("-" * len(header))

    sum_a = sum_b = 0.0
    ratios: list[float] = []
    for page in pages:
        t0 = time.perf_counter()
        text_a = _pymupdf4llm(path, page)
        dt_a = (time.perf_counter() - t0) * 1000

        dt_b = float("nan")
        text_b = ""
        if xberg_ok:
            t0 = time.perf_counter()
            text_b = xberg_engine.extract_page(path, page) or ""
            dt_b = (time.perf_counter() - t0) * 1000

        ratio = (
            difflib.SequenceMatcher(None, _normalize(text_a), _normalize(text_b)).ratio()
            if text_b else float("nan")
        )
        if text_b:
            ratios.append(ratio)
        sum_a += dt_a
        sum_b += 0.0 if dt_b != dt_b else dt_b

        print(
            f"{page:>4} {dt_a:>10.0f} {dt_b:>9.0f} "
            f"{len(text_a):>9} {len(text_b):>9} {ratio:>6.2f}"
        )

    print()
    print(f"Tempo totale PyMuPDF4LLM: {sum_a:>8.0f} ms")
    if xberg_ok:
        print(f"Tempo totale Xberg      : {sum_b:>8.0f} ms")
        if ratios:
            print(f"Similarità media        : {sum(ratios) / len(ratios):.3f}")
    else:
        print("Installa xberg per il confronto: pip install -r requirements-xberg.txt")

    if args.engine:
        print(
            "\n--engine: per applicare il layout_engine usa l'app "
            "(NOESIS_EXTRACT_BACKEND=xberg) o test_layout_engine."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
