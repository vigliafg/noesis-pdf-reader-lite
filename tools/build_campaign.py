#!/usr/bin/env python3
"""Costruisce i campioni della campagna di validazione (dev-only).

Dallo scan di disponibilità (`scan_objects.json`):

- **2 run generali** da 250 pagine, stratificati sulle 6 classi di layout,
  disgiunti tra loro;
- **3 run settoriali** da 100 pagine (figure / tabelle / box), stratificati per
  tipo di span (full / parziale);
- tutte le pagine sono **disgiunte** e **held-out** (esclude i campioni già usati).

Uso::

    .venv/bin/python tools/build_campaign.py --scan /tmp/opencode/scan_objects.json \\
        --exclude /tmp/opencode/e2e-r1/sample.json /tmp/opencode/e2e-r2/sample.json \\
        --out-dir /tmp/opencode/campaign
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

_LAYOUT_CLASSES = ("prosa", "colonne/box-liste", "tabelle", "indici",
                   "equazioni", "figure")
_OBJECTS = ("fig", "tab", "box")
_GENERAL = {"G1": 250, "G2": 250}
_SECTORIAL = 100


def _excluded(paths: list[str]) -> set[tuple[str, int]]:
    ex: set[tuple[str, int]] = set()
    for p in paths:
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        for _corpus, vals in (d.get("sample") or {}).items():
            for v in vals:
                ex.add((v["pdf"], v["page"]))
    return ex


def _write(out_dir: Path, name: str, items: list[dict]) -> None:
    sample: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        entry = {"pdf": it["pdf"], "page": it["page"]}
        for k in ("span", "layout_class"):
            if k in it:
                entry[k] = it[k]
        sample[it["corpus"]].append(entry)
    sample = {c: sorted(v, key=lambda x: (x["pdf"], x["page"]))
              for c, v in sample.items()}
    (out_dir / f"{name}.json").write_text(
        json.dumps({"name": name, "n": len(items), "sample": sample},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {name}: {len(items)} pagine")


def _pick_spans(by_span: dict[str, list[dict]], n: int) -> list[dict]:
    """Seleziona ``n`` candidati dando priorità a *parziale* (più raro) senza
    perdere i *full*: interleave parziale/full."""
    full = list(by_span.get("full", []))
    part = list(by_span.get("parziale", []))
    out: list[dict] = []
    i = j = 0
    while len(out) < n and (i < len(full) or j < len(part)):
        if j < len(part):
            out.append(part[j])
            j += 1
        if len(out) < n and i < len(full):
            out.append(full[i])
            i += 1
    return out


def build(scan: dict, ex: set[tuple[str, int]], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── settoriali: NON disgiunti tra loro (una pagina può avere più oggetti),
    #    ma held-out rispetto ai run precedenti ──
    sector_pages: set[tuple[str, int]] = set()
    for obj in _OBJECTS:
        by_span: dict[str, list[dict]] = {"full": [], "parziale": []}
        for span in ("full", "parziale"):
            for it in scan.get("picks", {}).get(f"{obj}_{span}", []):
                if (it["pdf"], it["page"]) in ex:
                    continue
                by_span[span].append(dict(it))
        sel = _pick_spans(by_span, _SECTORIAL)
        _write(out_dir, f"sector_{obj}", sel)
        for x in sel:
            sector_pages.add((x["pdf"], x["page"]))

    # ── generali: disgiunti dai settoriali e dai run precedenti ──
    used = set(ex) | sector_pages
    buckets: dict[str, list[dict]] = defaultdict(list)
    for p in scan.get("pages", []):
        if (p["pdf"], p["page"]) in used:
            continue
        buckets[p.get("layout_class") or "?"].append(p)

    # round-robin sulle classi: ogni classe entra nei run (niente classi escluse)
    queues = {c: list(buckets.get(c, [])) for c in _LAYOUT_CLASSES}
    order: list[dict] = []
    while any(queues.values()):
        for c in _LAYOUT_CLASSES:
            if queues.get(c):
                order.append(queues[c].pop(0))

    sel_g: dict[str, list[dict]] = {"G1": [], "G2": []}
    for i, row in enumerate(order):
        tag = "G1" if i % 2 == 0 else "G2"
        other = "G2" if tag == "G1" else "G1"
        if len(sel_g[tag]) < _GENERAL[tag]:
            sel_g[tag].append(row)
        elif len(sel_g[other]) < _GENERAL[other]:
            sel_g[other].append(row)
        if (len(sel_g["G1"]) >= _GENERAL["G1"]
                and len(sel_g["G2"]) >= _GENERAL["G2"]):
            break
    for tag in ("G1", "G2"):
        _write(out_dir, f"general_{tag}", sel_g[tag])


def main() -> int:
    ap = argparse.ArgumentParser(description="Costruisci campioni campagna")
    ap.add_argument("--scan", default="/tmp/opencode/scan_objects.json")
    ap.add_argument("--exclude", nargs="*", default=[])
    ap.add_argument("--out-dir", default="/tmp/opencode/campaign")
    args = ap.parse_args()
    scan = json.loads(Path(args.scan).read_text(encoding="utf-8"))
    ex = _excluded(args.exclude)
    print(f"escluse (held-out) {len(ex)} pagine")
    build(scan, ex, Path(args.out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
