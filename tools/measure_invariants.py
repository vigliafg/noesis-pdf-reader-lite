#!/usr/bin/env python3
"""Misura gli invarianti strutturali (``tools/invariants.py``) su un run E2E.

Uso:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_invariants.py \\
        ~/.local/share/opencode/e2e250l4 [--limit N]

Per ogni pagina del run riapre il PDF, ricostruisce gli elementi (content map) e
i box a tutta larghezza, legge l'md salvato e valuta I1/I3. Stampa il tasso per
tipo e l'elenco delle pagine violate.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    import pymupdf

    import invariants
    import ir_layout

    root = Path(args.run_dir)
    recs = [json.loads(ln) for ln in
            open(root / "records_partial.jsonl", encoding="utf-8") if ln.strip()]
    mds: dict[tuple[str, int], Path] = {}
    for p in glob.glob(str(root / "blocks" / "*" / "*" / "ir" / "page_*.md")):
        mds[(Path(p).parent.parent.name, int(Path(p).stem.split("_")[1]))] = Path(p)

    kinds: Counter = Counter()
    pages_bad: list[tuple] = []
    n = 0
    for r in recs:
        if args.limit and n >= args.limit:
            break
        key = (Path(r["pdf"]).stem, r["page_idx"])
        mdp = mds.get(key)
        if mdp is None:
            continue
        pdf = _ROOT / r.get("corpus", "corpus1") / r["pdf"]
        try:
            doc = pymupdf.open(pdf)
            page = doc[r["page_idx"]]
            _t, els = ir_layout.page_elements(doc, r["page_idx"])
            boxes = ir_layout._full_width_boxes(page, page.rect.width)
            md = mdp.read_text(encoding="utf-8")
            res = invariants.check_structure(md, els, boxes, page.rect.width)
            doc.close()
        except Exception as e:  # noqa: BLE001
            print(f"  ! {key}: {e!r}")
            continue
        n += 1
        if not res["ok"]:
            vs = res["violations"]
            for v in vs:
                kinds[v["kind"]] += 1
            pages_bad.append((key, r.get("engine"), vs))

    print(f"pagine analizzate: {n} | pagine con violazioni: {len(pages_bad)}")
    print("per tipo:", dict(kinds))
    for key, eng, vs in pages_bad:
        print(f"  {key[0]} p{key[1] + 1} engine={eng} -> "
              + "; ".join(f"{v['invariant']}:{v['kind']}"
                          + (f"(inv {v['inversions']})" if "inversions" in v else "")
                          for v in vs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
