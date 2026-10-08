#!/usr/bin/env python3
"""Confronto **non distruttivo** ordine IR vs ordine page model (dev-only).

Per ogni pagina calcola l'ordine di lettura realmente **emesso** da
``ir_layout.build_markdown`` (hook ``order_log``) e l'ordine **indipendente** del
page model (traversal del ``body``), li abbina per IoU e conta le **inversioni**
(tau di Kendall) tra i due. NON modifica alcun markdown emesso.

Uso:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_order.py \\
        corpus1/ce24.pdf:480 corpus2/fe22.pdf:1038 ...
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_order.py --sample 50
"""

from __future__ import annotations

import argparse
import glob
import os
import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_LEAF_KINDS = ("texts", "tables", "pictures")


def _pm_body_order(doc: dict) -> list[tuple[str, str, tuple]]:
    """Foglie del ``body`` in ordine di traversal: ``(ref, label, bbox)``."""
    kinds = {k: doc.get(k) or [] for k in ("groups", "texts", "tables", "pictures")}

    def children(ref: str) -> list[str]:
        kind, idx = ref.split("/")[1], int(ref.split("/")[2])
        if kind not in kinds or not (0 <= idx < len(kinds[kind])):
            return []
        return [c["cref"] for c in (kinds[kind][idx].get("children") or [])]

    out: list[tuple[str, str, tuple]] = []

    def dfs(ref: str) -> None:
        kind = ref.split("/")[1]
        if kind in _LEAF_KINDS:
            idx = int(ref.split("/")[2])
            o = kinds[kind][idx]
            bb = ((o.get("prov") or [{}])[0].get("bbox") or {})
            bbox = (bb.get("l", 0.0), bb.get("t", 0.0),
                    bb.get("r", 0.0), bb.get("b", 0.0))
            out.append((ref, o.get("label", kind), bbox))
        for c in children(ref):
            dfs(c)

    for c in (doc["body"]["children"] or []):
        dfs(c["cref"])
    return out


def _inversions(seq: list[int]) -> int:
    n = len(seq)
    return sum(1 for i in range(n) for j in range(i + 1, n) if seq[i] > seq[j])


def _compare(pm_order, engine_log, thresh: float = 0.5) -> dict:
    import page_model as pm
    cover = pm._cover  # frazione del blocco engine contenuta nella foglia pm
    pm_boxes = [o[2] for o in pm_order]
    # per ogni blocco engine: foglia pm che lo contiene di più
    best_pos: dict[int, int] = {}   # rank foglia pm -> prima posizione engine
    for epos, e in enumerate(engine_log):
        bb = tuple(e["bbox"])
        if not (bb[2] > bb[0] and bb[3] > bb[1]):
            continue
        bi, bv = -1, thresh
        for r, pb in enumerate(pm_boxes):
            v = cover(bb, pb)
            if v > bv:
                bi, bv = r, v
        if bi >= 0 and bi not in best_pos:
            best_pos[bi] = epos
    seq = [r for _, r in sorted((p, r) for r, p in best_pos.items())]
    inv = _inversions(seq)
    pairs = len(seq) * (len(seq) - 1) // 2
    return {
        "engine_blocks": len(engine_log),
        "pm_leaves": len(pm_order),
        "matched": len(seq),
        "engine_unmatched": len(engine_log) - len(best_pos),
        "inversions": inv,
        "pairs": pairs,
        "ratio": (inv / pairs) if pairs else 0.0,
    }


def _specs(args) -> list[tuple[str, int]]:
    specs = []
    for s in args.specs:
        pdf, _, page = s.rpartition(":")
        specs.append((pdf, int(page)))
    if args.sample:
        pdfs = sorted(glob.glob(str(_ROOT / "corpus1" / "*.pdf"))
                      + glob.glob(str(_ROOT / "corpus2" / "*.pdf"))
                      + glob.glob(str(_ROOT / "corpus3" / "*.pdf")))
        import pymupdf
        rng = random.Random(args.seed)
        target = args.sample + len(args.specs)
        attempts = 0
        while len(specs) < target and attempts < 20 * target:
            attempts += 1
            p = rng.choice(pdfs)
            try:
                n = pymupdf.open(p).page_count
            except Exception:
                continue
            if n:
                specs.append((p, rng.randrange(n) + 1))
    return specs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="*", help="pdf:pagina (1-based)")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20261024)
    args = ap.parse_args()

    import pymupdf
    import page_model
    import ir_layout

    specs = _specs(args)
    rows = []
    for pdf, page in specs:
        with pymupdf.open(pdf) as doc:
            p = doc[page - 1]
            chunk = ir_layout.page_chunk(doc, page - 1)
            log: list = []
            try:
                ir_layout.build_markdown(p, doc, page - 1, chunk=chunk,
                                         return_meta=True, order_log=log)
            except Exception as e:  # noqa: BLE001
                print(f"ERR  {Path(pdf).name} p{page}: {e!r}")
                continue
        # il page model DEVE leggere una page **pristina**: ``build_markdown``
        # (via pymupdf4llm) aggiunge un layer OCR alla page → si riapre il PDF,
        # altrimenti lo "scheletro indipendente" leggerebbe testo mutato.
        pmd = page_model.build_page_document(pdf, page - 1).to_dict()
        res = _compare(_pm_body_order(pmd), log)
        rows.append((Path(pdf).name, page, res))
        mark = "*" if res["inversions"] else " "
        print(f"{mark} {Path(pdf).name} p{page}: inv={res['inversions']}/"
              f"{res['pairs']} ({res['ratio']:.2f})  "
              f"matched={res['matched']} engine_unmatched={res['engine_unmatched']}"
              f" pm_leaves={res['pm_leaves']}")

    if rows:
        tot_inv = sum(r[2]["inversions"] for r in rows)
        tot_pairs = sum(r[2]["pairs"] for r in rows)
        clean = sum(1 for r in rows if r[2]["inversions"] == 0)
        worst = sorted(rows, key=lambda r: r[2]["inversions"], reverse=True)[:5]
        print(f"\npagine={len(rows)}  senza inversioni={clean}  "
              f"inv totali={tot_inv}/{tot_pairs} "
              f"({(tot_inv / tot_pairs) if tot_pairs else 0:.3f})")
        print("peggiori:")
        for name, page, r in worst:
            print(f"  {name} p{page}: inv={r['inversions']}/{r['pairs']} "
                  f"({r['ratio']:.2f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
