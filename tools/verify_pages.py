#!/usr/bin/env python3
"""Verifica/confronto pagina-per-pagina delle pipeline di estrazione.

Pipeline confrontabili (stessa pagina, stessa resa del pannello):
- ``current``: percorso layout GNN + motore dei fix (quello dell'app di oggi);
- ``legacy``:  ``pymupdf4llm.use_layout(False)`` (percorso "classico"), raw;
- ``ir``:      ricostruzione dalla **content map** (``ir_layout.build_markdown``).

Per ogni pagina e pipeline salva:
- ``<pipeline>/page_XXXX.md`` / ``.plain.txt`` (testo impaginato) / ``.png``;
- metriche: tempo, ``recall_pdf`` (integrità estrazione), ``recall_render``
  (integrità resa), ``order_score`` (contiguità per colonna), ``tail``.

Uso:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/verify_pages.py \\
        corpus1/ha22.pdf --pages 99-102 --out /tmp/opencode/verify \\
        --pipelines current,legacy,ir
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _parse_pages(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def _norm(t: str) -> str:
    t = t.lower()
    t = re.sub(r"-\s*\n\s*", "", t)
    t = re.sub(r"-\s+(?=[a-z])", "", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def _strip_images(t: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    return re.sub(r"data:[^\s)]+", " ", t)


def _page_text_no_figures(page, elements: list[dict]) -> str:
    """Testo della pagina escludendo le regioni `picture` (rese nell'immagine)."""
    pics = [e["bbox"] for e in elements if e["class"] == "picture"]

    def _inside(b) -> bool:
        x0, y0, x1, y1 = b["bbox"]
        for px0, py0, px1, py1 in pics:
            if x0 >= px0 - 2 and x1 <= px1 + 2 and y0 >= py0 - 2 and y1 <= py1 + 2:
                return True
        return False

    parts: list[str] = []
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type") != 0 or _inside(blk):
            continue
        for ln in blk["lines"]:
            parts.append("".join(s["text"] for s in ln["spans"]))
    return "\n".join(parts)


def _long_words(t: str, minlen: int = 6) -> set[str]:
    return {w for w in _norm(t).split() if len(w) >= minlen}


def _recall(ref: str, out: str, minlen: int = 6) -> float:
    words = _long_words(ref, minlen)
    if not words:
        return 1.0
    return len(words & _long_words(out, minlen)) / len(words)


def _order_score(md: str, elements: list[dict], page_width: float) -> float:
    """Quota di colonne il cui testo compare **contiguo** nel md (0..1).

    Usa i blocchi di testo della content map (ground truth delle colonne): per
    ogni colonna concatena i testi in ordine di y e verifica che siano una
    sottostringa contigua del md normalizzato. Penalizza l'interlacciamento.
    """
    import main

    texty = [e for e in elements
             if e["class"] in ("text", "section-header", "title")
             and e["w"] < 0.6 * page_width]
    if not texty:
        return 1.0
    splits = main._detect_column_splits(
        [{"x0": e["bbox"][0], "x1": e["bbox"][2],
          "y0": e["bbox"][1], "y1": e["bbox"][3]} for e in texty],
        page_width,
    )
    ncol = len(splits) + 1
    cols: list[list[dict]] = [[] for _ in range(ncol)]
    for e in texty:
        mid = (e["bbox"][0] + e["bbox"][2]) / 2
        cols[sum(1 for s in splits if mid > s)].append(e)
    M = _norm(md)
    ok = 0
    total = 0
    for c in cols:
        if not c:
            continue
        c.sort(key=lambda e: e["y0"])
        expected = _norm(" ".join(_strip_images(e["text"]) for e in c))
        if len(expected) < 80:
            continue
        total += 1
        if expected in M:
            ok += 1
    return 1.0 if total == 0 else ok / total


def _last_block_height(text_edit) -> float:
    from PyQt6.QtGui import QTextCursor

    d = text_edit.document()
    cur = QTextCursor(d)
    cur.movePosition(QTextCursor.MoveOperation.End)
    return float(d.documentLayout().blockBoundingRect(cur.block()).height())


def _build_md(pipeline: str, pdf: str, doc, page, idx: int, figures_dir, ocr_lang):
    import main

    import pymupdf4llm

    if pipeline == "legacy":
        pymupdf4llm.use_layout(False)
        try:
            return pymupdf4llm.to_markdown(pdf, pages=[idx])
        finally:
            pymupdf4llm.use_layout(True)
    if pipeline == "ir":
        import ir_layout

        return ir_layout.build_markdown(page, doc, idx, figures_dir=figures_dir)
    # current
    raw = main._extract_pymupdf4llm(pdf, idx, ocr_lang)
    md, _label = main._apply_engine_on_page(
        page, raw, figures_dir=figures_dir, page_num=idx, figure_mode="embed")
    return md


def main() -> int:
    ap = argparse.ArgumentParser(description="Verifica/confronto pipeline")
    ap.add_argument("pdf")
    ap.add_argument("--pages", required=True)
    ap.add_argument("--out", default="/tmp/opencode/verify")
    ap.add_argument("--pipelines", default="current")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    import pymupdf
    from PyQt6.QtWidgets import QApplication

    import main as app
    import ir_layout

    pdf = str(Path(args.pdf).resolve())
    pages = _parse_pages(args.pages)
    pipelines = [p.strip() for p in args.pipelines.split(",") if p.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    qapp = QApplication.instance() or QApplication([])
    panel = app.TextPanel()
    panel.resize(800, 900)
    panel.show()

    doc = pymupdf.open(pdf)
    ocr_lang = app._tess_lang_code(app.get_source_lang())
    report = []
    try:
        for idx in pages:
            if idx < 0 or idx >= len(doc):
                continue
            page = doc[idx]
            pw = page.rect.width
            # ground truth colonne: content map (layout ON), una volta per pagina
            try:
                _t, elements = ir_layout.page_elements(doc, idx)
            except Exception:
                elements = []
            pdf_text = _page_text_no_figures(page, elements)
            for pipe in pipelines:
                figdir = out / pipe / "fig"
                figdir.mkdir(parents=True, exist_ok=True)
                t0 = time.time()
                try:
                    md = _build_md(pipe, pdf, doc, page, idx, figdir, ocr_lang) or ""
                except Exception as e:  # noqa: BLE001
                    md = ""
                    print(f"  ! {pipe} p{idx}: {e!r}")
                dt = time.time() - t0
                qapp.processEvents()
                panel.show_text(md, as_markdown=True)
                qapp.processEvents()
                plain = panel.toPlainText() or ""
                body_text = _strip_images(md)
                r_pdf = _recall(pdf_text, body_text)
                r_ren = _recall(body_text, plain)
                oscore = _order_score(md, elements, pw)
                tail = _last_block_height(panel)
                pd = out / pipe
                pd.mkdir(parents=True, exist_ok=True)
                (pd / f"page_{idx:04d}.md").write_text(md, encoding="utf-8")
                (pd / f"page_{idx:04d}.plain.txt").write_text(plain, encoding="utf-8")
                panel.grab().save(str(pd / f"page_{idx:04d}.png"))
                rec = {"page_idx": idx, "page_ui": idx + 1, "pipeline": pipe,
                       "secs": round(dt, 2), "body_len": len(md),
                       "plain_len": len(plain), "recall_pdf": round(r_pdf, 4),
                       "recall_render": round(r_ren, 4),
                       "order_score": round(oscore, 3), "tail": round(tail, 1)}
                report.append(rec)
                print(f"{pipe:8s} p{idx:4d} {dt:6.2f}s body={len(md):7d} "
                      f"pdf={r_pdf:.3f} render={r_ren:.3f} order={oscore:.2f} tail={tail:.0f}")
    finally:
        panel.close()
        doc.close()

    (out / "report.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in report), encoding="utf-8")
    with (out / "report.md").open("w", encoding="utf-8") as fh:
        fh.write(f"# Studio pipeline — {Path(pdf).name}\n\n")
        fh.write("| pagina | pipeline | secs | body | plain | recall_pdf | recall_render | order | tail |\n")
        fh.write("|---|---|---|---|---|---|---|---|---|\n")
        for r in report:
            fh.write(f"| {r['page_ui']} | {r['pipeline']} | {r['secs']} | {r['body_len']} | "
                     f"{r['plain_len']} | {r['recall_pdf']} | {r['recall_render']} | "
                     f"{r['order_score']} | {r['tail']} |\n")
        # medie per pipeline
        fh.write("\n## Medie per pipeline\n\n| pipeline | secs (media) | recall_pdf | "
                 "recall_render | order |\n|---|---|---|---|---|\n")
        for p in pipelines:
            rows = [r for r in report if r["pipeline"] == p]
            if not rows:
                continue
            n = len(rows)
            fh.write(f"| {p} | {sum(r['secs'] for r in rows)/n:.2f} | "
                     f"{sum(r['recall_pdf'] for r in rows)/n:.3f} | "
                     f"{sum(r['recall_render'] for r in rows)/n:.3f} | "
                     f"{sum(r['order_score'] for r in rows)/n:.3f} |\n")
    print(f"\nReport: {out/'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
