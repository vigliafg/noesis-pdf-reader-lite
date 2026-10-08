#!/usr/bin/env python3
"""Fase 2 — misura accordo/divergenza tra motore IR e oracolo indipendente.

Confronta le **zone DocLayout-YOLO** (``tools/independent_zones.py``) con il
modello del motore (``ir_layout.page_elements`` + ``ir_layout._full_width_boxes``)
su un campione di pagine, e misura:

1. **Box a tutta larghezza** (classe ``su19`` "Key Points"): per ogni box del
   motore, quante zone DocLayout cadono dentro (0/1/2+). Se DocLayout segmenta
   il contenuto del box come *una* zona, può fare da oracolo indipendente per I1.
2. **Zone full-width non coperte** da un box del motore (candidati "box senza
   fill" / sidebar che il motore non rileva).
3. **Figure/didascalie**: pagine con figure; quante hanno in DocLayout una
   ``figure`` + ``figure_caption`` adiacenti (oracolo per I2).

Uso:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_independent.py \\
        --model /path/doclayout_yolo_docstructbench_imgsz1024.onnx \\
        --run ~/.local/share/opencode/e2e250l6 [--limit 60]
    ... --su19-kp          # le 54 pagine su19 con "Key Points"
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

import independent_zones as iz  # noqa: E402
import ir_layout  # noqa: E402
import pymupdf  # noqa: E402


def corpus_dir_for(corpus: str) -> Path:
    return _ROOT / corpus


def _load_run_pages(run_dir: Path, limit: int) -> list[tuple[str, str, int]]:
    """[(corpus, pdf, page_idx), ...] dai record del run."""
    recs = [json.loads(ln) for ln in
            open(run_dir / "records_partial.jsonl", encoding="utf-8") if ln.strip()]
    out: list[tuple[str, str, int]] = []
    for r in recs:
        out.append((r.get("corpus", "corpus1"), r["pdf"], int(r["page_idx"])))
        if limit and len(out) >= limit:
            break
    return out


def _su19_kp_pages(pdf: Path) -> list[tuple[str, str, int]]:
    doc = pymupdf.open(pdf)
    pages = [("corpus1", pdf.name, i) for i in range(doc.page_count)
             if "Key Points" in doc[i].get_text()]
    doc.close()
    return pages


def measure(model_path: str, pages: list[tuple[str, str, int]],
            conf: float, out_path: Path | None = None) -> dict:
    oracle = iz.IndependentZones(model_path, conf=conf)
    hist = Counter()          # box -> n. zone DocLayout dentro
    box_has_zone05 = 0        # box con una zona IoU>=0.5
    box_total = 0
    dl_fw_total = 0
    dl_fw_uncovered: list[dict] = []   # zona FW DocLayout senza box motore (IoU<0.3)
    box_uncovered: list[dict] = []      # box motore senza zona DocLayout dentro
    figure_pages = 0
    figure_with_caption = 0
    details: list[dict] = []
    done = 0

    for corpus, pdf_name, page_idx in pages:
        pdf = corpus_dir_for(corpus) / pdf_name
        try:
            doc = pymupdf.open(pdf)
            page = doc[page_idx]
            _t, els = ir_layout.page_elements(doc, page_idx)
            boxes = ir_layout._full_width_boxes(page, page.rect.width)
            zones = oracle.page_zones(page)
            pw, ph = page.rect.width, page.rect.height
            pictures = [e for e in els if e.get("class") == "picture"]
            doc.close()
        except Exception as e:  # noqa: BLE001
            print(f"  ! {pdf_name} p{page_idx + 1}: {e!r}")
            continue

        done += 1
        per = {"corpus": corpus, "pdf": pdf_name, "page": page_idx,
               "n_boxes": len(boxes), "n_zones": len(zones),
               "box_inside": [], "dl_fw_uncovered": []}

        for bx in boxes:
            box_total += 1
            inside = [z for z in zones if iz.contains(bx, z[2])]
            best = max((iz.iou(bx, z[2]) for z in zones), default=0.0)
            if best >= 0.5:
                box_has_zone05 += 1
            n = len(inside)
            hist[min(n, 3)] += 1
            per["box_inside"].append(
                {"box": [round(v, 1) for v in bx], "n": n,
                 "classes": [z[0] for z in inside], "best_iou": round(best, 2)})
            if n == 0:
                box_uncovered.append(
                    {"page": f"{pdf_name} p{page_idx + 1}",
                     "box": [round(v, 1) for v in bx]})

        # zone DocLayout full-width non coperte da un box del motore
        for c, s, b in zones:
            if not iz.is_full_width(b, pw):
                continue
            dl_fw_total += 1
            if any(iz.iou(bx, b) >= 0.3 for bx in boxes):
                continue
            # scarta fasce che coprono quasi tutta la pagina (sfondo/margini)
            if (b[2] - b[0]) > 0.97 * pw and (b[3] - b[1]) > 0.9 * ph:
                continue
            dl_fw_uncovered.append(
                {"page": f"{pdf_name} p{page_idx + 1}", "class": c,
                 "conf": round(s, 3), "bbox": [round(v, 1) for v in b]})
            per["dl_fw_uncovered"].append(
                {"class": c, "conf": round(s, 3),
                 "bbox": [round(v, 1) for v in b]})

        # figure + didascalia (oracolo per I2)
        if pictures:
            figure_pages += 1
            if any(c == "figure_caption" for c, _s, _b in zones):
                figure_with_caption += 1

        details.append(per)

    res = {
        "pages": done,
        "box_total": box_total,
        "box_inside_hist": {str(k): hist[k] for k in sorted(hist)},
        "box_with_dl_zone_iou_ge_0.5": box_has_zone05,
        "dl_fw_zones": dl_fw_total,
        "dl_fw_uncovered": dl_fw_uncovered,
        "box_uncovered": box_uncovered,
        "figure_pages": figure_pages,
        "figure_pages_with_caption": figure_with_caption,
        "details": details,
    }
    if out_path:
        out_path.write_text(json.dumps(res, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        print(f"report -> {out_path}")
    return res


def _print_summary(res: dict) -> None:
    print("=" * 68)
    print(f"pagine analizzate        : {res['pages']}")
    print(f"box a tutta larghezza     : {res['box_total']}")
    h = res["box_inside_hist"]
    print(f"  zone DocLayout nel box  : 0={h.get('0', 0)}  "
          f"1={h.get('1', 0)}  2={h.get('2', 0)}  3+={h.get('3', 0)}")
    print(f"  box con zona IoU>=0.5   : {res['box_with_dl_zone_iou_ge_0.5']}"
          f" / {res['box_total']}")
    print(f"zone FW DocLayout         : {res['dl_fw_zones']}")
    print(f"  non coperte da box      : {len(res['dl_fw_uncovered'])}")
    print(f"box motore senza zona DL  : {len(res['box_uncovered'])}")
    print(f"pagine con figure         : {res['figure_pages']}")
    print(f"  con figure_caption (DL) : {res['figure_pages_with_caption']}")
    print("=" * 68)
    for d in res["dl_fw_uncovered"][:15]:
        print(f"  [FW non coperta] {d['page']:16} {d['class']:14} "
              f"{d['conf']:.2f} {d['bbox']}")
    for d in res["box_uncovered"][:15]:
        print(f"  [box senza zona] {d['page']:16} {d['box']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--run", default=None)
    ap.add_argument("--su19-kp", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    pages: list[tuple[str, str, int]] = []
    if args.su19_kp:
        pages += _su19_kp_pages(_ROOT / "corpus1" / "su19.pdf")
    if args.run:
        pages += _load_run_pages(Path(args.run).expanduser(), args.limit)
    if not pages:
        ap.error("specifica --run e/o --su19-kp")
    if args.limit and not args.su19_kp:
        pages = pages[:args.limit]

    out = Path(args.out).expanduser() if args.out else None
    res = measure(args.model, pages, args.conf, out)
    _print_summary(res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
