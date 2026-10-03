#!/usr/bin/env python3
"""Harness E2E dell'agente per le pipeline di estrazione.

**Scopo**: permettere all'agente di eseguire i test end-to-end con un solo
comando, leggere un **report per pagina**, e arbitrare **visivamente solo se
richiesto** (l'agente guardando le PNG, oppure un advisor VLM).

Un entry point, tre modalità — stesso percorso:

- ``--mode auto``   (default): percorso app completo + **gate per tipo**
  (testo/figure/tabelle) + metriche + ``report.jsonl``/``summary.md``.
  Nessun arbitrato: deterministico e **CI-safe**.
- ``--mode visual``: come ``auto``, ma per le **pagine flaggate** produce coppie
  **PNG pagina ↔ markdown** (+ checklist) così l'**agente** arbitra a vista.
- ``--mode advisor``: come ``auto``, ma il verdetto sulle pagine flaggate lo
  scrive un **VLM remoto** (OpenRouter) e finisce nel report.

Il percorso può essere **diretto** (``_apply_ir_on_page`` / motore) oppure
**app reale** (``--via-app``: ``MainWindow`` + ``ExtractThread`` → copre
gate/fallback, cache/revisione, thread, header).

Esempi (dalla root del repo)::

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/e2e.py \\
        corpus1/ha22.pdf --pages 99-102 --mode auto --out /tmp/opencode/e2e
    ... --via-app --pipelines ir,current
    ... --mode visual
    ... --mode advisor --advisor-model meta/muse-spark-1.3-contributor
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_TOOLS = Path(__file__).resolve().parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import verify_pages as vp  # noqa: E402  (riuso di metriche e helper)

# ── soglie del gate per tipo ────────────────────────────────────────────────
RECALL_TEXT_MIN = 0.90      # recall della prosa (se la pagina ha prosa)
RECALL_RENDER_MIN = 0.99    # recall markdown → resa (plain)
RECALL_TABLE_MIN = 0.85     # recall delle celle di tabella
MIN_FIGURE_BYTES = 1000     # una figura embedded più piccola è sospetta

_ADVISOR_DEFAULT_MODEL = "meta/muse-spark-1.3-contributor"
_ADVISOR_URL = "https://openrouter.ai/api/v1/chat/completions"
_ADVISOR_SYSTEM = (
    "Sei un revisore di estrazione PDF. Ricevi l'immagine di UNA pagina e il "
    "markdown estratto dalla nostra pipeline. Giudica se il markdown riporta "
    "TUTTO il contenuto della pagina, UNA sola volta, nell'ordine di lettura "
    "corretto. Rispondi SOLO con un oggetto JSON, senza altro testo, con le "
    "chiavi: text_ok, order_ok, figures_ok, tables_ok (booleani), "
    "missing (lista di stringhe brevi), notes (stringa)."
)


# ── isolamento config (non tocca le impostazioni reali) ─────────────────────
def _isolate_config() -> Path:
    """Ridirige XDG_* verso una tmp dir: nessun impatto sulle config reali."""
    tmp = Path(tempfile.mkdtemp(prefix="noesis-e2e-"))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        os.environ[var] = str(tmp)
    return tmp


# ── verità di riferimento per pagina (una volta per pagina) ─────────────────
def _page_truth(doc, idx: int, with_tables: bool = True) -> dict:
    """Ground truth della pagina: prosa, tipo di pagina, figure, tabelle."""
    import ir_layout
    import main

    page = doc[idx]
    try:
        _t, elements = ir_layout.page_elements(doc, idx)
    except Exception:
        elements = []
    pdf_text = main._page_text_no_figures(page, elements)
    ntexty = sum(
        1 for e in elements if e.get("class") in ("text", "section-header", "title")
    )
    expected_figs = sum(1 for e in elements if e.get("class") == "picture")
    table_text = ""
    if with_tables:
        try:
            table_text = " ".join(
                str(c)
                for t in page.find_tables().tables
                for row in (t.extract() or [])
                for c in row
                if c
            )
        except Exception:
            table_text = ""
    return {
        "elements": elements,
        "page_width": page.rect.width,
        "pdf_text": pdf_text,
        "ntexty": ntexty,
        "expected_figs": expected_figs,
        "table_text": table_text,
        "is_index": main._looks_like_index(pdf_text),
    }


def _checks(truth: dict, md: str, plain: str) -> dict:
    """Controlli di completezza **per tipo** su una pagina estratta."""
    import main

    body = vp._strip_images(md)
    r_pdf = vp._recall(truth["pdf_text"], body)
    r_ren = vp._recall(body, plain)

    # ── testo ──
    tnotes: list[str] = []
    text_ok = True
    if not (md or "").strip() and truth["pdf_text"].strip():
        text_ok, _ = False, tnotes.append("body vuoto")
    if truth["ntexty"] > 2 and r_pdf < RECALL_TEXT_MIN:
        text_ok = False
        tnotes.append(f"recall {r_pdf:.2f}")
    if truth["is_index"]:
        text_ok = False
        tnotes.append("indice")

    # ── figure (embedded base64) ──
    emb = re.findall(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+", md)
    fig_sizes = [len(e) for e in emb]
    fnotes: list[str] = []
    fig_ok = True
    if truth["expected_figs"] > 0 and not emb:
        fig_ok = False
        fnotes.append(f"attese {truth['expected_figs']}, nessuna embedded")
    tiny = [s for s in fig_sizes if s < MIN_FIGURE_BYTES]
    if tiny:
        fig_ok = False
        fnotes.append(f"{len(tiny)} figure vuote")

    # ── tabelle ──
    tnotes2: list[str] = []
    tbl_ok = True
    tbl_recall = None
    if truth["table_text"]:
        tbl_recall = vp._recall(truth["table_text"], body)
        if tbl_recall < RECALL_TABLE_MIN:
            tbl_ok = False
            tnotes2.append(f"celle recall {tbl_recall:.2f}")

    checks = {
        "text": {
            "ok": text_ok, "recall_pdf": round(r_pdf, 4),
            "recall_render": round(r_ren, 4), "note": "; ".join(tnotes),
        },
        "figures": {
            "ok": fig_ok, "expected": truth["expected_figs"],
            "embedded": len(emb), "note": "; ".join(fnotes),
        },
        "tables": {
            "ok": tbl_ok, "recall": None if tbl_recall is None else round(tbl_recall, 4),
            "note": "; ".join(tnotes2),
        },
    }
    return checks


def _flags(checks: dict, gate: dict | None) -> list[str]:
    out: list[str] = []
    for kind in ("text", "figures", "tables"):
        c = checks.get(kind) or {}
        if not c.get("ok", True):
            out.append(f"{kind}: {c.get('note', '')}".strip().rstrip(":"))
    if gate is not None and not gate.get("ok", True):
        out.append(f"gate: {gate.get('reason', '')}".strip().rstrip(":"))
    return out


# ── costruzione markdown (percorso diretto) ─────────────────────────────────
def _build_direct(pdf: str, doc, page, idx: int, pipe: str, figdir: Path,
                  ocr_lang: str, elements: list[dict] | None = None) -> tuple[str, dict | None]:
    import main

    if pipe == "ir":
        md, _raw, gate_ok = main._apply_ir_on_page(pdf, idx, figures_dir=figdir)
        md = md or ""
        reason = ""
        try:
            _ok, reason = main._ir_gate(page, md, elements or [])
        except Exception:
            reason = ""
        return md, {"ok": bool(gate_ok), "reason": reason}
    return vp._build_md(pipe, pdf, doc, page, idx, figdir, ocr_lang) or "", None


def _render_plain(panel, md: str) -> str:
    panel.show_text(md, as_markdown=True)
    return panel.toPlainText() or ""


# ── percorso APP reale (MainWindow + ExtractThread) ─────────────────────────
def _run_via_app(pdf: str, pages: list[int], pipelines: list[str], out: Path,
                 qapp, truth: dict, timeout: float) -> list[dict]:
    import main

    records: list[dict] = []
    for pipe in pipelines:
        os.environ["NOESIS_PIPELINE"] = pipe
        win = main.MainWindow()
        win.resize(900, 1000)
        win.show()
        try:
            win._open_pdf(Path(pdf))
            qapp.processEvents()
            ocr_lang = main._tess_lang_code(main.get_source_lang())
            for idx in pages:
                if idx < 0 or idx >= win._page_count:
                    continue
                win._set_page(idx)
                key = (idx, ocr_lang, "()")
                t0 = time.perf_counter()
                while (key not in win._final_text_cache
                       and time.perf_counter() - t0 < timeout):
                    qapp.processEvents()
                    time.sleep(0.02)
                qapp.processEvents()
                cached = win._final_text_cache.get(key)
                text, label, elapsed = cached if cached else ("", "", 0.0)
                body = getattr(win.text_panel, "_page_body", "") or ""
                plain = win.text_panel.origin_panel.toPlainText() or ""
                png = win.text_panel.origin_panel.grab()
                rec = _make_record(
                    pdf, idx, pipe, truth[idx], body, plain,
                    secs=round(elapsed or (time.perf_counter() - t0), 2),
                    gate=None, via_app=True, label=label,
                )
                pd = out / pipe
                pd.mkdir(parents=True, exist_ok=True)
                (pd / f"page_{idx:04d}.md").write_text(body, encoding="utf-8")
                (pd / f"page_{idx:04d}.plain.txt").write_text(plain, encoding="utf-8")
                png.save(str(pd / f"page_{idx:04d}.png"))
                records.append(rec)
                _print_rec(rec)
        finally:
            try:
                win.close()
            except Exception:
                pass
            qapp.processEvents()
    return records


# ── percorso diretto ────────────────────────────────────────────────────────
def _run_direct(pdf: str, doc, pages: list[int], pipelines: list[str], out: Path,
                qapp, truth: dict, ocr_lang: str) -> list[dict]:
    import main

    panel = main.TextPanel()
    panel.resize(800, 900)
    panel.show()
    records: list[dict] = []
    try:
        for idx in pages:
            if idx < 0 or idx >= len(doc):
                continue
            page = doc[idx]
            for pipe in pipelines:
                figdir = out / pipe / "fig"
                figdir.mkdir(parents=True, exist_ok=True)
                t0 = time.perf_counter()
                try:
                    md, gate = _build_direct(
                        pdf, doc, page, idx, pipe, figdir, ocr_lang,
                        truth[idx]["elements"])
                except Exception as e:  # noqa: BLE001
                    md, gate = "", None
                    print(f"  ! {pipe} p{idx}: {e!r}")
                dt = time.perf_counter() - t0
                qapp.processEvents()
                plain = _render_plain(panel, md)
                qapp.processEvents()
                tail = vp._last_block_height(panel)
                rec = _make_record(
                    pdf, idx, pipe, truth[idx], md, plain,
                    secs=round(dt, 2), gate=gate, via_app=False, label="auto",
                )
                rec["tail"] = round(tail, 1)
                pd = out / pipe
                pd.mkdir(parents=True, exist_ok=True)
                (pd / f"page_{idx:04d}.md").write_text(md, encoding="utf-8")
                (pd / f"page_{idx:04d}.plain.txt").write_text(plain, encoding="utf-8")
                panel.grab().save(str(pd / f"page_{idx:04d}.png"))
                records.append(rec)
                _print_rec(rec)
    finally:
        panel.close()
    return records


def _make_record(pdf: str, idx: int, pipe: str, truth: dict, md: str, plain: str,
                 secs: float, gate: dict | None, via_app: bool, label: str) -> dict:
    import main

    checks = _checks(truth, md, plain)
    flags = _flags(checks, gate)
    order = 1.0
    try:
        order = vp._order_score(md, truth["elements"], truth["page_width"])
    except Exception:
        order = 1.0
    return {
        "pdf": Path(pdf).name,
        "page_idx": idx,
        "page_ui": idx + 1,
        "pipeline": pipe,
        "via_app": via_app,
        "label": label,
        "secs": secs,
        "body_len": len(md),
        "plain_len": len(plain),
        "order_score": round(order, 3),
        "checks": checks,
        "gate": gate,
        "flags": flags,
        "verdict": "review" if flags else "auto-ok",
        "arbitration": None,  # riempito in modalità visual/advisor
        "regression": None,   # riempito se --baseline
    }


def _print_rec(rec: dict) -> None:
    c = rec["checks"]
    print(
        f"{rec['pipeline']:8s} p{rec['page_idx']:4d} {rec['secs']:6.2f}s "
        f"body={rec['body_len']:7d} text={c['text']['recall_pdf']:.3f} "
        f"fig={c['figures']['embedded']}/{c['figures']['expected']} "
        f"tbl={'ok' if c['tables']['ok'] else 'KO'} "
        f"order={rec['order_score']:.2f} "
        f"{'✓' if not rec['flags'] else '⚠ ' + '; '.join(rec['flags'])}"
    )


# ── difetti ─────────────────────────────────────────────────────────────────
def _collect_defects(records: list[dict]) -> list[dict]:
    """Aggrega i difetti: flag automatici **e** verdetto dell'advisor visivo."""
    defects: list[dict] = []

    def _add(rec: dict, source: str, kind: str, note: str, verdict) -> None:
        defects.append({
            "pdf": rec["pdf"], "page_idx": rec["page_idx"],
            "page_ui": rec["page_ui"], "pipeline": rec["pipeline"],
            "source": source, "kind": kind, "note": note, "verdict": verdict,
        })

    for r in records:
        for f in r["flags"]:
            kind, _, note = f.partition(":")
            _add(r, "auto", kind.strip(), note.strip(), None)
        arb = r.get("arbitration") or {}
        v = arb.get("verdict")
        if not isinstance(v, dict) or "error" in v or "raw" in v:
            continue
        for key, kind in (("text_ok", "text"), ("order_ok", "order"),
                          ("figures_ok", "figures"), ("tables_ok", "tables")):
            if v.get(key) is False:
                _add(r, "advisor", kind, str(v.get("notes", "")), v)
        for miss in (v.get("missing") or []):
            _add(r, "advisor", "missing", str(miss), v)
    return defects


# ── report ──────────────────────────────────────────────────────────────────
def _write_reports(out: Path, records: list[dict], pipelines: list[str],
                   mode: str, pdf: str,
                   regressions: list[dict] | None = None,
                   total_secs: float | None = None) -> None:
    (out / "report.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
        encoding="utf-8",
    )
    ext_sum = sum(r["secs"] for r in records)
    arb_rows = [r for r in records
                if (r.get("arbitration") or {}).get("mode") == "advisor"]
    arb_sum = sum((r["arbitration"].get("secs") or 0.0) for r in arb_rows)
    (out / "meta.json").write_text(
        json.dumps({
            "pdf": Path(pdf).name, "mode": mode, "pipelines": pipelines,
            "pages": sorted({r["page_idx"] for r in records}),
            "n_records": len(records),
            "total_secs": None if total_secs is None else round(total_secs, 2),
            "extraction_secs_sum": round(ext_sum, 2),
            "advisor_calls": len(arb_rows),
            "advisor_secs_sum": round(arb_sum, 2),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        f"# Harness E2E — {Path(pdf).name}",
        "",
        f"- modalità: **{mode}**",
        f"- pipeline: {', '.join(pipelines)}",
        f"- pagine: {len({r['page_idx'] for r in records})}",
        f"- **tempo totale**: {total_secs:.1f}s" if total_secs is not None
        else "- tempo totale: n/d",
        f"- estrazione (somma record): {ext_sum:.1f}s",
        f"- advisor: {arb_sum:.1f}s su {len(arb_rows)} chiamate",
        "",
        "## Medie per pipeline",
        "",
        "| pipeline | secs | text recall | order | figure ok | tabelle ok | flag |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in pipelines:
        rows = [r for r in records if r["pipeline"] == p]
        if not rows:
            continue
        n = len(rows)
        fig_ok = sum(1 for r in rows if r["checks"]["figures"]["ok"])
        tbl_ok = sum(1 for r in rows if r["checks"]["tables"]["ok"])
        flags = sum(1 for r in rows if r["flags"])
        lines.append(
            f"| {p} | {sum(r['secs'] for r in rows)/n:.2f} | "
            f"{sum(r['checks']['text']['recall_pdf'] for r in rows)/n:.3f} | "
            f"{sum(r['order_score'] for r in rows)/n:.3f} | "
            f"{fig_ok}/{n} | {tbl_ok}/{n} | {flags}/{n} |"
        )
    flagged = [r for r in records if r["flags"]]
    lines += ["", f"## Pagine flaggate ({len(flagged)})", ""]
    if not flagged:
        lines.append("Nessuna: tutte le pagine passano i controlli automatici.")
    else:
        lines.append("| pagina | pipeline | flag | arbitraggio |")
        lines.append("|---|---|---|---|")
        for r in flagged:
            arb = (r.get("arbitration") or {}).get("verdict", "")
            lines.append(
                f"| {r['page_ui']} | {r['pipeline']} | "
                f"{'; '.join(r['flags'])} | {arb} |"
            )
    if regressions:
        lines += ["", f"## Regressioni ({len(regressions)})", "",
                  "| chiave | regressioni |", "|---|---|"]
        for x in regressions:
            lines.append(f"| {x['key']} | {'; '.join(x['issues'])} |")
    defects = _collect_defects(records)
    (out / "defects.jsonl").write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in defects),
        encoding="utf-8",
    )
    lines += ["", f"## Difetti ({len(defects)})", ""]
    if not defects:
        lines.append("Nessun difetto raccolto.")
    else:
        lines += ["| pagina | pipeline | fonte | tipo | nota |", "|---|---|---|---|---|"]
        for d in defects:
            note = (d["note"] or "").replace("|", "/")[:100]
            lines.append(
                f"| {d['page_ui']} | {d['pipeline']} | {d['source']} | "
                f"{d['kind']} | {note} |"
            )
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport: {out/'summary.md'} ({len(defects)} difetti)")


# ── arbitraggio visivo (agente) ─────────────────────────────────────────────
def _write_review(out: Path, pdf: str, records: list[dict], doc) -> None:
    """Per le pagine flaggate: PNG pagina ↔ markdown + checklist per l'agente."""
    import pymupdf

    review = out / "review"
    review.mkdir(parents=True, exist_ok=True)
    flagged = [r for r in records if r["flags"]]
    index = [
        "# Review visivo — pagine flaggate",
        "",
        "Per ogni voce: apri la PNG (pagina originale) e il markdown, poi giudica.",
        "",
    ]
    for r in flagged:
        idx = r["page_idx"]
        tag = f"{r['pipeline']}_p{idx:04d}"
        png = review / f"{tag}_page.png"
        page = doc[idx]
        page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(str(png))
        md_src = out / r["pipeline"] / f"page_{idx:04d}.md"
        md_dst = review / f"{tag}.md"
        if md_src.exists():
            md_dst.write_text(md_src.read_text(encoding="utf-8"), encoding="utf-8")
        index += [
            f"## p{r['page_ui']} — {r['pipeline']}",
            f"- flag: {'; '.join(r['flags'])}",
            f"- PNG pagina: `{png.name}`",
            f"- markdown: `{md_dst.name}`",
            "",
        ]
        r["arbitration"] = {"mode": "visual", "verdict": "pending (agente)"}
    (review / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"Review: {review/'index.md'} ({len(flagged)} pagine)")


# ── advisor VLM remoto (OpenRouter) ─────────────────────────────────────────
def _advisor_judge(png_bytes: bytes, md: str, model: str, key: str,
                   url: str = _ADVISOR_URL, timeout: float = 120.0) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _ADVISOR_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": "Markdown estratto:\n\n" + md[:20000]},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(png_bytes).decode()
                }},
            ]},
        ],
        "temperature": 0,
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/vigliafg/noesis-pdf-reader-lite",
            "X-Title": "noesis-pdf-reader-lite e2e advisor",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    m = re.search(r"\{.*\}", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {"raw": content}


def _run_advisor(out: Path, records: list[dict], doc, model: str, key: str,
                 max_calls: int) -> None:
    import pymupdf

    review = out / "review"
    review.mkdir(parents=True, exist_ok=True)
    # L'advisor giudica TUTTE le pagine richieste (anche quelle non auto-flaggate):
    # il gate automatico può non vedere un difetto. `max_calls>0` è solo un tetto.
    targets = records[:max_calls] if max_calls else records
    for r in targets:
        idx = r["page_idx"]
        page = doc[idx]
        png = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
        md = (out / r["pipeline"] / f"page_{idx:04d}.md")
        md_text = md.read_text(encoding="utf-8") if md.exists() else ""
        t0 = time.perf_counter()
        try:
            verdict = _advisor_judge(png, md_text, model, key)
        except urllib.error.HTTPError as e:
            verdict = {"error": f"HTTP {e.code}: {e.read()[:200].decode(errors='replace')}"}
        except Exception as e:  # noqa: BLE001
            verdict = {"error": repr(e)}
        dt = time.perf_counter() - t0
        r["arbitration"] = {
            "mode": "advisor", "model": model, "secs": round(dt, 2),
            "verdict": verdict,
        }
        (review / f"{r['pipeline']}_p{idx:04d}_advisor.json").write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"advisor p{r['page_ui']}: {dt:5.1f}s {verdict}")


# ── regressione (baseline) ──────────────────────────────────────────────────
_BASELINE_VERSION = 1
TOL_RECALL = 0.01   # calo massimo ammesso sul recall della prosa
TOL_ORDER = 0.02    # calo massimo ammesso sull'order score
TOL_BODY = 0.20     # calo massimo (frazione) sul body_len
TOL_TABLE = 0.05    # calo massimo ammesso sul recall delle tabelle


def _baseline_key(rec: dict) -> str:
    return f"{rec['pdf']}|{rec['pipeline']}|{rec['page_idx']}"


def _baseline_entry(rec: dict) -> dict:
    c = rec["checks"]
    return {
        "recall_pdf": c["text"]["recall_pdf"],
        "order_score": rec["order_score"],
        "body_len": rec["body_len"],
        "figures": c["figures"]["embedded"],
        "table_recall": c["tables"]["recall"],
    }


def _load_baseline(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("entries", {})
    except Exception:
        return {}


def _write_baseline(path: Path, records: list[dict]) -> None:
    # merge: più run (anche su PDF diversi) accumulano nello stesso baseline
    entries = _load_baseline(path)
    entries.update({_baseline_key(r): _baseline_entry(r) for r in records})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": _BASELINE_VERSION, "entries": entries},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Baseline scritta: {path} ({len(entries)} voci)")


def _compare_baseline(records: list[dict], entries: dict) -> list[dict]:
    """Annota ogni record con le regressioni rispetto al baseline."""
    regs: list[dict] = []
    for r in records:
        base = entries.get(_baseline_key(r))
        if not base:
            continue
        cur = _baseline_entry(r)
        issues: list[str] = []
        if base["recall_pdf"] - cur["recall_pdf"] > TOL_RECALL:
            issues.append(f"recall {base['recall_pdf']:.3f}->{cur['recall_pdf']:.3f}")
        if base["order_score"] - cur["order_score"] > TOL_ORDER:
            issues.append(f"order {base['order_score']:.3f}->{cur['order_score']:.3f}")
        if base["body_len"] and cur["body_len"] < base["body_len"] * (1 - TOL_BODY):
            issues.append(f"body {base['body_len']}->{cur['body_len']}")
        if cur["figures"] < base["figures"]:
            issues.append(f"figure {base['figures']}->{cur['figures']}")
        if (base["table_recall"] is not None and cur["table_recall"] is not None
                and base["table_recall"] - cur["table_recall"] > TOL_TABLE):
            issues.append(f"tabella {base['table_recall']:.2f}->{cur['table_recall']:.2f}")
        r["regression"] = issues or None
        if issues:
            regs.append({"key": _baseline_key(r), "issues": issues})
    return regs


# ── main ────────────────────────────────────────────────────────────────────
def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Harness E2E dell'agente")
    ap.add_argument("pdf")
    ap.add_argument("--pages", required=True)
    ap.add_argument("--out", default="/tmp/opencode/e2e")
    ap.add_argument("--mode", choices=("auto", "visual", "advisor"), default="auto")
    ap.add_argument("--pipelines", default="ir")
    ap.add_argument("--via-app", action="store_true")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--no-tables", action="store_true",
                    help="salta find_tables (ground truth tabelle)")
    ap.add_argument("--advisor-model", default=_ADVISOR_DEFAULT_MODEL)
    ap.add_argument("--advisor-key-env", default="OPENROUTER_API_KEY")
    ap.add_argument("--advisor-max", type=int, default=0,
                    help="tetto al numero di pagine giudicate (0 = tutte)")
    ap.add_argument("--baseline", default=None,
                    help="confronta i risultati con un baseline JSON (regressioni)")
    ap.add_argument("--update-baseline", default=None,
                    help="scrive il baseline JSON dai risultati di questo run")
    args = ap.parse_args(argv)
    t_start = time.perf_counter()

    _isolate_config()

    import pymupdf
    from PyQt6.QtWidgets import QApplication

    import main

    main.init_config(str(main._config_file_path()))
    main.set_language("it")

    pdf = str(Path(args.pdf).resolve())
    pages = vp._parse_pages(args.pages)
    pipelines = [p.strip() for p in args.pipelines.split(",") if p.strip()]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    qapp = QApplication.instance() or QApplication([])
    doc = pymupdf.open(pdf)
    ocr_lang = main._tess_lang_code(main.get_source_lang())
    try:
        truth = {i: _page_truth(doc, i, with_tables=not args.no_tables)
                 for i in pages if 0 <= i < len(doc)}
        if args.via_app:
            records = _run_via_app(pdf, pages, pipelines, out, qapp, truth,
                                   args.timeout)
        else:
            records = _run_direct(pdf, doc, pages, pipelines, out, qapp, truth,
                                  ocr_lang)

        if args.mode == "visual":
            _write_review(out, pdf, records, doc)
        elif args.mode == "advisor":
            key = os.environ.get(args.advisor_key_env, "")
            if not key:
                print(f"! advisor: manca la chiave (env {args.advisor_key_env}) — "
                      "salto l'arbitraggio")
            else:
                _run_advisor(out, records, doc, args.advisor_model, key,
                             args.advisor_max)
    finally:
        doc.close()

    regs: list[dict] = []
    if args.update_baseline:
        _write_baseline(Path(args.update_baseline), records)
    if args.baseline:
        entries = _load_baseline(Path(args.baseline))
        regs = _compare_baseline(records, entries)
        if regs:
            print(f"⚠ REGRESSIONI: {len(regs)}")
            for x in regs:
                print(f"   {x['key']}: {'; '.join(x['issues'])}")
        else:
            print("Baseline: nessuna regressione")

    total = time.perf_counter() - t_start
    print(f"Tempo totale: {total:.1f}s")
    _write_reports(out, records, pipelines, args.mode, pdf, regs, total)
    return 2 if regs else 0


def main() -> int:  # pragma: no cover - CLI
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
