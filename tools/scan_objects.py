#!/usr/bin/env python3
"""Scan di disponibilità — figure/tabelle/box classificati per **tipo di span**.

Dev-only, offline. Usa il layer **geometrico** (content map + split di colonna,
indipendente dall'ordine emesso) per contare, per ogni oggetto, quante pagine lo
hanno *in-colonna*, *parziale* (attraversa ≥1 confine ma non tutti) o *full*
(attraversa tutti i confini / a tutta larghezza).

Serve a dimensionare i run settoriali (N = min(100, disponibili)).

Uso::

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/scan_objects.py \\
        --corpora corpus1,corpus2,corpus3 --scan-max 800 --seed 20261101 \\
        --out /tmp/opencode/scan_objects.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_OBJECTS = ("fig", "tab", "box")
_SPANS = ("in-colonna", "parziale", "full")


def _pool(corpora: list[str]) -> list[tuple[str, str, int]]:
    import pymupdf

    out: list[tuple[str, str, int]] = []
    for c in corpora:
        for pdf in sorted((_ROOT / c).glob("*.pdf")):
            try:
                d = pymupdf.open(pdf)
                n = len(d)
                d.close()
            except Exception:
                continue
            rel = str(pdf.relative_to(_ROOT))
            out.extend((c, rel, i) for i in range(n))
    return out


def span_of(bbox: tuple, splits: list[float]) -> str:
    """Classifica lo span di un oggetto rispetto ai confini di colonna."""
    x0, _y0, x1, _y1 = bbox
    if not splits:
        return "in-colonna"
    cross = [s for s in splits if x0 < s < x1]
    if not cross:
        return "in-colonna"
    return "full" if len(cross) >= len(splits) else "parziale"


def scan(corpora: list[str], scan_max: int, seed: int, cap: int) -> dict:
    import pymupdf

    import ir_layout
    import layout_proxies
    import main
    import verify_pages as vp

    pool = _pool(corpora)
    rng = random.Random(seed)
    rng.shuffle(pool)
    pool = pool[:scan_max]

    picks: dict[str, list[dict]] = defaultdict(list)
    counts: Counter = Counter()
    pages: list[dict] = []
    scanned = 0
    t0 = time.time()
    for c, rel, idx in pool:
        try:
            with pymupdf.open(_ROOT / rel) as doc:
                page = doc[idx]
                pw, ph = page.rect.width, page.rect.height
                _t, els = ir_layout.page_elements(doc, idx)
                try:
                    cls = layout_proxies.layout_class(els, pw)
                except Exception:
                    cls = "?"
                pages.append({"corpus": c, "pdf": rel, "page": idx,
                              "layout_class": cls})
                objs: list[tuple[str, tuple]] = []
                prose = [{"x0": e["bbox"][0], "x1": e["bbox"][2],
                          "y0": e["bbox"][1], "y1": e["bbox"][3]}
                         for e in els
                         if e["class"] in ("text", "section-header", "title")]
                splits = (vp._column_splits_robust(prose, pw)
                          if prose else [])
                if splits:  # lo span ha senso solo su pagine multi-colonna
                    for e in ir_layout.real_pictures(els, pw, ph):
                        objs.append(("fig", tuple(e["bbox"])))
                    for e in els:
                        if e["class"] == "table":
                            objs.append(("tab", tuple(e["bbox"])))
                    try:
                        for b in ir_layout._full_width_boxes(page, pw):
                            objs.append(("box", tuple(b)))
                    except Exception:
                        pass
                    for kind, bb in objs:
                        sp = span_of(bb, splits)
                        counts[f"{kind}_{sp}"] += 1
                        if sp in ("full", "parziale"):
                            b = f"{kind}_{sp}"
                            if len(picks[b]) < cap:
                                picks[b].append(
                                    {"corpus": c, "pdf": rel, "page": idx,
                                     "span": sp})
        except Exception:
            pass
        scanned += 1
        if scanned % 50 == 0:
            print(f"  scan {scanned}/{len(pool)} ({time.time() - t0:.0f}s) "
                  + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
                  flush=True)
    return {"seed": seed, "scan_max": scan_max, "scanned": scanned,
            "counts": dict(counts), "picks": dict(picks), "pages": pages}


def main() -> int:
    ap = argparse.ArgumentParser(description="Scan disponibilità oggetti")
    ap.add_argument("--corpora", default="corpus1,corpus2,corpus3")
    ap.add_argument("--scan-max", type=int, default=800)
    ap.add_argument("--seed", type=int, default=20261101)
    ap.add_argument("--cap", type=int, default=200,
                    help="tetto di candidati salvati per bucket")
    ap.add_argument("--out", default="/tmp/opencode/scan_objects.json")
    args = ap.parse_args()

    data = scan([c.strip() for c in args.corpora.split(",") if c.strip()],
                args.scan_max, args.seed, args.cap)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print("\n== DISPONIBILITÀ ==")
    for obj in _OBJECTS:
        row = {sp: data["counts"].get(f"{obj}_{sp}", 0) for sp in _SPANS}
        span_ok = (len(data["picks"].get(f"{obj}_full", []))
                   + len(data["picks"].get(f"{obj}_parziale", [])))
        print(f"  {obj:4} in-colonna={row['in-colonna']:5} "
              f"parziale={row['parziale']:5} full={row['full']:5} "
              f"→ candidati-span={span_ok}")
    print(f"scritte: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
