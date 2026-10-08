#!/usr/bin/env python3
"""Genera i bundle di **arbitraggio visivo** per un run della campagna (dev-only).

Per le pagine selezionate produce, sotto ``<out>/review/<tag>/``:

- ``page.png`` — la pagina sorgente renderizzata 2×;
- ``md.txt`` — il markdown emesso, con le figure base64 sostituite da ``[IMG]``;
- ``info.txt`` — metriche del vettore diagnostico + flag + etichetta span/classe.

Selezione:
- ``--mode general``  → **tutte le flaggate + 30% delle pulite** (seed fisso);
- ``--mode sectorial`` → **tutte** le pagine.

Uso::

    .venv/bin/python tools/mk_review.py --run /tmp/opencode/campaign/runs/G1 \\
        --sample /tmp/opencode/campaign/general_G1.json --mode general \\
        --out /tmp/opencode/campaign/review_G1
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_IMG_RE = re.compile(r"data:image/[^)\s]+")


def _load_labels(sample_path: str | None) -> dict[tuple[str, int], str]:
    labels: dict[tuple[str, int], str] = {}
    if not sample_path:
        return labels
    try:
        d = json.loads(Path(sample_path).read_text(encoding="utf-8"))
    except Exception:
        return labels
    for _corpus, vals in (d.get("sample") or {}).items():
        for v in vals:
            lab = v.get("span") or v.get("layout_class")
            if lab:
                labels[(v["pdf"], v["page"])] = lab
    return labels


def main() -> int:
    ap = argparse.ArgumentParser(description="Bundle di arbitraggio")
    ap.add_argument("--run", required=True, help="dir del run (con report.jsonl)")
    ap.add_argument("--sample", default=None)
    ap.add_argument("--mode", choices=("general", "sectorial"),
                    default="general")
    ap.add_argument("--clean-frac", type=float, default=0.30)
    ap.add_argument("--seed", type=int, default=20261101)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import pymupdf

    recs: list[tuple[dict, Path]] = []
    for rj in sorted(glob.glob(os.path.join(args.run, "**", "report.jsonl"),
                               recursive=True)):
        base = Path(rj).parent
        for ln in Path(rj).read_text(encoding="utf-8").splitlines():
            if ln.strip():
                recs.append((json.loads(ln), base))
    flagged = [x for x in recs if x[0].get("flags")]
    clean = [x for x in recs if not x[0].get("flags")]
    if args.mode == "sectorial":
        sel = recs
    else:
        rng = random.Random(args.seed)
        k = int(round(len(clean) * args.clean_frac))
        sel = flagged + rng.sample(clean, min(k, len(clean)))
    sel.sort(key=lambda x: (x[0]["pdf"], x[0]["page_idx"]))

    labels = _load_labels(args.sample)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    index = ["# Arbitraggio", "",
             f"run={args.run} mode={args.mode} pagine={len(sel)} "
             f"(flaggate={len(flagged)}, pulite={len(clean)})", ""]
    for rec, base in sel:
        corpus = base.parent.name
        stem = base.name
        idx = rec["page_idx"]
        tag = f"{stem}_p{rec['page_ui']:04d}"
        d = out / tag
        d.mkdir(parents=True, exist_ok=True)
        md_path = base / "ir" / f"page_{idx:04d}.md"
        md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        (d / "md.txt").write_text(_IMG_RE.sub("[IMG]", md), encoding="utf-8")
        c = rec.get("checks") or {}
        lab = labels.get((rec["pdf"], idx), "")
        (d / "info.txt").write_text(
            f"{rec['pdf']} p{rec['page_ui']} label={lab} "
            f"engine={rec.get('engine')} flags={rec.get('flags')}\n"
            f"recall={c.get('text', {}).get('recall_pdf')} "
            f"render={c.get('text', {}).get('recall_render')} "
            f"fig={c.get('figures', {}).get('expected')}/"
            f"{c.get('figures', {}).get('embedded')}/blk"
            f"{c.get('figures', {}).get('blank')} "
            f"tbl_ok={c.get('tables', {}).get('ok')} "
            f"tbl_recall={c.get('tables', {}).get('recall')} "
            f"flow={rec.get('flow_score')} "
            f"order_inv={c.get('order', {}).get('inversions')} "
            f"struct_ok={c.get('structure', {}).get('ok')} "
            f"viol={c.get('structure', {}).get('note')}\n"
            f"diag={c.get('diag')}\n", encoding="utf-8")
        pdf_path = _ROOT / corpus / f"{stem}.pdf"
        try:
            with pymupdf.open(pdf_path) as doc:
                doc[idx].get_pixmap(
                    matrix=pymupdf.Matrix(2, 2)).save(str(d / "page.png"))
        except Exception as e:  # noqa: BLE001
            (d / "page.png.err").write_text(repr(e), encoding="utf-8")
        index.append(f"- {tag}  label={lab}  flags={rec.get('flags')}")
    (out / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"review: {out} ({len(sel)} pagine)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
