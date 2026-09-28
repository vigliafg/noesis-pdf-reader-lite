#!/usr/bin/env python3
"""Aggrega le metriche e genera report.html, summary.md e la scorecard.

Uso (da experimental/eval):
    ../../.venv/bin/python make_report.py
"""

from __future__ import annotations

import html
import sys

import evalutil
from evalutil import (
    ENGINES,
    OUT_DIR,
    bunch_pages,
    items,
    load_json,
    md_dir,
    metrics_path,
    save_json,
)


def _collect() -> dict:
    manifest: dict = {"bunches": {}, "metrics": {}}
    for bunch in items():
        bid = bunch["id"]
        manifest["bunches"][bid] = {
            "pdf": bunch.get("pdf"),
            "pages": bunch_pages(bid),
            "origin": bunch.get("origin"),
            "note": bunch.get("note"),
        }
        for engine in ENGINES:
            for run_pass in ("cold", "warm"):
                path = metrics_path(bid, engine, run_pass)
                if path.is_file():
                    manifest["metrics"][f"{bid}_{engine}_{run_pass}"] = load_json(path)
    save_json(OUT_DIR / "manifest.json", manifest)
    return manifest


def _speed_rows(manifest: dict) -> list[tuple]:
    rows: list[tuple] = []
    for bunch in items():
        bid = bunch["id"]
        for engine in ENGINES:
            for run_pass in ("cold", "warm"):
                data = manifest["metrics"].get(f"{bid}_{engine}_{run_pass}")
                if not data:
                    rows.append((bid, engine, run_pass, "—", "—", "—", "—"))
                    continue
                if not data.get("available"):
                    rows.append((bid, engine, run_pass, "n/d", "n/d", "n/d", "n/d"))
                    continue
                n = max(data.get("page_count", 1), 1)
                total = data.get("raw_total_ms", 0.0)
                rows.append((
                    bid,
                    engine,
                    run_pass,
                    f"{data.get('import_ms', 0):.0f}",
                    f"{total:.0f}",
                    f"{total / n:.0f}",
                    f"{data.get('rss_peak_kb', 0) / 1024:.0f}",
                ))
    return rows


def _write_summary(manifest: dict) -> None:
    lines = ["# Valutazione PyMuPDF4LLM vs Xberg — riepilogo velocità", ""]
    lines.append("| bunch | pagine | motore | passata | import ms | totale raw ms | ms/pagina (amm.) | RSS peak MB |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|")
    for row in _speed_rows(manifest):
        bid = row[0]
        pages = ",".join(str(p) for p in manifest["bunches"].get(bid, {}).get("pages", []))
        lines.append("| " + " | ".join([bid, pages, *[str(x) for x in row[1:]]]) + " |")
    lines.append("")
    lines.append("> Xberg estrae a livello **documento**: il totale è una sola chiamata")
    lines.append("> (la prima pagina paga l'estrazione completa, le altre sono cache-hit).")
    lines.append("> PyMuPDF4LLM estrae **per pagina**. Il confronto ms/pagina è ammortizzato.")
    (OUT_DIR / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _read_md(bunch: str, engine: str, mode: str, orig: int) -> str:
    path = md_dir(bunch, engine, mode) / f"p{orig}.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return "(output assente)"


def _write_html(manifest: dict) -> None:
    parts = [
        "<!doctype html><html lang='it'><head><meta charset='utf-8'>",
        "<title>Valutazione PyMuPDF4LLM vs Xberg</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;margin:16px;background:#1e1e1e;color:#ddd}",
        "h1,h2,h3{color:#fff} .page{display:grid;grid-template-columns:minmax(320px,1fr) 1fr 1fr;gap:12px;margin:18px 0;border-top:1px solid #444;padding-top:12px}",
        "img{max-width:100%;border:1px solid #555;background:#fff}",
        "pre{white-space:pre-wrap;word-break:break-word;background:#2a2a2a;border:1px solid #444;padding:8px;max-height:70vh;overflow:auto;font-size:12px}",
        ".col h4{margin:4px 0}",
        "</style></head><body>",
        "<h1>Valutazione PyMuPDF4LLM vs Xberg</h1>",
    ]
    for bunch in items():
        bid = bunch["id"]
        pages = bunch_pages(bid)
        parts.append(f"<h2>{bid} — {bunch.get('note', '')}</h2>")
        for orig in pages:
            img_rel = f"{bid}/pages/p{orig}.png"
            parts.append("<div class='page'>")
            parts.append(f"<div class='col'><h4>p{orig} (immagine)</h4><img src='{img_rel}'></div>")
            for engine in ENGINES:
                raw = html.escape(_read_md(bid, engine, "raw", orig))
                eng = html.escape(_read_md(bid, engine, "engine", orig))
                parts.append(
                    f"<div class='col'><h4>{engine}</h4>"
                    f"<details open><summary>raw</summary><pre>{raw}</pre></details>"
                    f"<details><summary>+layout_engine</summary><pre>{eng}</pre></details>"
                    "</div>"
                )
            parts.append("</div>")
    parts.append("</body></html>")
    (OUT_DIR / "report.html").write_text("".join(parts), encoding="utf-8")


def _write_scorecard(manifest: dict) -> None:
    lines = [
        "# Scorecard — arbitro visivo",
        "",
        "Compilare pagina per pagina confrontando l'immagine con i due markdown.",
        "Valori: `A` (vince PyMuPDF4LLM), `B` (vince Xberg), `=` (pari).",
        "Dimensioni: **fedeltà**, **ordine**, **tabelle**, **figure**, **struttura**,",
        "**verdetto** sintetico, **note**.",
        "",
    ]
    for bunch in items():
        bid = bunch["id"]
        pages = bunch_pages(bid)
        lines.append(f"## {bid} — {bunch.get('note', '')}")
        lines.append("")
        lines.append("| pagina | fedeltà | ordine | tabelle | figure | struttura | verdetto | note |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for orig in pages:
            lines.append(f"| p{orig} |  |  |  |  |  |  |  |")
        lines.append("")
    lines.append("## Metrica di velocità (auto)")
    lines.append("")
    lines.append("| bunch | motore | passata | import ms | totale ms | ms/pagina | RSS MB |")
    lines.append("|---|---|---|---:|---:|---:|---:|")
    for row in _speed_rows(manifest):
        lines.append("| " + " | ".join(str(x) for x in row) + " |")
    lines.append("")
    (OUT_DIR / "scorecard.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    manifest = _collect()
    _write_summary(manifest)
    _write_html(manifest)
    _write_scorecard(manifest)
    print(f"Generati: {OUT_DIR / 'manifest.json'}, summary.md, report.html, scorecard.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
