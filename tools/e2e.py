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
import layout_proxies as lpx  # noqa: E402  (proxy + confidenza, Fase 0.3)

# ── soglie del gate per tipo ────────────────────────────────────────────────
RECALL_TEXT_MIN = 0.90      # recall della prosa (se la pagina ha prosa)
RECALL_RENDER_MIN = 0.99    # recall markdown → resa (plain)
RECALL_TABLE_MIN = 0.85     # recall delle celle di tabella
MIN_FIGURE_BYTES = 1000     # una figura embedded più piccola è sospetta
# Golden Rule #1 (REGOLE-TEST.md): fedeltà = contenuto E flusso di lettura.
# Un flusso < FLOW_MIN è un difetto GRAVE, anche con recall 1.0.
FLOW_MIN = 0.95             # fedeltà minima dell'ordine di lettura (LIS/n)

_ADVISOR_DEFAULT_MODEL = "meta/muse-spark-1.3-contributor"
_ADVISOR_URL = "https://openrouter.ai/api/v1/chat/completions"
# Tassonomia dei difetti: `marginalia` NON è un difetto di contenuto (testate
# correnti, numero pagina, fasce "PART n", elenchi collaboratori).
_DEFECT_KINDS = (
    "figure_missing", "figure_duplicate", "figure_order", "figure_text_bleed",
    "figure_caption", "table_structure", "table_content", "equation",
    "text_missing", "text_order", "ref_order", "header_content",
    "index_truncate", "marginalia", "other",
)
_ADVISOR_SYSTEM = (
    "Sei un revisore di estrazione PDF. Ricevi l'immagine di UNA pagina e il "
    "markdown estratto dalla nostra pipeline. Giudica se il markdown riporta "
    "TUTTO il contenuto della pagina, UNA volta, nell'ordine di lettura "
    "corretto. Figure ed equazioni possono essere rese come immagini embedded, "
    "indicate da marcatori del tipo [FIGURA: ...] (i byte non sono inclusi): "
    "considerale PRESENTI (giudicane presenza/posizione dall'immagine della "
    "pagina), NON segnalarle come mancanti solo perché non trascritte come "
    "testo.\n"
    "NON sono difetti di contenuto: numero di pagina, testate correnti "
    "(es. 'CHAPTER n'), fasce laterali (es. 'PART n'), elenchi di collaboratori: "
    "classificali come kind 'marginalia' (severita' 'low').\n"
    "Rispondi SOLO con un oggetto JSON, senza altro testo, con le chiavi: "
    "text_ok, order_ok, figures_ok, tables_ok (booleani); "
    "defects (lista di {kind, severity, note}); notes (stringa). "
    "kind e' uno tra: " + "|".join(_DEFECT_KINDS) + ". "
    "severity e' 'high'|'medium'|'low'. Non usare 'marginalia' per altro."
)

_BASE64_IMG_RE = re.compile(
    r"!\[([^\]]*)\]\(data:image/[^;)]+;base64,[^)]*\)")
_BARE_BASE64_RE = re.compile(r"data:image/[^;)]+;base64,[A-Za-z0-9+/=]+")


def _strip_base64_for_prompt(md: str) -> str:
    """Sostituisce le figure base64 con marcatori: il VLM deve vedere il testo.

    Senza questo, su una pagina con figure all'inizio il markdown (centinaia di
    KB di base64) verrebbe troncato e il modello vedrebbe *solo* l'immagine,
    giudicando per errore che tutto il testo manchi.
    """
    md = _BASE64_IMG_RE.sub(r"[FIGURA: \1]", md)
    return _BARE_BASE64_RE.sub("[FIGURA]", md)


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
    expected_figs = len(ir_layout.real_pictures(
        elements, page.rect.width, page.rect.height))
    table_text = ""
    if with_tables:
        # ground truth = **parole di pagina** nella regione delle tabelle
        # (robusto; `find_tables` dà celle garbled/parziali). Tabelle ruotate
        # escluse (rese come immagine).
        try:
            rot = {tuple(r) for r in main._rotated_table_rects(page, elements)}
            tbl_rects = [e["bbox"] for e in elements
                         if e.get("class") == "table"
                         and tuple(e["bbox"]) not in rot]
            if tbl_rects:
                def _in_tbl(b):
                    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
                    return any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3]
                               for r in tbl_rects)
                table_text = " ".join(w[4] for w in page.get_text("words")
                                      if _in_tbl(w[:4]))
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
    if truth["is_index"] and r_pdf < RECALL_TEXT_MIN:
        text_ok = False
        tnotes.append(f"indice recall {r_pdf:.2f}")

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

    # ── flusso di lettura (Golden Rule #1) ──
    try:
        flow_info = vp._flow_score(md, truth.get("elements", []),
                                   truth.get("page_width", 0.0))
    except Exception:
        flow_info = {"flow": 1.0, "n": 0, "found": 0, "lis": 0,
                     "transitions": 0}
    flow = flow_info["flow"]
    flow_ok = flow >= FLOW_MIN
    fnotes2 = [] if flow_ok else [f"ordine {flow:.2f} "
                                  f"(transizioni {flow_info['transitions']})"]

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
        "flow": {
            "ok": flow_ok, "score": flow, "n": flow_info["n"],
            "found": flow_info["found"], "transitions": flow_info["transitions"],
            "note": "; ".join(fnotes2),
        },
    }
    return checks


def _flags(checks: dict, gate: dict | None) -> list[str]:
    out: list[str] = []
    for kind in ("text", "flow", "figures", "tables"):
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


def _engine_used(pdf: str, idx: int, figures_dir) -> tuple[str, bool]:
    """Quale pipeline ha prodotto il testo finale nell'app: ``ir`` o ``current``.

    L'``ExtractThread`` non espone la pipeline usata (l'etichetta è ``"auto"`` in
    entrambi i casi). La ricaviamo in modo **deterministico** con la stessa
    decisione dell'app (``main._select_page_output``: gate + chooser IR/current).
    """
    import main

    try:
        _text, engine, _raw = main._select_page_output(
            pdf, idx, figures_dir=figures_dir)
    except Exception:
        return "current", False
    return engine, engine == "ir"


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
                # attribuzione: quale pipeline ha davvero prodotto il testo
                engine, _gate_ok = _engine_used(pdf, idx, win._get_images_dir())
                rec = _make_record(
                    pdf, idx, pipe, truth[idx], body, plain,
                    secs=round(elapsed or (time.perf_counter() - t0), 2),
                    gate=None, via_app=True, label=label, engine=engine,
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
                 secs: float, gate: dict | None, via_app: bool, label: str,
                 engine: str | None = None) -> dict:
    import main

    checks = _checks(truth, md, plain)
    flags = _flags(checks, gate)
    order = 1.0
    try:
        order = vp._order_score(md, truth["elements"], truth["page_width"])
    except Exception:
        order = 1.0
    # proxy + confidenza (Fase 0.3): il gate deterministico, se c'è, è un segnale
    # forte; in via-app (gate=None) lo deduciamo dai controlli per tipo.
    if gate is None:
        gate_ok = all(checks[k]["ok"] for k in ("text", "flow", "figures", "tables"))
        gate_reason = ""
    else:
        gate_ok = bool(gate.get("ok", True))
        gate_reason = str(gate.get("reason", "") or "")
    try:
        proxies = lpx.all_proxies(truth.get("elements", []),
                                  truth.get("page_width", 0.0), md,
                                  gate_ok=gate_ok, gate_reason=gate_reason)
    except Exception:  # pragma: no cover - il report non deve mai rompersi
        proxies = {}
    return {
        "pdf": Path(pdf).name,
        "page_idx": idx,
        "page_ui": idx + 1,
        "pipeline": pipe,
        "engine": engine or pipe,  # pipeline che ha prodotto il testo (via-app: ir|current)
        "via_app": via_app,
        "label": label,
        "secs": secs,
        "body_len": len(md),
        "plain_len": len(plain),
        "order_score": round(order, 3),
        "flow_score": checks["flow"]["score"],
        "flow_transitions": checks["flow"]["transitions"],
        "checks": checks,
        "gate": gate,
        "flags": flags,
        "verdict": "review" if flags else "auto-ok",
        "proxies": proxies,
        "confidence": proxies.get("confidence"),
        "escalate": proxies.get("escalate"),
        "layout_class": proxies.get("layout_class"),
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
        f"order={rec['order_score']:.2f} flow={rec.get('flow_score', 1.0):.2f} "
        f"{'✓' if not rec['flags'] else '⚠ ' + '; '.join(rec['flags'])}"
    )


# ── difetti ─────────────────────────────────────────────────────────────────
# I flag automatici sono grossolani: mappati sulla stessa tassonomia.
_AUTO_KIND = {"tables": "table_content", "text": "text_missing",
              "flow": "text_order",
              "figures": "figure_missing", "gate": "other"}


def _collect_defects(records: list[dict]) -> list[dict]:
    """Aggrega i difetti in modo **categorizzato** (kind + severita' + real).

    Fonte ``auto`` (gate/metriche) e ``advisor`` (VLM). I difetti di
    ``marginalia`` sono marcati ``real=False``: non sono difetti di contenuto.
    """
    defects: list[dict] = []

    def _add(rec: dict, source: str, kind: str, note: str, severity: str,
             verdict) -> None:
        defects.append({
            "pdf": rec["pdf"], "page_idx": rec["page_idx"],
            "page_ui": rec["page_ui"], "pipeline": rec["pipeline"],
            "engine": rec.get("engine"),  # pipeline che ha prodotto il testo
            "source": source, "kind": kind, "severity": severity,
            "real": kind != "marginalia", "note": note, "verdict": verdict,
        })

    for r in records:
        for f in r["flags"]:
            prefix, _, note = f.partition(":")
            kind = _AUTO_KIND.get(prefix.strip(), "other")
            _add(r, "auto", kind, note.strip(), "high", None)
        arb = r.get("arbitration") or {}
        v = arb.get("verdict")
        if not isinstance(v, dict) or "error" in v or "raw" in v:
            continue
        listed = v.get("defects")
        if isinstance(listed, list) and listed:
            for d in listed:
                if not isinstance(d, dict):
                    continue
                kind = str(d.get("kind") or "other")
                if kind not in _DEFECT_KINDS:
                    kind = "other"
                _add(r, "advisor", kind, str(d.get("note", "")),
                     str(d.get("severity") or "medium"), v)
            continue
        # fallback: schema a soli booleani (vecchie risposte)
        for key, kind in (("text_ok", "text_missing"), ("order_ok", "text_order"),
                          ("figures_ok", "figure_missing"),
                          ("tables_ok", "table_structure")):
            if v.get(key) is False:
                _add(r, "advisor", kind, str(v.get("notes", "")), "medium", v)
        for miss in (v.get("missing") or []):
            _add(r, "advisor", "other", str(miss), "low", v)
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
            "escalations": sum(1 for r in records if r.get("escalate")),
            "mean_confidence": round(
                sum(r["confidence"] for r in records
                    if r.get("confidence") is not None)
                / max(1, sum(1 for r in records
                              if r.get("confidence") is not None)), 4),
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    eng_counts: dict[str, int] = {}
    for r in records:
        k = r.get("engine") or "?"
        eng_counts[k] = eng_counts.get(k, 0) + 1
    confs = [r["confidence"] for r in records if r.get("confidence") is not None]
    mean_conf = sum(confs) / len(confs) if confs else None
    escalated = [r for r in records if r.get("escalate")]
    esc_noflag = [r for r in escalated if not r["flags"]]
    lines = [
        f"# Harness E2E — {Path(pdf).name}",
        "",
        f"- modalità: **{mode}**",
        f"- pipeline: {', '.join(pipelines)}",
        f"- engine (attribuzione): {', '.join(f'{k}={v}' for k, v in eng_counts.items())}",
        f"- pagine: {len({r['page_idx'] for r in records})}",
        (f"- confidenza media: {mean_conf:.3f} · "
         f"escalation: {len(escalated)}/{len(records)} "
         f"(soglia {lpx.ESCALATION_THRESHOLD})"
         if mean_conf is not None else "- confidenza: n/d"),
        f"- **tempo totale**: {total_secs:.1f}s" if total_secs is not None
        else "- tempo totale: n/d",
        f"- estrazione (somma record): {ext_sum:.1f}s",
        f"- advisor: {arb_sum:.1f}s su {len(arb_rows)} chiamate",
        "",
        "## Medie per pipeline",
        "",
        "| pipeline | secs | text recall | flow | order | figure ok | tabelle ok | flag |",
        "|---|---|---|---|---|---|---|---|",
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
            f"{sum(r.get('flow_score', 1.0) for r in rows)/n:.3f} | "
            f"{sum(r['order_score'] for r in rows)/n:.3f} | "
            f"{fig_ok}/{n} | {tbl_ok}/{n} | {flags}/{n} |"
        )
    flagged = [r for r in records if r["flags"]]
    lines += ["", f"## Pagine flaggate ({len(flagged)})", ""]
    if not flagged:
        lines.append("Nessuna: tutte le pagine passano i controlli automatici.")
    else:
        lines.append("| pagina | pipeline | engine | conf | flag | arbitraggio |")
        lines.append("|---|---|---|---|---|---|")
        for r in flagged:
            arb = (r.get("arbitration") or {}).get("verdict", "")
            conf = r.get("confidence")
            conf_s = f"{conf:.2f}" if conf is not None else "n/d"
            lines.append(
                f"| {r['page_ui']} | {r['pipeline']} | {r.get('engine')} | "
                f"{conf_s} | {'; '.join(r['flags'])} | {arb} |"
            )
    # valore aggiunto del proxy: pagine che il gate NON flagga ma la confidenza
    # manda in escalation (obiettivo Fase 0: catturare ≥80% dei difetti reali).
    if esc_noflag:
        lines += ["", f"## Escalation proxy senza flag del gate ({len(esc_noflag)})", "",
                  "| pagina | classe | conf | proxy debole |", "|---|---|---|---|"]
        for r in esc_noflag:
            prox = r.get("proxies") or {}
            weak = min(
                (("order", prox.get("order")), ("table", prox.get("table")),
                 ("figure", prox.get("figure")), ("text", prox.get("text"))),
                key=lambda kv: (kv[1] or {}).get("score", 1.0),
            )
            lines.append(
                f"| {r['page_ui']} | {r.get('layout_class')} | "
                f"{r.get('confidence'):.2f} | {weak[0]}="
                f"{(weak[1] or {}).get('score', 1.0):.2f} |"
            )
    if regressions:
        lines += ["", f"## Regressioni ({len(regressions)})", "",
                  "| chiave | regressioni |", "|---|---|"]
        for x in regressions:
            lines.append(f"| {x['key']} | {'; '.join(x['issues'])} |")
    defects = _collect_defects(records)
    real = [d for d in defects if d["real"]]
    marg = [d for d in defects if not d["real"]]
    (out / "defects.jsonl").write_text(
        "\n".join(json.dumps(d, ensure_ascii=False) for d in defects),
        encoding="utf-8",
    )
    kinds: dict[str, int] = {}
    for d in real:
        kinds[d["kind"]] = kinds.get(d["kind"], 0) + 1
    lines += ["", f"## Difetti ({len(real)} reali + {len(marg)} marginalia)", ""]
    if not defects:
        lines.append("Nessun difetto raccolto.")
    else:
        if kinds:
            top = sorted(kinds.items(), key=lambda x: -x[1])
            lines.append("**Tipi (reali):** "
                         + ", ".join(f"{k}={v}" for k, v in top))
            lines.append("")
        lines += ["| pagina | engine | fonte | tipo | sev | nota |",
                  "|---|---|---|---|---|---|"]
        for d in defects:
            note = (d["note"] or "").replace("|", "/")[:100]
            lines.append(
                f"| {d['page_ui']} | {d.get('engine')} | {d['source']} | "
                f"{d['kind']} | {d['severity']} | {note} |"
            )
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nReport: {out/'summary.md'} "
          f"({len(real)} difetti reali + {len(marg)} marginalia)")


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
        "Per ogni voce: apri la PNG (pagina originale) e il markdown, poi giudica",
        "**due assi**: (a) CONTENUTO, (b) FLUSSO DI LETTURA (Golden Rule #1:",
        "colonne/righe intrecciate = difetto GRAVE anche se le parole ci sono).",
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
            f"- flow (Golden Rule #1): {r.get('flow_score', 1.0):.2f} "
            f"(soglia {FLOW_MIN}); transizioni colonna {r.get('flow_transitions', 0)}",
            f"- PNG pagina: `{png.name}`",
            f"- markdown: `{md_dst.name}`",
            "",
        ]
        r["arbitration"] = {"mode": "visual", "verdict": "pending (agente)"}
    (review / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"Review: {review/'index.md'} ({len(flagged)} pagine)")


# ── advisor VLM remoto (OpenRouter) ─────────────────────────────────────────
# 404 incluso: il router OpenRouter a volte risponde "model_not_found" in modo
# transitorio (osservato su un run reale), pur con il modello corretto.
_ADVISOR_RETRYABLE = {404, 429, 500, 502, 503, 504}


def _advisor_judge(png_bytes: bytes, md: str, model: str, key: str,
                   url: str = _ADVISOR_URL, timeout: float = 120.0,
                   retries: int = 4, base_delay: float = 5.0) -> dict:
    md_clean = _strip_base64_for_prompt(md)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _ADVISOR_SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": "Markdown estratto:\n\n" + md_clean[:200000]},
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
    # Retry con backoff sui limiti upstream (429) e sugli errori transitori:
    # il modello condiviso è spesso rate-limited e un run lungo perderebbe
    # meta' dei verdetti.
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            content = (data.get("choices") or [{}])[0].get(
                "message", {}).get("content", "")
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
            return {"raw": content}
        except urllib.error.HTTPError as e:
            if e.code in _ADVISOR_RETRYABLE and attempt < retries:
                wait = base_delay * (2 ** attempt)
                try:
                    ra = e.headers.get("Retry-After") if e.headers else None
                    if ra:
                        wait = max(wait, float(ra))
                except Exception:
                    pass
                print(f"    advisor: HTTP {e.code}, retry {attempt + 1}/{retries} "
                      f"tra {wait:.0f}s")
                time.sleep(wait)
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries:
                wait = base_delay * (2 ** attempt)
                print(f"    advisor: {e!r}, retry {attempt + 1}/{retries} "
                      f"tra {wait:.0f}s")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("advisor: retry esauriti")  # pragma: no cover


def _run_advisor(out: Path, records: list[dict], doc, model: str, key: str,
                 max_calls: int, retries: int = 4, delay: float = 0.0) -> None:
    import pymupdf

    review = out / "review"
    review.mkdir(parents=True, exist_ok=True)
    # L'advisor giudica TUTTE le pagine richieste (anche quelle non auto-flaggate):
    # il gate automatico può non vedere un difetto. `max_calls>0` è solo un tetto.
    targets = records[:max_calls] if max_calls else records
    for i, r in enumerate(targets):
        if i and delay:
            time.sleep(delay)  # gentile con il rate limit upstream
        idx = r["page_idx"]
        page = doc[idx]
        png = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
        md = (out / r["pipeline"] / f"page_{idx:04d}.md")
        md_text = md.read_text(encoding="utf-8") if md.exists() else ""
        t0 = time.perf_counter()
        try:
            verdict = _advisor_judge(png, md_text, model, key, retries=retries)
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
TOL_FLOW = 0.02     # calo massimo ammesso sul flow (Golden Rule #1)
TOL_BODY = 0.20     # calo massimo (frazione) sul body_len
TOL_TABLE = 0.05    # calo massimo ammesso sul recall delle tabelle


def _baseline_key(rec: dict) -> str:
    return f"{rec['pdf']}|{rec['pipeline']}|{rec['page_idx']}"


def _baseline_entry(rec: dict) -> dict:
    c = rec["checks"]
    return {
        "recall_pdf": c["text"]["recall_pdf"],
        "order_score": rec["order_score"],
        "flow_score": rec.get("flow_score", 1.0),
        "body_len": rec["body_len"],
        "figures": c["figures"]["embedded"],
        "table_recall": c["tables"]["recall"],
        "layout_class": rec.get("layout_class"),
    }


def _class_aggregate(entries: dict) -> dict:
    """Medie per **classe di layout** dalle voci di baseline (Fase 0.4)."""
    groups: dict[str, list[dict]] = {}
    for e in entries.values():
        cls = e.get("layout_class")
        if cls:
            groups.setdefault(cls, []).append(e)
    out: dict[str, dict] = {}
    for cls, rows in groups.items():
        def _mean(key: str):
            vals = [r[key] for r in rows if r.get(key) is not None]
            return round(sum(vals) / len(vals), 4) if vals else None

        out[cls] = {
            "n": len(rows),
            "recall_pdf": _mean("recall_pdf"),
            "order_score": _mean("order_score"),
            "flow_score": _mean("flow_score"),
            "body_len": _mean("body_len"),
            "table_recall": _mean("table_recall"),
        }
    return out


def _load_baseline_doc(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_baseline(path: Path) -> dict:
    return _load_baseline_doc(path).get("entries", {})


def _write_baseline(path: Path, records: list[dict]) -> None:
    # merge: più run (anche su PDF diversi) accumulano nello stesso baseline
    entries = _load_baseline(path)
    entries.update({_baseline_key(r): _baseline_entry(r) for r in records})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": _BASELINE_VERSION, "entries": entries,
                    "classes": _class_aggregate(entries)},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Baseline scritta: {path} ({len(entries)} voci, "
          f"{len(_class_aggregate(entries))} classi)")


def _compare_baseline(records: list[dict], entries: dict,
                      classes: dict | None = None) -> list[dict]:
    """Annota ogni record con le regressioni rispetto al baseline.

    Oltre al confronto per pagina, se ``classes`` è fornito confronta le **medie
    per classe di layout** (Fase 0.4): un fix che migliora la pagina bersaglio ma
    peggiora la classe viene segnalato.
    """
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
        if base.get("flow_score", 1.0) - cur.get("flow_score", 1.0) > TOL_FLOW:
            issues.append(
                f"flow {base.get('flow_score', 1.0):.3f}->{cur.get('flow_score', 1.0):.3f}")
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
    if classes:
        cur_classes = _class_aggregate(
            {_baseline_key(r): _baseline_entry(r) for r in records})
        for cls, cur in cur_classes.items():
            base = classes.get(cls)
            if not base:
                continue
            issues = []
            if (base.get("recall_pdf") is not None
                    and cur.get("recall_pdf") is not None
                    and base["recall_pdf"] - cur["recall_pdf"] > TOL_RECALL):
                issues.append(f"recall {base['recall_pdf']:.3f}->{cur['recall_pdf']:.3f}")
            if (base.get("order_score") is not None
                    and cur.get("order_score") is not None
                    and base["order_score"] - cur["order_score"] > TOL_ORDER):
                issues.append(f"order {base['order_score']:.3f}->{cur['order_score']:.3f}")
            if (base.get("flow_score") is not None
                    and cur.get("flow_score") is not None
                    and base["flow_score"] - cur["flow_score"] > TOL_FLOW):
                issues.append(
                    f"flow {base['flow_score']:.3f}->{cur['flow_score']:.3f}")
            if (base.get("table_recall") is not None
                    and cur.get("table_recall") is not None
                    and base["table_recall"] - cur["table_recall"] > TOL_TABLE):
                issues.append(
                    f"tabella {base['table_recall']:.2f}->{cur['table_recall']:.2f}")
            if issues:
                regs.append({"key": f"classe:{cls}", "issues": issues})
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
    ap.add_argument("--advisor-retries", type=int, default=4,
                    help="retry con backoff su 429/5xx dell'advisor")
    ap.add_argument("--advisor-delay", type=float, default=2.0,
                    help="pausa (s) tra le chiamate all'advisor")
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
                             args.advisor_max, retries=args.advisor_retries,
                             delay=args.advisor_delay)
    finally:
        doc.close()

    regs: list[dict] = []
    if args.update_baseline:
        _write_baseline(Path(args.update_baseline), records)
    if args.baseline:
        doc_bl = _load_baseline_doc(Path(args.baseline))
        entries = doc_bl.get("entries", {})
        regs = _compare_baseline(records, entries, doc_bl.get("classes"))
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
