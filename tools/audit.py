#!/usr/bin/env python3
"""Token-level anti-regression audit between two run dirs (before/after).

Usage:
    .venv/bin/python tools/audit.py <dir_before> <dir_after>

Compares, per key, the markdown in ``<dir>/md/<key>.md`` at content-token level
(lowercased alphanumeric tokens, markdown stripped) and reports pages whose
markdown LOST content tokens, plus structural anomalies (empty headings, stray
fences, adjacent empty table cells).  A loss is not always a bug (a deliberate
header/watermark removal does it), so this only points at candidates for the
visual arbitration -- it is the arbiter that decides.
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter

_WORD = re.compile(r"[0-9a-z]+")
_MD_NOISE = re.compile(r"[*_`~>#|]|<[^>]+>")


def tokens(md: str) -> Counter:
    clean = _MD_NOISE.sub(" ", md or "").lower()
    return Counter(_WORD.findall(clean))


def structure_flags(md: str) -> list[str]:
    md = md or ""
    flags = []
    if re.search(r"(?m)^#{1,6}\s*$", md):
        flags.append("empty_heading")
    if md.count("```") % 2:
        flags.append("unbalanced_fence")
    if re.search(r"\|\s*\|\s*\|", md):
        flags.append("adjacent_empty_cells")
    return flags


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    before_dir, after_dir = argv[1], argv[2]
    md_before = os.path.join(before_dir, "md")
    md_after = os.path.join(after_dir, "md")
    keys = sorted(k[:-3] for k in os.listdir(md_before) if k.endswith(".md"))
    worsened, improved, same = [], [], []
    total_removed = total_added = 0
    struct_changes = []
    for k in keys:
        pb = os.path.join(md_before, k + ".md")
        pa = os.path.join(md_after, k + ".md")
        if not os.path.exists(pa):
            print(f"!! manca after per {k}")
            continue
        tb = tokens(open(pb).read())
        ta = tokens(open(pa).read())
        removed = tb - ta
        added = ta - tb
        n_rem = sum(removed.values())
        n_add = sum(added.values())
        total_removed += n_rem
        total_added += n_add
        fb = structure_flags(open(pb).read())
        fa = structure_flags(open(pa).read())
        if set(fb) != set(fa):
            struct_changes.append((k, fb, fa))
        rec = (k, n_rem, n_add, removed)
        if n_rem > n_add:
            worsened.append(rec)
        elif n_add > n_rem:
            improved.append(rec)
        else:
            same.append(rec)
    print(f"keys: {len(keys)}  |  peggiorate: {len(worsened)}  "
          f"migliorate: {len(improved)}  invarianti: {len(same)}")
    print(f"token-contenuto: rimossi {total_removed}, aggiunti {total_added}")
    if struct_changes:
        print("\n-- cambi strutturali --")
        for k, fb, fa in struct_changes:
            print(f"  {k}: {fb} -> {fa}")
    if worsened:
        print("\n-- pagine con PIÙ token rimossi che aggiunti (da ispezionare) --")
        for k, n_rem, n_add, removed in sorted(worsened, key=lambda r: -r[1])[:30]:
            sample = ", ".join(w for w, _ in removed.most_common(8))
            print(f"  {k:30} -{n_rem} +{n_add}  es: {sample}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
