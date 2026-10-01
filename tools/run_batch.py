#!/usr/bin/env python3
"""Run the real extraction+engine pipeline over a manifest of pages.

Usage:
    .venv/bin/python -u tools/run_batch.py <manifest.json> <outdir>

Manifest: [{"key": str, "path": str, "page": int}, ...]  (page is 0-based)

Per page it does exactly what the app does:
    raw = main._extract_pymupdf4llm(path, page, ocr_language="eng")
    md, label = main._apply_engine_on_page(page_obj, raw,
                                           figures_dir=<outdir>/fig/<key>,
                                           page_num=page)

Writes <outdir>/{raw,md}/<key>.md, <outdir>/pages/<key>.png (dpi 110),
<outdir>/results.jsonl (one line per page, appended+flushed), and summary.json.
A per-page signal.alarm guards against hangs; failures are logged, not fatal.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pymupdf  # noqa: E402

import main  # noqa: E402
from tools import detectors  # noqa: E402

DPI = 110
PAGE_TIMEOUT = 240


class _Timeout(Exception):
    pass


def _alarm(signum, frame):  # noqa: ARG001
    raise _Timeout()


def run_page(entry: dict, outdir: str) -> dict:
    key = entry["key"]
    path = entry["path"]
    page_num = int(entry["page"])
    rec = {"key": key, "path": path, "page": page_num, "ok": False,
           "t_extract": 0.0, "t_engine": 0.0, "t_render": 0.0}

    raw_dir = os.path.join(outdir, "raw")
    md_dir = os.path.join(outdir, "md")
    png_dir = os.path.join(outdir, "pages")
    fig_dir = os.path.join(outdir, "fig", key)
    for d in (raw_dir, md_dir, png_dir, fig_dir):
        os.makedirs(d, exist_ok=True)

    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(PAGE_TIMEOUT)
    try:
        t0 = time.time()
        raw = main._extract_pymupdf4llm(path, page_num, ocr_language="eng")
        rec["t_extract"] = time.time() - t0

        with pymupdf.open(path) as doc:
            page = doc[page_num]

            t0 = time.time()
            md, label = main._apply_engine_on_page(
                page, raw, figures_dir=fig_dir, page_num=page_num)
            rec["t_engine"] = time.time() - t0
            rec["label"] = label

            t0 = time.time()
            pix = page.get_pixmap(dpi=DPI)
            pix.save(os.path.join(png_dir, f"{key}.png"))
            rec["t_render"] = time.time() - t0
            rec["n_pages"] = doc.page_count
            rec["n_images"] = len(page.get_images(full=True))

        with open(os.path.join(raw_dir, f"{key}.md"), "w") as f:
            f.write(raw or "")
        with open(os.path.join(md_dir, f"{key}.md"), "w") as f:
            f.write(md or "")

        rec.update(detectors.analyze(raw or "", md or "", key))
        rec["ok"] = True
    except _Timeout:
        rec["error"] = "timeout"
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"
        rec["trace"] = traceback.format_exc(limit=3)
    finally:
        signal.alarm(0)
    rec["t_total"] = rec["t_extract"] + rec["t_engine"] + rec["t_render"]
    return rec


def main_cli(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    manifest_path, outdir = argv[1], argv[2]
    with open(manifest_path) as f:
        manifest = json.load(f)
    os.makedirs(outdir, exist_ok=True)
    results_path = os.path.join(outdir, "results.jsonl")
    # resume: keep pages already done OK (results survive a restart)
    done: list[dict] = []
    skip: set[str] = set()
    if os.path.exists(results_path):
        for ln in open(results_path):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            done.append(r)
            if r.get("ok"):
                skip.add(r["key"])
        if skip:
            print(f"[resume] {len(skip)} pagine già fatte, salto", flush=True)
    mode = "a" if done else "w"
    with open(results_path, mode) as rf:
        for i, entry in enumerate(manifest, 1):
            if entry["key"] in skip:
                continue
            t0 = time.time()
            rec = run_page(entry, outdir)
            done.append(rec)
            rf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            rf.flush()
            flag = "ok " if rec["ok"] else "ERR"
            print(f"[{i:3}/{len(manifest)}] {flag} {rec['key']:40} "
                  f"{rec.get('t_total', 0):5.1f}s  {time.time() - t0:5.1f}s  "
                  f"{rec.get('error', '')}", flush=True)
    ok = [r for r in done if r["ok"]]
    scores = [r["score"] for r in ok if "score" in r]
    summary = {
        "n": len(done),
        "ok": len(ok),
        "errors": len(done) - len(ok),
        "score_avg": round(sum(scores) / len(scores), 2) if scores else None,
        "score_min": min(scores) if scores else None,
        "t_total": round(sum(r["t_total"] for r in done), 1),
        "t_extract": round(sum(r["t_extract"] for r in done), 1),
        "t_engine": round(sum(r["t_engine"] for r in done), 1),
        "t_render": round(sum(r["t_render"] for r in done), 1),
        "fig_missing": sum(1 for r in ok if r.get("fig_missing")),
    }
    with open(os.path.join(outdir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main_cli(sys.argv))
