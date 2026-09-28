#!/usr/bin/env python3
"""Esegue un motore (PyMuPDF4LLM o Xberg) su un bunch e raccoglie le metriche.

Worker progettato per girare in un **processo separato per (bunch, engine,
passata)**: così tempi e picco RSS sono attribuiti correttamente e un crash di
un motore non blocca gli altri.

Output:
- markdown per pagina in ``out/<bunch>/<engine>/{raw,engine}/pXXXX.md``
- metriche in ``out/metrics/<bunch>_<engine>_<passata>.json``

Uso (da experimental/eval):
    ../../.venv/bin/python run_engines.py --bunch A --engine xberg --pass-name cold
"""

from __future__ import annotations

import argparse
import importlib
import resource
import sys
import time

import pymupdf

import evalutil
from evalutil import (
    ENGINE_LABEL,
    bunch_id_from_arg,
    md_dir,
    metrics_path,
    original_page,
    save_json,
    slice_path,
    text_metrics,
)

# I moduli del progetto (layout_engine, xberg_engine, main) stanno nella radice.
sys.path.insert(0, str(evalutil.ROOT))


def _rss_peak_kb() -> int:
    """Picco RSS del processo (Linux/macOS in KB)."""
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def _extract_engine(engine: str):
    """Carica il backend e ritorna (callable(path, page_index) -> str|None, import_ms)."""
    t0 = time.perf_counter()
    if engine == "pymupdf4llm":
        mod = importlib.import_module("pymupdf4llm")

        def call(path: str, page_index: int) -> str | None:
            return mod.to_markdown(path, pages=[page_index])

        return call, (time.perf_counter() - t0) * 1000

    import xberg_engine

    if not xberg_engine.is_available():
        return None, (time.perf_counter() - t0) * 1000

    def call(path: str, page_index: int) -> str | None:
        return xberg_engine.extract_page(path, page_index)

    return call, (time.perf_counter() - t0) * 1000


def _apply_layout_engine(pdf: str, page_index: int, raw: str, engine: str) -> tuple[str, list[str]]:
    """Applica il layout_engine (Forma B) al markdown grezzo del motore."""
    import layout_engine

    with pymupdf.open(pdf) as doc:
        page = doc[page_index]
        profile = layout_engine.profile_page(page)
        plan = layout_engine.plan_fixes(profile, ENGINE_LABEL[engine], mode="auto")
        out = layout_engine.apply_plan(raw, page, profile, plan) or raw
    return out, [f.id for f in plan]


def _progress(msg: str) -> None:
    print(msg, flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bunch", required=True, type=bunch_id_from_arg)
    parser.add_argument("--engine", required=True, choices=evalutil.ENGINES)
    parser.add_argument("--pass-name", required=True, choices=("cold", "warm"))
    parser.add_argument("--no-engine", action="store_true", help="solo output grezzo")
    args = parser.parse_args(argv)

    bunch = args.bunch
    engine = args.engine
    pdf = str(slice_path(bunch))
    with pymupdf.open(pdf) as doc:
        page_count = doc.page_count

    call, import_ms = _extract_engine(engine)
    available = call is not None
    _progress(
        f"[{bunch}/{engine}/{args.pass_name}] import {import_ms:.0f} ms, "
        f"available={available}, pagine={page_count}"
    )

    result: dict = {
        "bunch": bunch,
        "engine": engine,
        "pass": args.pass_name,
        "page_count": page_count,
        "available": available,
        "import_ms": import_ms,
        "rss_peak_kb": _rss_peak_kb(),
        "pages": [],
        "errors": [],
    }
    if not available:
        result["errors"].append(f"{engine} non disponibile")
        save_json(metrics_path(bunch, engine, args.pass_name), result)
        _progress(f"[{bunch}/{engine}/{args.pass_name}] motore non disponibile, salto")
        return 0

    for i in range(page_count):
        orig = original_page(bunch, i)
        entry: dict = {"local_index": i, "original_page": orig}

        t0 = time.perf_counter()
        try:
            raw = call(pdf, i) or ""
        except Exception as exc:  # noqa: BLE001
            raw = ""
            result["errors"].append(f"raw p{orig}: {type(exc).__name__}: {exc}")
        entry["raw_ms"] = (time.perf_counter() - t0) * 1000
        entry["raw"] = text_metrics(raw)

        raw_dir = md_dir(bunch, engine, "raw")
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"p{orig}.md").write_text(raw, encoding="utf-8")

        if not args.no_engine:
            t0 = time.perf_counter()
            try:
                eng_text, fixes = _apply_layout_engine(pdf, i, raw, engine)
            except Exception as exc:  # noqa: BLE001
                eng_text, fixes = raw, []
                result["errors"].append(f"engine p{orig}: {type(exc).__name__}: {exc}")
            entry["engine_ms"] = (time.perf_counter() - t0) * 1000
            entry["fixes"] = fixes
            entry["engine"] = text_metrics(eng_text)

            eng_dir = md_dir(bunch, engine, "engine")
            eng_dir.mkdir(parents=True, exist_ok=True)
            (eng_dir / f"p{orig}.md").write_text(eng_text, encoding="utf-8")

        result["pages"].append(entry)
        _progress(
            f"[{bunch}/{engine}/{args.pass_name}] p{orig} "
            f"raw {entry['raw_ms']:.0f} ms ({entry['raw']['chars']} char)"
            + (f", engine {entry.get('engine_ms', 0):.0f} ms" if not args.no_engine else "")
        )

    result["rss_peak_kb"] = _rss_peak_kb()
    result["raw_total_ms"] = sum(p["raw_ms"] for p in result["pages"])
    if not args.no_engine:
        result["engine_total_ms"] = sum(p.get("engine_ms", 0) for p in result["pages"])
    save_json(metrics_path(bunch, engine, args.pass_name), result)

    total = result["raw_total_ms"]
    _progress(
        f"[{bunch}/{engine}/{args.pass_name}] totale raw {total:.0f} ms "
        f"({total / max(page_count, 1):.0f} ms/pagina ammortizzati), "
        f"RSS peak {result['rss_peak_kb'] / 1024:.0f} MB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
