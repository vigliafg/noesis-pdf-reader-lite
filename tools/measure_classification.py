#!/usr/bin/env python3
"""Fase 2 — misura sistematica delle **divergenze di classificazione**.

Confronta la classe degli elementi del motore (``ir_layout.page_elements``:
``table``/``picture``/``formula``/``text``/…) con le zone DocLayout-YOLO
(``tools/independent_zones.py``) sulla **stessa regione** (IoU) e conta:

- **conflitti** motore↔oracolo su classi "speciali" (tabella/figura/formula):
  es. motore ``table`` vs oracolo ``figure`` (`ha22 p3355`);
- **zone speciali dell'oracolo senza corrispettivo** nel motore (candidati
  "elemento mancante", es. ``figure`` non vista);
- **gap di prosa** per pagina (candidati "prosa persa", pagine formula).

Così l'aneddoto (§3.4 dello studio) diventa un **tasso** su un campione
held-out. L'oracolo resta **dev-only** (AGPL): qui non entra mai nel runtime.

Uso:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_classification.py \\
        --model /path/doclayout_yolo_docstructbench_imgsz1024.onnx \\
        --run ~/.local/share/opencode/e2e250l6 --run ~/.local/share/opencode/e2e250m4 \\
        --limit 60 --known --out /tmp/class_div.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import independent_zones as iz  # noqa: E402
import ir_layout  # noqa: E402
import pymupdf  # noqa: E402

_CACHE = Path.home() / ".cache" / "noesis_fase2" / "elements"

#: categoria canonica di ogni classe **motore**
_ENG_CAT = {
    "table": "table", "picture": "figure", "caption": "caption",
    "formula": "formula",
    "text": "prose", "list-item": "prose", "footnote": "prose",
    "section-header": "heading", "title": "heading",
}
#: categoria canonica di ogni classe **oracolo**
_DL_CAT = {
    "title": "heading", "plain text": "prose", "abandon": "other",
    "figure": "figure", "figure_caption": "caption",
    "table": "table", "table_caption": "caption", "table_footnote": "prose",
    "isolate_formula": "formula", "formula_caption": "caption",
}
#: categorie compatibili (una classe motore può corrispondere a queste DL)
_COMPAT = {
    "table": {"table"},
    "figure": {"figure"},
    "formula": {"formula"},
    "caption": {"caption"},
    "prose": {"prose"},
    "heading": {"heading", "prose"},   # gli heading del motore possono essere plain text
}
#: categorie "speciali" su cui si misura il conflitto
_SPECIAL = {"table", "figure", "formula"}

_MIN_AREA = 1500.0  # px^2 minimo per considerare un elemento "regionale"


def _engine_elements(doc, page_idx: int) -> list[dict]:
    """Elementi del motore, con cache su disco (evita di rifare pymupdf4llm)."""
    _CACHE.mkdir(parents=True, exist_ok=True)
    key = f"{Path(doc.name).stem}_{page_idx}.json"
    f = _CACHE / key
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    _t, els = ir_layout.page_elements(doc, page_idx)
    f.write_text(json.dumps(els, ensure_ascii=False), encoding="utf-8")
    return els


def _area(b) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _best(item_bbox, items):
    best, bb = 0.0, None
    for other in items:
        v = iz.iou(item_bbox, other["bbox"])
        if v > best:
            best, bb = v, other
    return best, bb


def _load_pages(runs: list[Path], limit: int) -> list[tuple[str, str, int]]:
    out: list[tuple[str, str, int]] = []
    for run in runs:
        recs = [json.loads(ln) for ln in
                open(run / "records_partial.jsonl", encoding="utf-8") if ln.strip()]
        n = 0
        for r in recs:
            out.append((r.get("corpus", "corpus1"), r["pdf"], int(r["page_idx"])))
            n += 1
            if limit and n >= limit:
                break
    return out


_KNOWN = [
    ("corpus1", "ha22.pdf", 3354),                 # p3355 figura multi-pannello
    ("corpus3", "arxiv_2609.38133.pdf", 15),       # p16 pagina formula
    ("corpus3", "arxiv_2609.30742.pdf", 18),       # p19 pagina formula
    ("corpus3", "arxiv_2609.37412.pdf", 38),       # p39 pagina formula
]


def measure(model_path: str, pages: list[tuple[str, str, int]],
            out_path: Path | None = None) -> dict:
    oracle = iz.IndependentZones(model_path)
    counts = Counter()
    conflicts: list[dict] = []
    dl_unmatched: list[dict] = []
    prose_gap_pages: list[dict] = []
    cache_hits = Counter()
    done = 0

    for corpus, pdf_name, page_idx in pages:
        pdf = _ROOT / corpus / pdf_name
        try:
            doc = pymupdf.open(pdf)
            els = _engine_elements(doc, page_idx)
            zones = oracle.page_zones(doc[page_idx])
            doc.close()
        except Exception as e:  # noqa: BLE001
            print(f"  ! {pdf_name} p{page_idx + 1}: {e!r}")
            continue
        done += 1
        page = f"{pdf_name} p{page_idx + 1}"

        eng = [{"bbox": tuple(e["bbox"]), "class": e.get("class", "text"),
                "cat": _ENG_CAT.get(e.get("class", "text")),
                "text": (e.get("text") or "")[:80]} for e in els]
        dl = [{"bbox": b, "class": c, "cat": _DL_CAT.get(c), "conf": s}
              for c, s, b in zones]

        eng_special = [e for e in eng if e["cat"] in _SPECIAL and _area(e["bbox"]) >= _MIN_AREA]
        dl_special = [d for d in dl if d["cat"] in _SPECIAL and _area(d["bbox"]) >= _MIN_AREA]

        # A) motore -> oracolo
        for e in eng_special:
            counts[f"eng_{e['cat']}"] += 1
            iou, bb = _best(e["bbox"], dl)
            if iou >= 0.3 and bb is not None:
                if bb["cat"] in _COMPAT.get(e["cat"], set()):
                    counts[f"eng_{e['cat']}_ok"] += 1
                else:
                    counts[f"eng_{e['cat']}_conflict"] += 1
                    conflicts.append({
                        "page": page, "dir": "engine->oracle",
                        "engine": e["class"], "oracle": bb["class"],
                        "iou": round(iou, 2), "bbox": [round(v, 1) for v in e["bbox"]],
                        "text": e["text"]})
            elif iou >= 0.3 and bb is None:
                pass
            else:
                counts[f"eng_{e['cat']}_unmatched"] += 1

        # B) oracolo -> motore (zone speciali senza corrispettivo)
        for d in dl_special:
            iou, eb = _best(d["bbox"], eng)
            if iou >= 0.3 and eb is not None:
                # se l'elemento motore è speciale, il conflitto è già contato in A)
                if eb["cat"] in _SPECIAL:
                    continue
                if eb["cat"] in _COMPAT.get(d["cat"], set()) or \
                        d["cat"] in _COMPAT.get(eb["cat"] or "", set()):
                    continue
                counts[f"dl_{d['cat']}_conflict"] += 1
                conflicts.append({
                    "page": page, "dir": "oracle->engine",
                    "engine": eb["class"], "oracle": d["class"],
                    "iou": round(iou, 2), "bbox": [round(v, 1) for v in d["bbox"]],
                    "text": eb["text"]})
            elif iou < 0.3:
                counts[f"dl_{d['cat']}_unmatched"] += 1
                dl_unmatched.append({"page": page, "oracle": d["class"],
                                     "conf": round(d["conf"], 2),
                                     "bbox": [round(v, 1) for v in d["bbox"]]})

        # C) gap di prosa
        eng_prose = sum(1 for e in eng if e["cat"] == "prose")
        dl_prose = sum(1 for d in dl if d["cat"] == "prose")
        if dl_prose - eng_prose >= 3:
            counts["prose_gap_pages"] += 1
            prose_gap_pages.append({"page": page, "engine_prose": eng_prose,
                                    "oracle_prose": dl_prose,
                                    "gap": dl_prose - eng_prose})

    res = {
        "pages": done,
        "counts": dict(counts),
        "conflicts": conflicts,
        "oracle_unmatched_special": dl_unmatched,
        "prose_gap_pages": prose_gap_pages,
    }
    if out_path:
        out_path.write_text(json.dumps(res, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        print(f"report -> {out_path}")
    return res


def _print_summary(res: dict) -> None:
    c = res["counts"]
    print("=" * 68)
    print(f"pagine analizzate: {res['pages']}")
    print("elementi speciali motore (>=%.0f px^2):" % _MIN_AREA)
    for k in ("table", "figure", "formula"):
        tot = c.get(f"eng_{k}", 0)
        ok = c.get(f"eng_{k}_ok", 0)
        conf = c.get(f"eng_{k}_conflict", 0)
        unm = c.get(f"eng_{k}_unmatched", 0)
        print(f"  {k:8} tot={tot:4}  ok={ok:4}  conflitto={conf:3}  non-match={unm:3}")
    print("zone speciali oracolo senza corrispettivo motore:")
    for k in ("table", "figure", "formula"):
        print(f"  {k:8} unmatched={c.get(f'dl_{k}_unmatched', 0)}  "
              f"conflitto={c.get(f'dl_{k}_conflict', 0)}")
    print(f"pagine con gap di prosa >=3: {c.get('prose_gap_pages', 0)}")
    print("=" * 68)
    print("CONFLITTI:")
    for x in res["conflicts"]:
        print(f"  [{x['dir']:16}] {x['page']:24} motore={x['engine']:8} "
              f"oracolo={x['oracle']:16} iou={x['iou']} {x['bbox']} {x['text']!r}")
    print("ZONE ORACOLO NON CORRISPOSTE:")
    for x in res["oracle_unmatched_special"][:20]:
        print(f"  {x['page']:24} {x['oracle']:16} conf={x['conf']} {x['bbox']}")
    print("PAGINE CON GAP DI PROSA:")
    for x in res["prose_gap_pages"][:20]:
        print(f"  {x['page']:24} motore={x['engine_prose']:3} oracolo={x['oracle_prose']:3} gap={x['gap']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--run", action="append", default=[])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--known", action="store_true",
                    help="includi le pagine residue note (ha22/arxiv)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    pages: list[tuple[str, str, int]] = []
    if args.known:
        pages += _KNOWN
    runs = [Path(r).expanduser() for r in args.run]
    if runs:
        pages += _load_pages(runs, args.limit)
    if not pages:
        ap.error("specifica --run e/o --known")

    out = Path(args.out).expanduser() if args.out else None
    res = measure(args.model, pages, out)
    _print_summary(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
