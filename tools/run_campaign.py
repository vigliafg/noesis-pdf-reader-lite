#!/usr/bin/env python3
"""Esegue un run della campagna su un ``sample.json`` (percorso **diretto**).

Come ``e2e_sample.run_sample`` ma senza ``--via-app`` (più veloce per il
volume): invoca ``tools/e2e.py`` per ogni PDF e aggrega con
``e2e_sample.aggregate``. Il via-app resta per lo spot-check.

Uso::

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/run_campaign.py \\
        --sample /tmp/opencode/campaign/general_G1.json \\
        --out /tmp/opencode/campaign/runs/G1
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TOOLS = Path(__file__).resolve().parent
for _p in (str(_ROOT), str(_TOOLS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import e2e_sample  # noqa: E402


def _run_pdf(pdf: str, pages: list[int], out: Path) -> None:
    cmd = [sys.executable, str(_TOOLS / "e2e.py"), pdf,
           "--pages", ",".join(str(p) for p in sorted(pages)),
           "--out", str(out), "--mode", "auto", "--pipelines", "ir"]
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True,
                           errors="replace", timeout=7200)
    except Exception as e:  # noqa: BLE001  (un PDF che uccide il figlio non
        print(f"  !! {pdf}: subprocess {e!r}")  # deve fermare il run)
        return
    if r.returncode not in (0, 2):
        print(f"  ! {pdf} rc={r.returncode}: {(r.stderr or '')[-300:]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run di campagna (diretto)")
    ap.add_argument("--sample", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    data = json.loads(Path(args.sample).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    e2e_sample.sample_meta = data.get("sample") or {}
    e2e_sample.sample_classes = data.get("classes") or {}

    records: list[dict] = []
    for corpus, items in (data.get("sample") or {}).items():
        by_pdf: dict[str, list[int]] = defaultdict(list)
        for it in items:
            by_pdf[it["pdf"]].append(it["page"])
        print(f"== {corpus}: {len(items)} pagine su {len(by_pdf)} PDF ==",
              flush=True)
        for pdf, pages in by_pdf.items():
            sub = out / corpus / Path(pdf).stem
            sub.mkdir(parents=True, exist_ok=True)
            print(f"   {pdf} -> {sorted(pages)}", flush=True)
            _run_pdf(pdf, pages, sub)
            rj = sub / "report.jsonl"
            if rj.exists():
                for ln in rj.read_text(encoding="utf-8").splitlines():
                    if not ln.strip():
                        continue
                    try:
                        rec = json.loads(ln)
                    except Exception:
                        continue  # report troncato (figlio ucciso): salta
                    rec["corpus"] = corpus
                    records.append(rec)
    verdict = e2e_sample.aggregate(records, out)
    print("\n== VERDETTO ==")
    print(json.dumps({k: verdict[k] for k in
                      ("pages", "clean_pages", "real_defects", "kinds")},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
