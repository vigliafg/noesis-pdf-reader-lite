#!/usr/bin/env python3
"""Test E2E a campione su più corpus, con advisor visivo — per blocchi.

Costruisce un campione di N pagine casuali per corpus, esegue `tools/e2e.py`
(`--via-app --mode advisor`) **PDF per PDF** (i blocchi) e aggrega un report
complessivo con verdetto.

Esempi:
    .venv/bin/python tools/e2e_sample.py --build --per-corpus 20 \
        --seed 20261005 --out /tmp/opencode/e2e60
    .venv/bin/python tools/e2e_sample.py --sample /tmp/opencode/e2e60/sample.json \
        --out /tmp/opencode/e2e60
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_CORPORA = ("corpus1", "corpus2", "corpus3")


def _list_pdfs(corpus: str) -> list[Path]:
    return sorted((_ROOT / corpus).glob("*.pdf"))


def build_sample(per_corpus: int, seed: int) -> dict:
    """Campione di ``per_corpus`` pagine per corpus, pesato sul n. di pagine."""
    import pymupdf

    rng = random.Random(seed)
    sample: dict[str, list[dict]] = {}
    for corpus in _CORPORA:
        pool: list[tuple[str, int]] = []
        for pdf in _list_pdfs(corpus):
            try:
                d = pymupdf.open(pdf)
                n = len(d)
                d.close()
            except Exception:
                continue
            pool.extend((str(pdf.relative_to(_ROOT)), i) for i in range(n))
        picks = rng.sample(pool, min(per_corpus, len(pool)))
        sample[corpus] = [{"pdf": p, "page": i} for p, i in sorted(picks)]
    return {"seed": seed, "per_corpus": per_corpus, "sample": sample}


def _run_pdf(pdf: str, pages: list[int], out: Path, mode: str,
             retries: int, delay: int, extra: list[str]) -> None:
    cmd = [sys.executable, str(_TOOLS / "e2e.py"), pdf,
           "--pages", ",".join(str(p) for p in pages),
           "--out", str(out), "--via-app", "--mode", mode,
           "--advisor-retries", str(retries), "--advisor-delay", str(delay),
           *extra]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=3600)
    if r.returncode not in (0, 2):
        print(f"  ! {pdf} rc={r.returncode}: {r.stderr[-400:]}")


def run_sample(sample: dict, out: Path, mode: str, retries: int, delay: int,
               extra: list[str]) -> list[dict]:
    records: list[dict] = []
    for corpus, items in sample["sample"].items():
        by_pdf: dict[str, list[int]] = defaultdict(list)
        for it in items:
            by_pdf[it["pdf"]].append(it["page"])
        print(f"== blocco {corpus}: {len(items)} pagine su {len(by_pdf)} PDF ==")
        for pdf, pages in by_pdf.items():
            sub = out / corpus / Path(pdf).stem
            sub.mkdir(parents=True, exist_ok=True)
            print(f"   {pdf} -> {pages}")
            _run_pdf(pdf, sorted(pages), sub, mode, retries, delay, extra)
            rj = sub / "report.jsonl"
            if rj.exists():
                for ln in rj.read_text(encoding="utf-8").splitlines():
                    if ln.strip():
                        rec = json.loads(ln)
                        rec["corpus"] = corpus
                        records.append(rec)
    return records


def _defects(records: list[dict]) -> list[dict]:
    import e2e
    return e2e._collect_defects(records)


def aggregate(records: list[dict], out: Path) -> dict:
    defects = _defects(records)
    corpus_of = {(r["pdf"], r["page_idx"]): r["corpus"] for r in records}
    for d in defects:
        d["corpus"] = corpus_of.get((d["pdf"], d["page_idx"]), "?")
    real = [d for d in defects if d["real"]]
    marg = [d for d in defects if not d["real"]]
    pages = {(r["corpus"], r["pdf"], r["page_idx"]) for r in records}
    kinds = Counter(d["kind"] for d in real)
    per_corpus: dict[str, dict] = {}
    for c in _CORPORA:
        rows = [r for r in records if r["corpus"] == c]
        if not rows:
            continue
        dr = [d for d in real if d["corpus"] == c]
        eng = Counter(r.get("engine") for r in rows)
        per_corpus[c] = {
            "pages": len({r["page_idx"] for r in rows}),
            "engine": dict(eng),
            "text_ok": sum(1 for r in rows if r["checks"]["text"]["ok"]),
            "fig_ok": sum(1 for r in rows if r["checks"]["figures"]["ok"]),
            "tbl_ok": sum(1 for r in rows if r["checks"]["tables"]["ok"]),
            "auto_flags": sum(1 for r in rows if r["flags"]),
            "real_defects": len(dr),
            "defects_kinds": dict(Counter(d["kind"] for d in dr)),
            "secs_ext": round(sum(r["secs"] for r in rows), 1),
        }
    arb_rows = [r for r in records
                if (r.get("arbitration") or {}).get("mode") == "advisor"]
    arb_secs = sum((r["arbitration"].get("secs") or 0) for r in arb_rows)

    # scrivi difetti aggregati
    (out / "defects_all.jsonl").write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in defects),
        encoding="utf-8")

    n_pages = len(pages)
    n_clean = n_pages - len({(d["corpus"], d["pdf"], d["page_idx"]) for d in real})
    lines = [
        "# Test E2E a campione (60 pagine, advisor visivo)",
        "",
        f"- pagine: **{n_pages}** ({' + '.join(f'{c}:{len(v)}' for c, v in sample_meta.items())})",
        f"- modalità: advisor · engine IR (fallback `current` dove il gate fallisce)",
        f"- pagine senza difetti reali: **{n_clean}/{n_pages}**",
        f"- difetti reali: **{len(real)}** · marginalia (escluse): {len(marg)}",
        f"- advisor: {len(arb_rows)} chiamate · {arb_secs:.0f}s",
        f"- estrazione (somma): {sum(r['secs'] for r in records):.0f}s",
        "",
        "## Tipi di difetto (reali)",
        "",
    ]
    for k, v in kinds.most_common():
        lines.append(f"- `{k}`: {v}")
    lines += ["", "## Per corpus", "",
              "| corpus | pagine | engine | text ok | fig ok | tbl ok | flag auto | difetti reali |",
              "|---|---|---|---|---|---|---|---|"]
    for c, s in per_corpus.items():
        eng = ", ".join(f"{k}={v}" for k, v in s["engine"].items())
        lines.append(
            f"| {c} | {s['pages']} | {eng} | {s['text_ok']}/{s['pages']} | "
            f"{s['fig_ok']}/{s['pages']} | {s['tbl_ok']}/{s['pages']} | "
            f"{s['auto_flags']} | {s['real_defects']} |")
    lines += ["", "## Difetti reali per tipo/corpus", "",
              "| corpus | pagina | engine | tipo | sev | nota |",
              "|---|---|---|---|---|---|"]
    for d in real:
        note = (d["note"] or "").replace("|", "/")[:110]
        lines.append(
            f"| {d['corpus']} | {Path(d['pdf']).name} p{d['page_ui']} | "
            f"{d.get('engine')} | {d['kind']} | {d['severity']} | {note} |")
    (out / "summary_all.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    verdict = {
        "pages": n_pages,
        "clean_pages": n_clean,
        "real_defects": len(real),
        "marginalia": len(marg),
        "kinds": dict(kinds),
        "per_corpus": per_corpus,
        "advisor_calls": len(arb_rows),
        "advisor_secs": round(arb_secs, 1),
        "extraction_secs": round(sum(r["secs"] for r in records), 1),
    }
    (out / "verdict.json").write_text(json.dumps(verdict, ensure_ascii=False,
                                                 indent=2), encoding="utf-8")
    print(f"\nReport: {out/'summary_all.md'}")
    print(f"Pagine senza difetti reali: {n_clean}/{n_pages} | "
          f"difetti reali: {len(real)} | marginalia: {len(marg)}")
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser(description="Test E2E a campione (multi-corpus)")
    ap.add_argument("--build", action="store_true",
                    help="costruisci il campione casuale")
    ap.add_argument("--per-corpus", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--sample", default=None, help="sample.json esistente")
    ap.add_argument("--out", default="/tmp/opencode/e2e60")
    ap.add_argument("--mode", choices=("auto", "visual", "advisor"),
                    default="advisor")
    ap.add_argument("--advisor-retries", type=int, default=5)
    ap.add_argument("--advisor-delay", type=int, default=3)
    ap.add_argument("extra", nargs="*", help="argomenti extra per e2e.py")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    global sample_meta
    if args.build:
        data = build_sample(args.per_corpus, args.seed)
        (out / "sample.json").write_text(json.dumps(data, ensure_ascii=False,
                                                    indent=2), encoding="utf-8")
        print(f"Campione: {out/'sample.json'}")
    elif args.sample:
        data = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    else:
        sp = out / "sample.json"
        if not sp.exists():
            ap.error("serve --build o --sample")
        data = json.loads(sp.read_text(encoding="utf-8"))
    sample_meta = data["sample"]
    records = run_sample(data, out, args.mode, args.advisor_retries,
                         args.advisor_delay, args.extra)
    aggregate(records, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
