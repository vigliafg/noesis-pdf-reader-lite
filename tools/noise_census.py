#!/usr/bin/env python3
"""Censimento del **rumore** dell'output IR — metrica oggettiva, CI-safe.

Scansiona un campione di pagine **held-out** (random per PDF), produce il
markdown IR (senza embed delle figure, per velocità) + cosmetica, e conta
pattern-spia **non ambigui**. Aggrega per classe di layout e per PDF, e scrive
`noise.json` + `noise.md` con i peggiori offender.

Uso:
    .venv/bin/python tools/noise_census.py --per-pdf 4 --seed 20261004 \
        --out /tmp/opencode/noise

Non usa rete né advisor: è deterministico (a parità di seed) e adatto a
misurare i progressi nel tempo.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: pattern-spia non ambigui (catturano artefatti, non prosa legittima)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")
_GLUED_DIGIT = re.compile(r"[a-z]{4,}\d")          # bleed: "ther60 apies"
_DOUBLE_SPACE = re.compile(r"[A-Za-z]{2}  +[A-Za-z]{2}")
#: frammento di parola spezzata ("Dif f erences"): euristico, ristretto alle
#: intestazioni (dove l'artefatto del motore si manifesta)
_SPACED_FRAG_HEAD = re.compile(r"^#{1,6}\s.*\b[A-Za-z]{2,} [A-Za-z] [A-Za-z]{2,}\b")
_ORPHAN_BULLET = re.compile(r"^\s*[-*+]\s*$")
_STRAY_HASH = re.compile(r"^#{1,6}\s*$")
_HTML_RESIDUE = re.compile(r"<(?!/?(?:br|sup|sub|i|b|em|strong)\b)[a-zA-Z][a-zA-Z0-9]*>")


def _table_blocks(md: str):
    """Blocchi-tabella: liste di righe che iniziano con ``|``."""
    for block in re.split(r"\n\s*\n", md or ""):
        rows = [ln for ln in block.splitlines() if ln.strip().startswith("|")]
        if len(rows) >= 2:
            yield rows


def _is_sep_row(row: str) -> bool:
    return set(row.strip().strip("|")) <= set("-: |")


def count_noise(md: str) -> dict:
    """Conta i pattern-spia in ``md``. Ritorna ``{pattern: n, ...}`` (puro)."""
    md = md or ""
    c: Counter = Counter()
    c["control_char"] = len(_CONTROL.findall(md))
    c["glued_digit"] = len(_GLUED_DIGIT.findall(md))
    c["double_space"] = len(_DOUBLE_SPACE.findall(md))
    c["spaced_frag_heading"] = sum(
        1 for ln in md.splitlines() if _SPACED_FRAG_HEAD.match(ln))
    c["orphan_bullet"] = sum(1 for ln in md.splitlines() if _ORPHAN_BULLET.match(ln))
    c["stray_hash"] = sum(1 for ln in md.splitlines() if _STRAY_HASH.match(ln))
    c["html_residue"] = len(_HTML_RESIDUE.findall(md))
    lines = [ln.strip() for ln in md.splitlines() if len(ln.strip()) > 10]
    c["dup_line"] = sum(1 for a, b in zip(lines, lines[1:]) if a == b)
    for rows in _table_blocks(md):
        data = [r for r in rows if not _is_sep_row(r)]
        if len({r.count("|") for r in data}) > 1:
            c["table_ragged"] += 1
        for r in data:
            cells = r.strip().strip("|").split("|")
            c["empty_cell"] += sum(1 for x in cells if not x.strip())
    c["total"] = sum(v for k, v in c.items() if k != "total")
    return dict(c)


def _md_for_page(doc, page, idx: int, chunk: dict) -> str:
    import ir_layout
    import main
    md, _meta = ir_layout.build_markdown(
        page, doc, idx, embed_figures=False, return_meta=True, chunk=chunk)
    return main._cosmetic_ir(md or "")


def sample_and_measure(corpora, per_pdf: int, seed: int, max_scan: int = 0) -> dict:
    import pymupdf

    import ir_layout
    import layout_proxies
    import main

    rng = random.Random(seed)
    pdfs: list[Path] = []
    for corpus in corpora:
        pdfs += sorted((_ROOT / corpus).glob("*.pdf"))

    records: list[dict] = []
    for pdf in pdfs:
        try:
            doc = pymupdf.open(pdf)
        except Exception:
            continue
        try:
            n = len(doc)
            pool = list(range(n))
            if max_scan:
                pool = pool[:max_scan]
            idxs = sorted(rng.sample(pool, min(per_pdf, len(pool))))
            for idx in idxs:
                page = doc[idx]
                t0 = time.perf_counter()
                chunk = ir_layout.page_chunk(doc, idx)
                md = _md_for_page(doc, page, idx, chunk)
                ms = (time.perf_counter() - t0) * 1000
                _t, els = ir_layout._elements_from_chunk(chunk)
                cls = layout_proxies.layout_class(els, page.rect.width)
                nz = count_noise(md)
                try:
                    fixes = ir_layout._ligature_fixes(page)
                    nz["ligature_split"] = sum(1 for b in fixes if b in md)
                except Exception:
                    nz["ligature_split"] = 0
                nz["total"] = sum(v for k, v in nz.items() if k != "total")
                records.append({
                    "pdf": pdf.name, "page_idx": idx, "page_ui": idx + 1,
                    "class": cls, "ms": round(ms, 1), "noise": nz,
                    "chars": len(md),
                })
        finally:
            doc.close()
    return _aggregate(records)


def _aggregate(records: list[dict]) -> dict:
    patterns = ("control_char", "glued_digit", "double_space",
                "spaced_frag_heading", "orphan_bullet", "stray_hash",
                "html_residue", "dup_line", "table_ragged", "empty_cell",
                "ligature_split")
    totals: Counter = Counter()
    pages_with: Counter = Counter()
    per_class: dict[str, Counter] = defaultdict(Counter)
    per_class_n: Counter = Counter()
    offenders: list[dict] = []
    ms: list[float] = []
    for r in records:
        nz = r["noise"]
        ms.append(r["ms"])
        per_class_n[r["class"]] += 1
        for p in patterns:
            v = nz.get(p, 0)
            totals[p] += v
            if v:
                pages_with[p] += 1
                per_class[r["class"]][p] += v
                offenders.append({"pdf": r["pdf"], "page_ui": r["page_ui"],
                                  "class": r["class"], "pattern": p, "n": v})
    offenders.sort(key=lambda o: -o["n"])
    return {
        "n_pages": len(records),
        "n_noise": sum(totals.values()),
        "totals": dict(totals),
        "pages_with": dict(pages_with),
        "per_class": {k: dict(v) for k, v in per_class.items()},
        "per_class_n": dict(per_class_n),
        "ms_avg": round(sum(ms) / len(ms), 1) if ms else 0.0,
        "offenders": offenders[:25],
        "records": records,
    }


def _write_report(agg: dict, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "noise.json").write_text(json.dumps(agg, indent=2), encoding="utf-8")
    L: list[str] = []
    L.append("# Censimento rumore IR\n")
    L.append(f"- pagine: **{agg['n_pages']}** · eventi di rumore: "
             f"**{agg['n_noise']}** · media: "
             f"**{agg['n_noise'] / max(1, agg['n_pages']):.2f}/pagina**")
    L.append(f"- tempo medio estrazione (no figure): **{agg['ms_avg']} ms/pagina**\n")
    L.append("| pattern | eventi | pagine |")
    L.append("|---|---:|---:|")
    for p, n in sorted(agg["totals"].items(), key=lambda kv: -kv[1]):
        L.append(f"| {p} | {n} | {agg['pages_with'].get(p, 0)} |")
    L.append("\n## Per classe\n")
    L.append("| classe | pagine | rumore | pattern |")
    L.append("|---|---:|---:|---|")
    for cls, n in sorted(agg["per_class_n"].items()):
        tot = sum(agg["per_class"].get(cls, {}).values())
        pat = ", ".join(f"{k}:{v}" for k, v in
                        sorted(agg["per_class"].get(cls, {}).items(),
                               key=lambda kv: -kv[1]))
        L.append(f"| {cls} | {n} | {tot} | {pat} |")
    L.append("\n## Peggiori offender\n")
    L.append("| pdf | pag | classe | pattern | n |")
    L.append("|---|---:|---|---|---:|")
    for o in agg["offenders"]:
        L.append(f"| {o['pdf']} | {o['page_ui']} | {o['class']} | "
                 f"{o['pattern']} | {o['n']} |")
    (out / "noise.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Censimento rumore IR")
    ap.add_argument("--corpus", default="corpus1,corpus2")
    ap.add_argument("--per-pdf", type=int, default=4)
    ap.add_argument("--seed", type=int, default=20261004)
    ap.add_argument("--max-scan", type=int, default=0,
                    help="limita le pagine scansionate per PDF (0 = tutte)")
    ap.add_argument("--out", default="/tmp/opencode/noise")
    args = ap.parse_args(argv)

    try:
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            QApplication([])
    except Exception:
        pass
    import main
    try:
        main.init_config(str(main._config_file_path()))
        main.set_language("it")
    except Exception:
        pass

    agg = sample_and_measure(args.corpus.split(","), args.per_pdf,
                             args.seed, args.max_scan)
    out = Path(args.out)
    _write_report(agg, out)
    print(f"Pagine {agg['n_pages']} · rumore {agg['n_noise']} "
          f"({agg['n_noise'] / max(1, agg['n_pages']):.2f}/pagina) · "
          f"{agg['ms_avg']} ms/pagina")
    for p, n in sorted(agg["totals"].items(), key=lambda kv: -kv[1]):
        if n:
            print(f"  {p:20s} {n:5d}  (pagine {agg['pages_with'].get(p, 0)})")
    print(f"Report: {out/'noise.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
