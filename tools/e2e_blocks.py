#!/usr/bin/env python3
"""Campione E2E **a blocchi** con dati parziali per blocco (advisor su tutte le pagine).

Differenze da ``e2e_sample.py``: le pagine del campione sono spezzate in
**blocchi di N pagine** (default 2), anche se appartengono a PDF diversi. Ogni
blocco ha la sua directory ``blocks/block_XX/`` con i report per PDF, un
``report.jsonl`` aggregato del blocco e un ``done.json``: il run è **riprendibile**
(``--resume`` salta i blocchi già completati). Alla fine aggrega tutto con
``e2e_sample.aggregate`` (``summary_all.md`` + ``defects_all.jsonl`` +
``verdict.json``).

Esempio::

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/e2e_blocks.py --build \\
        --corpora corpus1,corpus2 --per-corpus 5 --block-size 2 --seed 20261004 \\
        --mode advisor --out /tmp/opencode/e2e10
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import e2e_sample  # noqa: E402


def _run_e2e(pdf: str, pages: list[int], out: Path, args) -> None:
    cmd = [sys.executable, str(_TOOLS / "e2e.py"), pdf,
           "--pages", ",".join(str(p) for p in sorted(pages)),
           "--out", str(out), "--via-app", "--mode", args.mode,
           "--pipelines", args.pipelines,
           "--advisor-retries", str(args.advisor_retries),
           "--advisor-delay", str(args.advisor_delay)]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                       timeout=args.block_timeout)
    if r.returncode not in (0, 2):
        print(f"    ! {pdf} rc={r.returncode}: {r.stderr[-500:]}")


def _read_records(bdir: Path) -> list[dict]:
    """Legge tutti i ``report.jsonl`` dei PDF dentro un blocco."""
    recs: list[dict] = []
    for rj in sorted(bdir.glob("*/report.jsonl")):
        for ln in rj.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                recs.append(json.loads(ln))
    return recs


def _run_block(block: list[dict], idx: int, out: Path, args) -> list[dict]:
    bdir = out / "blocks" / f"block_{idx:02d}"
    bdir.mkdir(parents=True, exist_ok=True)
    done = bdir / "done.json"
    if args.resume and done.exists():
        block = json.loads((bdir / "sample.json").read_text(encoding="utf-8"))
        recs = _read_records(bdir)
        print(f"blocco {idx:02d}: già fatto ({len(recs)} record) [resume]")
    else:
        (bdir / "sample.json").write_text(
            json.dumps(block, ensure_ascii=False, indent=2), encoding="utf-8")
        by_pdf: dict[str, list[int]] = {}
        for it in block:
            by_pdf.setdefault(it["pdf"], []).append(it["page"])
        print(f"blocco {idx:02d}: {len(block)} pagine su {len(by_pdf)} PDF")
        for pdf, pages in by_pdf.items():
            sub = bdir / Path(pdf).stem
            sub.mkdir(parents=True, exist_ok=True)
            print(f"   {pdf} -> {sorted(pages)}")
            _run_e2e(pdf, pages, sub, args)
        recs = _read_records(bdir)
        (bdir / "report.jsonl").write_text(
            "\n".join(json.dumps(x, ensure_ascii=False) for x in recs),
            encoding="utf-8")
        done.write_text(json.dumps({"block": idx, "n": len(recs)}),
                        encoding="utf-8")
    # attribuisci corpus/blocco (il report di e2e non li conosce)
    index = {(Path(it["pdf"]).name, it["page"]): it for it in block}
    for r in recs:
        it = index.get((r["pdf"], r["page_idx"]))
        if it:
            r["corpus"] = it["corpus"]
        r["block"] = idx
    return recs


def _write_blocks_report(out: Path, blocks: list[list[dict]]) -> None:
    lines = ["# Report per blocco", ""]
    for i, recs in enumerate(blocks):
        real = [r for r in recs if r.get("flags")]
        arb = sum(1 for r in recs
                  if (r.get("arbitration") or {}).get("mode") == "advisor")
        lines += [f"## blocco {i:02d} ({len(recs)} pagine, {arb} advisor, "
                  f"{len(real)} con flag)", ""]
        if not recs:
            lines.append("_nessun record_")
            lines.append("")
            continue
        lines += ["| pagina | engine | conf | recall | flow | order | fig | tbl | flag | verdetto |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in recs:
            c = r["checks"]
            v = (r.get("arbitration") or {}).get("verdict")
            vs = json.dumps(v, ensure_ascii=False)[:80] if v else ""
            conf = r.get("confidence")
            lines.append(
                f"| {r['pdf']} p{r['page_ui']} | {r.get('engine')} | "
                f"{conf if conf is None else round(conf, 2)} | "
                f"{c['text']['recall_pdf']:.3f} | "
                f"{r.get('flow_score', 1.0):.2f} | {r['order_score']:.2f} | "
                f"{c['figures']['embedded']}/{c['figures']['expected']} | "
                f"{'ok' if c['tables']['ok'] else 'KO'} | "
                f"{'; '.join(r['flags'])[:60]} | {vs} |")
        lines.append("")
    (out / "summary_blocks.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Campione E2E a blocchi")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--sample", default=None, help="sample.json esistente")
    ap.add_argument("--corpora", default="corpus1,corpus2")
    ap.add_argument("--per-corpus", type=int, default=5)
    ap.add_argument("--block-size", type=int, default=2)
    ap.add_argument("--seed", type=int, default=20261004)
    ap.add_argument("--mode", choices=("auto", "visual", "advisor"),
                    default="advisor")
    ap.add_argument("--pipelines", default="ir")
    ap.add_argument("--out", default="/tmp/opencode/e2e10")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--advisor-retries", type=int, default=5)
    ap.add_argument("--advisor-delay", type=float, default=3.0)
    ap.add_argument("--block-timeout", type=float, default=1800.0)
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    if args.sample:
        data = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    elif args.build:
        e2e_sample._CORPORA = tuple(args.corpora.split(","))
        data = e2e_sample.build_sample(args.per_corpus, args.seed)
        (out / "sample.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        data = json.loads((out / "sample.json").read_text(encoding="utf-8"))

    e2e_sample.sample_meta = data["sample"]
    e2e_sample.sample_classes = data.get("classes") or {}

    items: list[dict] = []
    for corpus, vals in data["sample"].items():
        for v in vals:
            items.append({"corpus": corpus, "pdf": v["pdf"], "page": v["page"]})
    print(f"campione: {len(items)} pagine da {args.corpora} "
          f"(blocchi da {args.block_size})")

    blocks: list[list[dict]] = [items[i:i + args.block_size]
                                for i in range(0, len(items), args.block_size)]
    all_records: list[dict] = []
    per_block: list[list[dict]] = []
    for i, block in enumerate(blocks):
        recs = _run_block(block, i, out, args)
        per_block.append(recs)
        all_records.extend(recs)
        # salvataggio parziale cumulativo ad ogni blocco
        (out / "records_partial.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in all_records),
            encoding="utf-8")

    _write_blocks_report(out, per_block)
    verdict = e2e_sample.aggregate(all_records, out)
    print("\n== VERDETTO FINALE ==")
    print(json.dumps({k: verdict[k] for k in
                      ("pages", "clean_pages", "real_defects", "marginalia",
                       "kinds")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
