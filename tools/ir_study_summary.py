#!/usr/bin/env python3
"""Aggrega i report dello studio pipeline (tools/verify_pages.py).

Legge tutti i ``*/report.jsonl`` sotto --dir e produce un ``summary.md`` con:
- tabella per (pagina, pipeline);
- medie per pipeline: tempo, recall_pdf, recall_render, order_score;
- confronto diretto current vs ir (vittorie su ordine e tempo).

Uso: .venv/bin/python tools/ir_study_summary.py --dir /tmp/opencode/ir_study
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    args = ap.parse_args()
    root = Path(args.dir)

    rows: list[dict] = []
    files = sorted(root.glob("*/report.jsonl"))
    if (root / "report.jsonl").exists():
        files.append(root / "report.jsonl")
    for jl in files:
        for line in jl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        print("nessun report trovato in", root)
        return 1

    pipes = sorted({r["pipeline"] for r in rows})

    def _ord(r):
        # preferisce la metrica a transizioni (robusta alla formattazione)
        return r.get("order_anchor", r.get("order_score", 0.0))

    # medie per pipeline
    means = {}
    for p in pipes:
        rs = [r for r in rows if r["pipeline"] == p]
        n = len(rs)
        means[p] = {
            "n": n,
            "secs": sum(r["secs"] for r in rs) / n,
            "recall_pdf": sum(r["recall_pdf"] for r in rs) / n,
            "recall_render": sum(r["recall_render"] for r in rs) / n,
            "order": sum(_ord(r) for r in rs) / n,
            "order_full": sum(1 for r in rs if _ord(r) >= 0.999) / n,
            "render_ok": sum(1 for r in rs if r["recall_render"] >= 0.999) / n,
        }

    lines = ["# Studio pipeline (corpus1, 10 pagine casuali)\n",
             "| pipeline | n | secs/pag | recall_pdf | recall_render | order | pagine ordine pieno |",
             "|---|---|---|---|---|---|---|"]
    for p in pipes:
        m = means[p]
        lines.append(f"| {p} | {m['n']} | {m['secs']:.2f} | {m['recall_pdf']:.3f} | "
                     f"{m['recall_render']:.3f} | {m['order']:.3f} | "
                     f"{m['order_full']*100:.0f}% |")
    lines.append("")

    # confronto per pagina (order e tempo)
    if "current" in means and "ir" in means:
        by = defaultdict(dict)
        for r in rows:
            by[(r["page_ui"], r["pipeline"])] = r
        pages = sorted({r["page_ui"] for r in rows})
        wins_ir = wins_cur = 0
        lines.append("## Confronto ordine (current vs ir)\n")
        lines.append("| pagina | order current | order ir | esito |")
        lines.append("|---|---|---|---|")
        for pg in pages:
            c = by.get((pg, "current"), {})
            i = by.get((pg, "ir"), {})
            oc = _ord(c) if c else None; oi = _ord(i) if i else None
            if oc is None or oi is None:
                continue
            if oi > oc + 1e-9:
                res = "IR"; wins_ir += 1
            elif oc > oi + 1e-9:
                res = "current"; wins_cur += 1
            else:
                res = "="
            lines.append(f"| {pg} | {oc} | {oi} | {res} |")
        lines.append(f"\n**IR vince l'ordine {wins_ir} volte, current {wins_cur}.**")
        lines.append(f"Tempo: current {means['current']['secs']:.2f}s/pag vs "
                     f"ir {means['ir']['secs']:.2f}s/pag.")
        lines.append("")

    lines.append("## Dettaglio per pagina\n")
    lines.append("| pagina | pipeline | secs | body | plain | recall_pdf | recall_render | order | tail |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["page_ui"], r["pipeline"])):
        lines.append(f"| {r['page_ui']} | {r['pipeline']} | {r['secs']} | {r['body_len']} | "
                     f"{r['plain_len']} | {r['recall_pdf']} | {r['recall_render']} | "
                     f"{r['order_score']} | {r['tail']} |")

    out = root / "summary.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:40]))
    print(f"\nSommario: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
