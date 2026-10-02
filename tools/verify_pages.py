#!/usr/bin/env python3
"""Verifica automatica pagina-per-pagina (per l'agente, senza arbitraggio umano).

Apre l'**app reale** headless, va alle pagine richieste, lascia estrarre il
markdown e salva **quello che il pannello mostra davvero**:

- ``page_XXXX.md``    → il markdown in memoria (body del pannello);
- ``page_XXXX.plain.txt`` → il **testo impaginato** (``toPlainText`` del pannello): è
  ciò che l'utente vede/scrolla, quindi cattura anche i bug di *resa*;
- ``page_XXXX.png``   → screenshot della finestra;
- ``report.md``/``report.jsonl`` → metriche per pagina.

Metriche (per pagina):
- ``recall_pdf``:  quota di parole "lunghe" della pagina PDF presenti nel body
  (integrità dell'**estrazione**);
- ``recall_render``: quota di parole lunghe del body presenti nel testo
  impaginato (integrità della **resa**: se < ~1, il pannello tronca);
- ``tail_ok``: l'ultimo blocco del documento è impaginato (altezza > 0);
- ``body_len`` / ``plain_len``.

Uso:
    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/verify_pages.py \\
        corpus1/ha22.pdf --pages 100,118,140-143 --out /tmp/opencode/verify \\
        [--fresh] [--timeout 120]
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
    """'100,118,140-143' → [100,118,140,141,142,143] (0-based indici)."""
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


def _long_words(t: str, minlen: int = 6) -> set[str]:
    return {w for w in _norm(t).split() if len(w) >= minlen}


def _strip_images(t: str) -> str:
    """Toglie i data-URI/base64 (parole finte) prima dei confronti."""
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    return re.sub(r"data:[^\s)]+", " ", t)


def _recall(ref: str, out: str, minlen: int = 6) -> float:
    words = _long_words(ref, minlen)
    if not words:
        return 1.0
    have = _long_words(out, minlen)
    return len(words & have) / len(words)


def _last_block_height(text_edit) -> float:
    from PyQt6.QtGui import QTextCursor

    d = text_edit.document()
    cur = QTextCursor(d)
    cur.movePosition(QTextCursor.MoveOperation.End)
    return float(d.documentLayout().blockBoundingRect(cur.block()).height())


def main() -> int:
    ap = argparse.ArgumentParser(description="Verifica pagine con l'app reale")
    ap.add_argument("pdf")
    ap.add_argument("--pages", required=True, help="es. 100,118,140-143 (indici 0-based)")
    ap.add_argument("--out", default="/tmp/opencode/verify")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--fresh", action="store_true",
                    help="svuota la cache della pagina e riestrae (ignora i final salvati)")
    args = ap.parse_args()

    import pymupdf
    from PyQt6.QtWidgets import QApplication

    import main as app

    pdf = Path(args.pdf).resolve()
    pages = _parse_pages(args.pages)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    qapp = QApplication.instance() or QApplication([])
    win = app.MainWindow()
    win._resume_last_page = False
    win.show()
    win._open_pdf(pdf)
    lang = app._tess_lang_code(app.get_source_lang())

    doc = pymupdf.open(str(pdf))
    report = []
    try:
        for idx in pages:
            if idx < 0 or idx >= len(doc):
                print(f"  ! pagina {idx} fuori range")
                continue
            if args.fresh:
                win._purge_page_cache(idx)
            win._set_page(idx)
            t0 = time.time()
            while time.time() - t0 < args.timeout:
                qapp.processEvents()
                if (idx, lang, "()") in win._final_text_cache:
                    break
                time.sleep(0.02)
            qapp.processEvents()
            origin = win.text_panel.origin_panel
            body = win.text_panel._page_body or ""
            plain = origin.toPlainText() or ""
            body_text = _strip_images(body)
            pdf_text = doc[idx].get_text("text")
            r_pdf = _recall(pdf_text, body_text)
            r_ren = _recall(body_text, plain)
            tail = _last_block_height(origin)
            (out / f"page_{idx:04d}.md").write_text(body, encoding="utf-8")
            (out / f"page_{idx:04d}.plain.txt").write_text(plain, encoding="utf-8")
            win.grab().save(str(out / f"page_{idx:04d}.png"))
            rec = {
                "page_idx": idx, "page_ui": idx + 1,
                "body_len": len(body), "body_text_len": len(body_text),
                "plain_len": len(plain),
                "recall_pdf": round(r_pdf, 4), "recall_render": round(r_ren, 4),
                "tail_block_h": round(tail, 1),
                "ok": (r_pdf >= 0.98 and r_ren >= 0.98 and tail > 1),
            }
            report.append(rec)
            flag = "OK " if rec["ok"] else "!! "
            print(f"{flag}p{idx:4d} body={rec['body_len']:6d} plain={rec['plain_len']:6d} "
                  f"recall_pdf={r_pdf:.3f} recall_render={r_ren:.3f} tail={tail:.0f}")
    finally:
        win.close()
        doc.close()

    (out / "report.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in report), encoding="utf-8")
    with (out / "report.md").open("w", encoding="utf-8") as fh:
        fh.write("# Verifica pagine — " + pdf.name + "\n\n")
        fh.write("| pagina | body | plain | recall_pdf | recall_render | tail | esito |\n")
        fh.write("|---|---|---|---|---|---|---|\n")
        for r in report:
            fh.write(f"| {r['page_ui']} | {r['body_len']} | {r['plain_len']} | "
                     f"{r['recall_pdf']} | {r['recall_render']} | {r['tail_block_h']} | "
                     f"{'OK' if r['ok'] else '**DA ESAMINARE**'} |\n")
    bad = [r for r in report if not r["ok"]]
    print(f"\nReport: {out/'report.md'}   pagine={len(report)}  da esaminare={len(bad)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
