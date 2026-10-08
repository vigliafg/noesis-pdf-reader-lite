#!/usr/bin/env python3
"""Aggrega i vettori diagnostici di un run della campagna (dev-only).

Legge tutti i ``report.jsonl`` sotto la dir del run e produce:

- una tabella per pagina (segnale per segnale);
- un riepilogo dei **candidati gravi** (severità sospetta ≥3): ordine
  (inversioni/fuori-ordine), struttura (invarianti), figura mancante/bianca,
  tabella a basso recall, testo a basso recall, fallback/body vuoto.

Uso::

    .venv/bin/python tools/campaign_report.py --run /tmp/opencode/campaign/runs/G1
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter

_RECALL_TEXT_MIN = 0.90
_RECALL_TABLE_MIN = 0.85


def load(rundir: str) -> list[dict]:
    recs: list[dict] = []
    for rj in sorted(glob.glob(os.path.join(rundir, "**", "report.jsonl"),
                               recursive=True)):
        corpus = os.path.basename(os.path.dirname(os.path.dirname(rj)))
        for ln in open(rj, encoding="utf-8"):
            if ln.strip():
                r = json.loads(ln)
                r.setdefault("corpus", corpus)
                recs.append(r)
    return recs


def gross_flags(rec: dict) -> list[str]:
    """Segnali che possono indicare un errore **grave** (severità ≥3)."""
    c = rec.get("checks") or {}
    out: list[str] = []
    o = c.get("order") or {}
    if o.get("inversions"):
        out.append(f"order_inv={o['inversions']}")
    if o.get("unassigned"):
        out.append(f"order_unassigned={o['unassigned']}")
    t = c.get("text") or {}
    if t.get("ok") is False:
        out.append("text_ko")
    if (t.get("recall_pdf") is not None
            and t["recall_pdf"] < _RECALL_TEXT_MIN):
        out.append(f"text_recall={t['recall_pdf']:.2f}")
    if (c.get("tables") or {}).get("ok") is False:
        out.append("table_ko")
    if (c.get("figures") or {}).get("ok") is False:
        out.append("figure_ko")
    f = c.get("figures") or {}
    if f.get("expected") and f.get("embedded", 0) < f["expected"]:
        out.append(f"fig_missing={f.get('embedded')}/{f['expected']}")
    if f.get("blank"):
        out.append(f"fig_blank={f['blank']}")
    tb = c.get("tables") or {}
    if tb.get("recall") is not None and tb["recall"] < _RECALL_TABLE_MIN:
        out.append(f"tbl_recall={tb['recall']:.2f}")
    st = c.get("structure") or {}
    if st.get("ok") is False:
        out.append(f"struct={st.get('note')}")
    if not (rec.get("body_len") or 0):
        out.append("body_empty")
    if rec.get("engine") == "current":
        out.append("fallback=current")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    recs = load(args.run)
    kinds = Counter()
    gross_pages: list[dict] = []
    for r in recs:
        d = (r.get("checks") or {}).get("diag") or {}
        for k, v in d.items():
            if k.endswith("_words") and v:
                kinds[k] += 1
        g = gross_flags(r)
        if g:
            gross_pages.append({"pdf": r["pdf"], "page": r["page_ui"],
                                "corpus": r.get("corpus"),
                                "class": r.get("layout_class"),
                                "flags": g})
    print(f"run={args.run} pagine={len(recs)}")
    print(f"  pagine con segnale 'grave' candidato: {len(gross_pages)}")
    by = Counter(x["flags"][0].split("=")[0] for x in gross_pages)
    print(f"  per tipo: {dict(by)}")
    print(f"  diag (pagine con conteggio>0): {dict(kinds)}")
    for x in gross_pages:
        print(f"    {x['corpus']}/{x['pdf']} p{x['page']} [{x['class']}] "
              + "; ".join(x["flags"]))
    if args.json:
        json.dump({"run": args.run, "n": len(recs),
                   "gross_pages": gross_pages}, open(args.json, "w"),
                  ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
