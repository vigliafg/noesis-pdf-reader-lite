#!/usr/bin/env python3
"""Segnala le pagine di un blocco da **arbitrare visivamente**.

Legge i report del blocco e stampa, per pagina, le metriche di current e ir con
i flag che meritano occhio umano:
- ``R`` recall_pdf < 0.97 (sospetta perdita testo);
- ``O`` order < 0.9 (sospetto interlacciamento);
- ``F`` figure diverse tra current e ir (differenza forte di body_len);
- ``T`` tail basso (resa troncata).

Stampa anche i path degli screenshot (PNG) da guardare.

Uso: .venv/bin/python tools/ir_flags.py --dir /tmp/opencode/ir150/block1
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def _ord(r):
    return r.get("order_score", 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    root = Path(args.dir)

    files = sorted(root.glob("*/report.jsonl"))
    if (root / "report.jsonl").exists():
        files.append(root / "report.jsonl")
    rows = []
    for jl in files:
        for line in jl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append((jl.parent, json.loads(line)))

    by = defaultdict(dict)
    for base, r in rows:
        by[(r.get("pdf","?"), r["page_ui"])][r["pipeline"]] = (base, r)

    flagged = []
    for (pdf, pg), ps in sorted(by.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        c = ps.get("current", (None, {}))[1]
        i = ps.get("ir", (None, {}))[1]
        flags = []
        if min(c.get("recall_pdf", 1), i.get("recall_pdf", 1)) < 0.97:
            flags.append("R")
        if min(_ord(c), _ord(i)) < 0.9:
            flags.append("O")
        if c and i:
            bl = max(c.get("body_len", 1), 1)
            if max(c.get("body_len", 0), i.get("body_len", 0)) > 3 * min(c.get("body_len", 1), i.get("body_len", 1)):
                flags.append("F")
        if min(c.get("tail", 9), i.get("tail", 9)) < 2:
            flags.append("T")
        line = (f"{pdf} p{pg:4d} | cur r={c.get('recall_pdf','-')} o={_ord(c):.2f} "
                f"tail={c.get('tail','-')} | ir r={i.get('recall_pdf','-')} "
                f"o={_ord(i):.2f} tail={i.get('tail','-')} | {' '.join(flags) or 'ok'}")
        print(line)
        if flags:
            flagged.append((pdf, pg, flags, ps))

    print(f"\nFLAGGATE: {len(flagged)}/{len(by)}")
    for pdf, pg, flags, ps in flagged:
        base_i = ps.get("ir")
        print(f"  - {pdf} p{pg} {' '.join(flags)}")
        for pipe in ("current", "ir"):
            if pipe in ps:
                pdfdir = ps[pipe][0]
                png = pdfdir / pipe / f"page_{pg - 1:04d}.png"
                print(f"      {pipe}: {png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
