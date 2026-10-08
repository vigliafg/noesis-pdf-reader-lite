#!/usr/bin/env python3
"""Invarianti dello **scheletro geometrico** del page model (dev-only).

Costruisce il page model di una pagina (``page_model.build_page_document``) e
verifica proprietà **strutturali** che non dipendono dalla semantica (GNN):

- S1 albero: ogni ``cref`` risolve, un solo padre, nessun ciclo, tutte le foglie
  raggiungibili dal ``body``/``furniture``.
- S2 provenienza: ogni foglia ha un bbox ben formato.
- S3 tabella: la griglia coincide con ``num_rows × num_cols``; le celle coprono
  esattamente gli slot non vuoti; gli offset sono nei limiti.
- S4 ordine: il traversal (DFS) elenca ogni foglia esattamente una volta.
- S5 colonne: entro un gruppo ``region=column`` le foglie hanno ``y0`` non
  decrescente.

Uso:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_page_model.py \\
        corpus1/ce24.pdf:480 corpus2/fe22.pdf:1038 ...
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_page_model.py \\
        --sample 40
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


def check_skeleton(doc: dict) -> list[str]:
    """Ritorna l'elenco delle violazioni degli invarianti dello scheletro."""
    v: list[str] = []
    kinds = {k: doc.get(k) or [] for k in ("groups", "texts", "tables", "pictures")}

    def obj(ref: str):
        try:
            kind, idx = ref.split("/")[1], int(ref.split("/")[2])
        except Exception:
            return None
        if kind not in kinds or not (0 <= idx < len(kinds[kind])):
            return None
        return kinds[kind][idx]

    def children(ref: str) -> list[str]:
        o = obj(ref)
        if o is None:
            return []
        return [c["cref"] for c in (o.get("children") or [])]

    # ── S1: risoluzione, un solo padre, niente cicli, raggiungibilità ──
    roots = [c["cref"] for c in (doc["body"]["children"] or [])]
    roots += [c["cref"] for c in (doc["furniture"]["children"] or [])]
    parent: dict[str, str] = {}
    seen: set[str] = set()
    stack = list(roots)
    cyclic = False
    while stack:
        r = stack.pop()
        if r in seen:
            cyclic = True  # già visitato → ciclo o doppio padre
            continue
        seen.add(r)
        o = obj(r)
        if o is None:
            v.append(f"S1 cref irrisolvibile: {r}")
            continue
        for c in children(r):
            if c in parent:
                v.append(f"S1 doppio padre: {c} ({parent[c]} e {r})")
            parent[c] = r
            stack.append(c)

    reachable_leaves: list[str] = []
    for r in seen:
        kind = r.split("/")[1]
        if kind in _LEAF_KINDS:
            reachable_leaves.append(r)
    all_leaves = [f"#/{k}/{i}" for k in _LEAF_KINDS
                  for i in range(len(kinds[k]))]
    missing = [r for r in all_leaves if r not in seen]
    if missing:
        v.append(f"S1 foglie non raggiungibili: {len(missing)} (es. {missing[0]})")
    if cyclic:
        v.append("S1 ciclo o referenza ripetuta nell'albero")

    # ── S2: provenienza ben formata ──
    for r in reachable_leaves:
        o = obj(r)
        prov = o.get("prov") or []
        if not prov:
            v.append(f"S2 senza provenienza: {r}")
            continue
        bb = prov[0].get("bbox")
        if not bb or not (bb["l"] <= bb["r"] and bb["t"] <= bb["b"]):
            v.append(f"S2 bbox malformato: {r}")

    # ── S3: griglia tabella ──
    for ti, t in enumerate(kinds["tables"]):
        d = t.get("data") or {}
        nr, nc = d.get("num_rows", 0), d.get("num_cols", 0)
        grid = d.get("grid") or []
        if len(grid) != nr or any(len(row) != nc for row in grid):
            v.append(f"S3 tabella {ti}: griglia != {nr}x{nc}")
            continue
        nonnull = sum(1 for row in grid for c in row if c is not None)
        cover = sum((c.get("row_span", 1) * c.get("col_span", 1))
                    for c in (d.get("table_cells") or []))
        if nonnull != cover:
            v.append(f"S3 tabella {ti}: coverage {cover} != slot {nonnull}")
        for c in d.get("table_cells") or []:
            if not (0 <= c["start_row_offset_idx"] < c["end_row_offset_idx"] <= nr
                    and 0 <= c["start_col_offset_idx"] < c["end_col_offset_idx"] <= nc):
                v.append(f"S3 tabella {ti}: offset cella fuori griglia")
                break

    # ── S4: ordine = permutazione delle foglie ──
    order: list[str] = []

    def dfs(r: str) -> None:
        kind = r.split("/")[1]
        if kind in _LEAF_KINDS:
            order.append(r)
        for c in children(r):
            dfs(c)

    for r in [c["cref"] for c in (doc["body"]["children"] or [])]:
        dfs(r)
    if len(order) != len(set(order)):
        v.append("S4 foglia visitata più di una volta")
    body_leaves = [r for r in reachable_leaves
                   if not _under_furniture(r, parent, doc)]
    if set(order) != set(body_leaves):
        v.append("S4 l'ordine non copre tutte le foglie del body")

    # ── S5: monotonia verticale dentro una colonna ──
    for gi, g in enumerate(kinds["groups"]):
        if (g.get("meta") or {}).get("region") != "column":
            continue
        ys: list[float] = []

        def collect(r: str) -> None:
            kind = r.split("/")[1]
            if kind in _LEAF_KINDS:
                o = obj(r)
                bb = ((o.get("prov") or [{}])[0].get("bbox") or {})
                if "t" in bb:
                    ys.append(bb["t"])
            for c in children(r):
                collect(c)

        collect(g["self_ref"])
        if ys != sorted(ys):
            v.append(f"S5 colonna {gi}: y non monotòno")
    return v


def _under_furniture(ref: str, parent: dict, doc: dict) -> bool:
    furn = {c["cref"] for c in (doc["furniture"]["children"] or [])}
    if ref in furn:
        return True
    cur = ref
    while cur in parent:
        cur = parent[cur]
        if cur == "#/furniture" or cur in furn:
            return True
    return False


def _specs_from_args(args) -> list[tuple[str, int]]:
    if args.sample:
        pdfs = sorted(glob.glob(str(_ROOT / "corpus1" / "*.pdf"))
                      + glob.glob(str(_ROOT / "corpus2" / "*.pdf"))
                      + glob.glob(str(_ROOT / "corpus3" / "*.pdf")))
        rng = random.Random(20261011)
        specs = []
        while len(specs) < args.sample and pdfs:
            p = rng.choice(pdfs)
            try:
                import pymupdf
                n = pymupdf.open(p).page_count
            except Exception:
                continue
            if n:
                specs.append((p, rng.randrange(n) + 1))
        return specs
    specs = []
    for s in args.specs:
        pdf, _, page = s.rpartition(":")
        specs.append((pdf, int(page)))
    return specs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="*", help="pdf:pagina (1-based)")
    ap.add_argument("--sample", type=int, default=0, help="N pagine casuali")
    ap.add_argument("--show", action="store_true",
                    help="stampa la struttura essenziale di ogni pagina")
    args = ap.parse_args()

    import page_model as pm

    specs = _specs_from_args(args)
    total = bad = 0
    for pdf, page in specs:
        total += 1
        try:
            doc = pm.build_page_document(pdf, page - 1).to_dict()
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"ERR {Path(pdf).name} p{page}: {e!r}")
            continue
        viol = check_skeleton(doc)
        if viol:
            bad += 1
            print(f"VIOL {Path(pdf).name} p{page}: {len(viol)}")
            for x in viol[:6]:
                print(f"     - {x}")
        elif args.show:
            print(f"OK   {Path(pdf).name} p{page}: "
                  f"texts={len(doc['texts'])} tables={len(doc['tables'])} "
                  f"pictures={len(doc['pictures'])} groups={len(doc['groups'])}")
    print(f"\n{total - bad}/{total} pagine senza violazioni")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
