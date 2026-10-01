#!/usr/bin/env python3
"""Build corpus150.json: 50 random, non-blank pages per corpus (1/2/3).

Usage:
    .venv/bin/python tools/select_corpus150.py [out.json]

Writes [{"key","path","page","corpus"}] with page 0-based.  Deterministic for a
fixed seed so runs are reproducible.
"""
from __future__ import annotations

import glob
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import pymupdf  # noqa: E402

SEED = 20261001
PER_CORPUS = 50
CORPORA = [("c1", "corpus1"), ("c2", "corpus2"), ("c3", "corpus3")]
MIN_CHARS = 25


def has_text(doc, page: int) -> bool:
    try:
        return len(doc[page].get_text().strip()) >= MIN_CHARS
    except Exception:
        return False


def pick_for_corpus(cid: str, folder: str, rng: random.Random) -> list[dict]:
    pdfs = sorted(glob.glob(os.path.join(ROOT, folder, "*.pdf")))
    if not pdfs:
        print(f"  !! {folder}: nessun PDF")
        return []
    rng.shuffle(pdfs)
    # round-robin quota across PDFs
    quota = {p: 0 for p in pdfs}
    i = 0
    while sum(quota.values()) < PER_CORPUS:
        quota[pdfs[i % len(pdfs)]] += 1
        i += 1
    out: list[dict] = []
    for pdf in pdfs:
        want = quota[pdf]
        if want <= 0:
            continue
        stem = os.path.splitext(os.path.basename(pdf))[0]
        try:
            doc = pymupdf.open(pdf)
        except Exception as e:
            print(f"  !! {stem}: {e}")
            continue
        try:
            n = doc.page_count
            if n <= 1:
                continue
            taken: set[int] = set()
            tries = 0
            while len(taken) < want and tries < want * 40:
                tries += 1
                p = rng.randrange(1, n)          # skip cover page
                if p in taken or not has_text(doc, p):
                    continue
                taken.add(p)
                out.append({"key": f"{cid}_{stem}_{p:04d}", "path": pdf,
                            "page": p, "corpus": cid})
        finally:
            doc.close()
    rng.shuffle(out)
    return out


def main(argv: list[str]) -> int:
    out_path = argv[1] if len(argv) > 1 else os.path.join(ROOT, "corpus150.json")
    rng = random.Random(SEED)
    allpages: list[dict] = []
    for cid, folder in CORPORA:
        got = pick_for_corpus(cid, folder, rng)
        print(f"{cid} ({folder}): {len(got)} pagine")
        allpages.extend(got)
    with open(out_path, "w") as f:
        json.dump(allpages, f, indent=1, ensure_ascii=False)
    print(f"\ntotale {len(allpages)} pagine -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
