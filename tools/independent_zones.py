#!/usr/bin/env python3
"""Fase 2 — riferimento **indipendente** per il layout (DocLayout-YOLO, ONNX).

Perché serve
------------
Il metro storico (``verify_pages._reference_units``) e il motore
(``ir_layout._column_splits_robust``) condividono lo stesso modello di layout di
PyMuPDF (GNN ``BoxRFDGNN``) → **cecità correlata** (``su19 p383``). Questo modulo
introduce un secondo modello **diverso per architettura e dati di training**
(DocLayout-YOLO / DocStructBench, YOLOv10 su ONNX), usato come *oracolo* per
misurare l'**accordo/divergenza** col motore (la divergenza è un rilevatore).

Natura del modulo
-----------------
- **Solo sviluppo / validazione** (opt-in). Niente CI, niente runtime.
- **Nessuna dipendenza nuova**: usa ``onnxruntime`` (già presente) + ``numpy`` +
  ``pymupdf``. Non importa ``torch``/``cv2``/``doclayout_yolo``.
- **Offline**: il modello ONNX è un file locale passato via ``--model``.
- **Licenza**: il modello DocLayout-YOLO (e il codice) è **AGPL-3.0** (vedi
  metadata ONNX / repo). Da valutare con attenzione: vedi
  ``docs/STUDIO-FASE2-*.md``. Non va distribuito col prodotto senza valutazione legale.

API
---
    z = IndependentZones(model_path, imgsz=1024, conf=0.25, iou=0.45)
    zones = z.page_zones(pymupdf_page)     # [(class, conf, (x0,y0,x1,y1)), ...]

Le coordinate sono in **punti PDF** (origine in alto a sinistra), così sono
confrontabili con ``ir_layout`` e con ``_full_width_boxes``.

CLI
---
    .venv/bin/python tools/independent_zones.py --pdf corpus1/su19.pdf \
        --page 383 --model /path/doclayout_yolo_docstructbench_imgsz1024.onnx

Stampa le zone (JSON) e, con ``--overlay out.png``, salva una resa con i box.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# Ordine classi dal metadata ONNX DocStructBench (doclayout_yolo_docstructbench).
CLASSES = [
    "title",            # 0
    "plain text",       # 1
    "abandon",          # 2  (header/footer/note a margine)
    "figure",           # 3
    "figure_caption",   # 4
    "table",            # 5
    "table_caption",    # 6
    "table_footnote",   # 7
    "isolate_formula",  # 8
    "formula_caption",  # 9
]

_PAD = 114  # valore di padding YOLO/letterbox


def _letterbox(page, imgsz: int):
    """Rende la pagina alla dimensione content del letterbox (senza resize a valle).

    Ritorna ``(img_hwc_rgb, sx, sy, pad_left, pad_top)`` dove ``sx``/``sy`` sono i
    fattori di scala (px per punto PDF) usati per riportare i box a punti.
    """
    import pymupdf

    rect = page.rect
    wp, hp = rect.width, rect.height
    r = min(imgsz / hp, imgsz / wp)
    new_w, new_h = max(1, round(wp * r)), max(1, round(hp * r))
    # MuPDF fa direttamente il resize anisotropo: nessun cv2/PIL richiesto.
    mat = pymupdf.Matrix(new_w / wp, new_h / hp)
    pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csRGB, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    canvas = np.full((imgsz, imgsz, 3), _PAD, dtype=np.uint8)
    top, left = (imgsz - new_h) // 2, (imgsz - new_w) // 2
    canvas[top:top + new_h, left:left + new_w] = arr
    return canvas, new_w / wp, new_h / hp, left, top


class IndependentZones:
    """DocLayout-YOLO (ONNX) come riferimento indipendente del layout."""

    def __init__(self, model_path: str, imgsz: int = 1024,
                 conf: float = 0.25, iou: float = 0.45, threads: int = 0):
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.log_severity_level = 3
        if threads:
            so.intra_op_num_threads = threads
        self.sess = ort.InferenceSession(
            str(model_path), sess_options=so, providers=["CPUExecutionProvider"])
        self.input = self.sess.get_inputs()[0].name
        self.imgsz = imgsz
        self.conf = conf
        self.iou = iou
        # determina nc dal file (4 + nc canali)
        shape = self.sess.get_outputs()[0].shape
        self.nc = (shape[1] - 4) if isinstance(shape[1], int) else len(CLASSES)

    def predict_array(self, img_rgb: np.ndarray, sx: float, sy: float,
                      left: int, top: int, page_w: float, page_h: float):
        """Inferenza su un'immagine già in letterbox; ritorna zone in punti PDF."""
        x = img_rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = self.sess.run(None, {self.input: x})[0]  # (1, N, 6) end2end
        p = out[0]
        if p.ndim != 2 or p.shape[1] < 6:
            raise RuntimeError(f"formato output ONNX non atteso: {out.shape}")
        # DocLayout-YOLO/YOLOv10 end2end: (x0,y0,x1,y1,conf,cls), già NMS.
        conf = p[:, 4]
        m = conf > self.conf
        if not m.any():
            return []
        xyxy = p[m, :4].astype(np.float64)
        conf = conf[m].astype(np.float64)
        cls = p[m, 5].astype(int)

        zones = []
        for i in range(len(conf)):
            x0 = (xyxy[i, 0] - left) / sx
            y0 = (xyxy[i, 1] - top) / sy
            x1 = (xyxy[i, 2] - left) / sx
            y1 = (xyxy[i, 3] - top) / sy
            x0, x1 = sorted((max(0.0, x0), min(page_w, x1)))
            y0, y1 = sorted((max(0.0, y0), min(page_h, y1)))
            if x1 <= x0 or y1 <= y0:
                continue
            name = CLASSES[cls[i]] if cls[i] < len(CLASSES) else f"cls{cls[i]}"
            zones.append((name, float(conf[i]),
                          (float(round(x0, 2)), float(round(y0, 2)),
                           float(round(x1, 2)), float(round(y1, 2)))))
        zones.sort(key=lambda z: (z[2][1], z[2][0]))
        return zones

    def page_zones(self, page):
        """Zone DocLayout per una pagina PyMuPDF (coordinate in punti PDF)."""
        img, sx, sy, left, top = _letterbox(page, self.imgsz)
        return self.predict_array(img, sx, sy, left, top,
                                  page.rect.width, page.rect.height)


# ── utilità di confronto ────────────────────────────────────────────────────

def is_full_width(bbox, page_width: float, min_frac: float = 0.6,
                  min_height: float = 20.0) -> bool:
    return (bbox[2] - bbox[0]) >= min_frac * page_width and \
           (bbox[3] - bbox[1]) >= min_height


def contains(outer, inner) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return (outer[0] <= cx <= outer[2]) and (outer[1] <= cy <= outer[3])


def iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    aa = (a[2] - a[0]) * (a[3] - a[1])
    bb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (aa + bb - inter) if (aa + bb - inter) > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--page", type=int, required=True, help="1-based")
    ap.add_argument("--model", required=True)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.45)
    ap.add_argument("--imgsz", type=int, default=1024)
    ap.add_argument("--overlay", default=None, help="PNG di output con i box")
    args = ap.parse_args()

    import pymupdf

    doc = pymupdf.open(args.pdf)
    page = doc[args.page - 1]
    z = IndependentZones(args.model, imgsz=args.imgsz, conf=args.conf, iou=args.iou)
    zones = z.page_zones(page)
    print(json.dumps({"pdf": args.pdf, "page": args.page,
                      "page_size": [round(page.rect.width, 1), round(page.rect.height, 1)],
                      "zones": [{"class": c, "conf": round(s, 3), "bbox": b}
                                for c, s, b in zones]}, indent=2, ensure_ascii=False))

    if args.overlay:
        colors = {"title": (0.9, 0.1, 0.1), "figure": (0.1, 0.5, 0.9),
                  "table": (0.1, 0.7, 0.2), "plain text": (0.5, 0.5, 0.5)}
        for c, s, b in zones:
            page.draw_rect(pymupdf.Rect(*b), color=colors.get(c, (0.8, 0.4, 0.0)),
                           width=1.5)
            page.insert_text((b[0] + 2, max(8, b[1] + 8)), f"{c} {s:.2f}",
                             fontsize=7, color=colors.get(c, (0.8, 0.4, 0.0)))
        # disegna i box full-width del motore per confronto
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import ir_layout
        for bx in ir_layout._full_width_boxes(page, page.rect.width):
            page.draw_rect(pymupdf.Rect(*bx), color=(1, 0, 1), width=1.0, dashes="[3 2] 0")
        page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(args.overlay)
        print(f"overlay -> {args.overlay}")
    doc.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
