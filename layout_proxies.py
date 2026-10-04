#!/usr/bin/env python3
"""Proxy deterministici sul layout — **Fase 0.2** del piano GNN.

Funzioni **pure** sugli elementi della content map di PyMuPDF4LLM (i
``page_boxes``): dict con chiavi ``class``, ``bbox`` ``(x0, y0, x1, y1)``,
``w`` (larghezza), ``y0`` e ``text``.

Obiettivi:
- **predire** i verdetti dell'advisor (ordine / tabelle / figure / testo) senza
  rete e senza Qt → CI-safe;
- dare una **confidenza** per pagina e decidere l'**escalation** all'advisor
  (Fase 0.3);
- dare una **classe di layout** per il campionamento stratificato (Fase 0.1).

Ogni proxy restituisce un dict JSON-serializzabile::

    {"score": 0..1, "anomalies": [{"type": ..., ...}], "metrics": {...}}

``score == 1.0`` = nessuna anomalia rilevata. Le soglie sono **provvisorie** e
vanno **calibrate sul golden** (Fase 0.1): qui l'API è stabile, i numeri no.

Nessuna dipendenza da ``main``/Qt/pymupdf: il modulo è importabile a freddo.
"""

from __future__ import annotations

import re

#: classi di testo "da flusso" (esclusi table/picture/formula: non sono prosa)
TEXTY_CLASSES = frozenset(
    {"text", "section-header", "title", "list-item", "footnote", "caption"}
)
#: classi "pesanti" (float: tabelle, figure, formule)
FLOAT_CLASSES = frozenset({"table", "picture", "formula"})

#: soglia provvisoria di escalation: sotto questa confidenza la pagina è
#: "incerta" → da mandare all'advisor. Da calibrare sul golden (≥80% recall).
ESCALATION_THRESHOLD = 0.75

#: pesi provvisori delle anomalie d'ordine (calibrabili)
_ORDER_WEIGHTS = {
    "cross_column": 1.0,     # box di testo a cavallo di un confine di colonna
    "ref_interlace": 0.7,    # liste/riferimenti interlacciati tra colonne
    "float_in_column": 0.5,  # tabella/figura annidata nel flusso di una colonna
    "full_width_cut": 0.4,   # separatore full-width che taglia un blocco di testo
    "duplicate": 0.6,        # due box con stesso testo e bbox quasi identici
}

_MD_IMG_RE = re.compile(r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+")
#: voce d'indice: "termine, 123" oppure "termine, 123–125" (come il motore)
_INDEX_ENTRY_RE = re.compile(r",\s*\d{1,4}(?:[–\-]\d{1,4})?[a-z]?\b")
_PIPE_ROW_RE = re.compile(r"^\|.*\|$")


# ── helper geometrici ───────────────────────────────────────────────────────
def box(e: dict) -> tuple[float, float, float, float]:
    """bbox ``(x0, y0, x1, y1)`` di un elemento (tollerante a bbox corti)."""
    b = e.get("bbox") or (0, 0, 0, 0)
    x0, y0, x1, y1 = (list(b) + [0, 0, 0, 0])[:4]
    return float(x0), float(y0), float(x1), float(y1)


def width(e: dict) -> float:
    x0, _, x1, _ = box(e)
    return x1 - x0


def _overlap_y(a: dict, b: dict) -> float:
    _, ay0, _, ay1 = box(a)
    _, by0, _, by1 = box(b)
    return max(0.0, min(ay1, by1) - max(ay0, by0))


def _overlap_area(a: dict, b: dict) -> float:
    ax0, ay0, ax1, ay1 = box(a)
    bx0, by0, bx1, by1 = box(b)
    w = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    h = max(0.0, min(ay1, by1) - max(ay0, by0))
    return w * h


def _area(e: dict) -> float:
    x0, y0, x1, y1 = box(e)
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _norm_text(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").lower()).strip()


# ── colonne ─────────────────────────────────────────────────────────────────
def column_splits(
    elements: list[dict],
    page_width: float,
    classes: frozenset[str] = frozenset({"text", "section-header", "title"}),
) -> list[float]:
    """Confini x tra colonne (N-1 split per N colonne) dai blocchi di testo.

    Usa una **proiezione a copertura** robusta: un gutter è una fascia x coperta
    da **pochi** box; così un singolo box che "pontica" le colonne (float, titolo
    a tutta larghezza sfuggito) **non** cancella il confine — a differenza del
    merge degli intervalli usato dal motore. ``x-projection/gap`` è esattamente
    ciò che prescrive il piano per Fase 1.
    """
    spans = [(box(e)[0], box(e)[2]) for e in elements
             if e.get("class") in classes and box(e)[2] > box(e)[0]]
    if len(spans) < 4:
        return []
    W = int(page_width) + 1
    cov = [0] * W
    for x0, x1 in spans:
        a = max(0, int(x0))
        b = min(W, int(x1))
        for i in range(a, b):
            cov[i] += 1
    thr = max(1, int(0.25 * len(spans)))
    min_gap = max(8.0, 0.02 * page_width)
    splits: list[float] = []
    i = 0
    while i < W:
        if cov[i] <= thr:
            j = i
            while j < W and cov[j] <= thr:
                j += 1
            if j - i >= min_gap:
                mid = (i + j) / 2
                # scarta i gutter nei margini esterni (non separano colonne)
                if 0.05 * page_width < mid < 0.95 * page_width:
                    splits.append(mid)
            i = j
        else:
            i += 1
    return splits


def column_of(e: dict, splits: list[float]) -> int:
    """Indice di colonna di un elemento (dal centro), dato l'elenco di split."""
    x0, _, x1, _ = box(e)
    mid = (x0 + x1) / 2
    return sum(1 for s in splits if mid > s)


def _straddles(e: dict, splits: list[float]) -> bool:
    x0, _, x1, _ = box(e)
    return any(x0 < s < x1 for s in splits)


# ── proxy: ordine ───────────────────────────────────────────────────────────
def order_proxy(elements: list[dict], page_width: float) -> dict:
    """Anomalie geometriche che predicono i difetti d'**ordine di lettura**.

    Rileva: box di testo a cavallo di colonne, liste/riferimenti interlacciati,
    float (tabella/figura) annidati nel flusso di una colonna, separatori
    full-width che tagliano un blocco, duplicati geometrici.
    """
    texty = [e for e in elements if e.get("class") in TEXTY_CLASSES]
    floats = [e for e in elements if e.get("class") in FLOAT_CLASSES]
    full = [e for e in elements if width(e) >= 0.6 * page_width]
    splits = column_splits(elements, page_width)

    anomalies: list[dict] = []

    # 1. box di testo a cavallo di un confine di colonna
    for e in texty:
        if width(e) < 0.6 * page_width and _straddles(e, splits):
            anomalies.append({"type": "cross_column", "bbox": list(box(e))})

    # 2. liste/riferimenti interlacciati tra colonne (liste in colonne diverse
    #    con forte sovrapposizione verticale)
    lists = [e for e in texty if e.get("class") == "list-item"]
    for i, a in enumerate(lists):
        for b in lists[i + 1:]:
            if column_of(a, splits) == column_of(b, splits):
                continue
            ha = max(1.0, box(a)[3] - box(a)[1])
            if _overlap_y(a, b) >= 0.5 * ha:
                anomalies.append({"type": "ref_interlace",
                                  "bbox": list(box(a)),
                                  "bbox2": list(box(b))})

    # 3. float (tabella/figura) annidato nel flusso verticale di un testo in
    #    colonna (il testo ci scorre attorno → rischio ordine)
    for f in floats:
        fx0, fy0, fx1, fy1 = box(f)
        fcol = column_of(f, splits)
        for t in texty:
            tx0, ty0, tx1, ty1 = box(t)
            if column_of(t, splits) != fcol:
                continue
            if ty0 <= fy0 and fy1 <= ty1 and min(tx1, fx1) - max(tx0, fx0) > 0:
                anomalies.append({"type": "float_in_column",
                                  "bbox": list(box(f))})
                break

    # 4. separatore full-width che "taglia" un blocco di testo (y0 interno)
    for f in full:
        _, fy0, _, _ = box(f)
        for t in texty:
            tx0, ty0, tx1, ty1 = box(t)
            if ty0 < fy0 < ty1 and min(tx1, box(f)[2]) - max(tx0, box(f)[0]) > 0:
                anomalies.append({"type": "full_width_cut",
                                  "bbox": list(box(f))})
                break

    # 5. duplicati (stesso testo, bbox quasi identici)
    for i, a in enumerate(texty):
        for b in texty[i + 1:]:
            ta, tb = _norm_text(a.get("text", "")), _norm_text(b.get("text", ""))
            if not ta or ta != tb:
                continue
            if _overlap_area(a, b) >= 0.8 * min(_area(a) or 1, _area(b) or 1):
                anomalies.append({"type": "duplicate",
                                  "bbox": list(box(a))})

    penalty = sum(_ORDER_WEIGHTS.get(x["type"], 0.5)
                  for x in anomalies) / max(1, len(texty))
    score = max(0.0, min(1.0, 1.0 - penalty))
    return {
        "score": round(score, 4),
        "anomalies": anomalies,
        "metrics": {
            "n_texty": len(texty), "n_floats": len(floats),
            "n_full": len(full), "n_columns": len(splits) + 1,
        },
    }


# ── proxy: tabelle ──────────────────────────────────────────────────────────
def _md_table_stats(text: str) -> dict:
    rows = cols = cells = empty = 0
    has_sep = False
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not _PIPE_ROW_RE.match(s):
            continue
        cs = s.strip("|").split("|")
        if all(set(c.strip()) <= set("-: ") for c in cs):
            has_sep = True
            continue
        rows += 1
        cols = max(cols, len(cs))
        cells += len(cs)
        empty += sum(1 for c in cs if not c.strip())
    return {"rows": rows, "cols": cols, "cells": cells,
            "empty": empty, "has_sep": has_sep,
            "empty_ratio": (empty / cells) if cells else 0.0}


def table_proxy(elements: list[dict], page=None) -> dict:
    """Qualità delle **tabelle** dalla content map (e dalla griglia GNN, se ``page``).

    Anomalie: tabella con **griglia degenere** (<2 righe o <2 colonne), molte
    celle vuote, tabella senza markdown. Se ``page`` ha ``layout_information``
    con ``table_grid`` si registrano anche ``h_lines``/``v_lines`` del GNN e si
    segnala la griglia **presente ma degenere** (caso `ha22 p230`, `ce24 p2480`).
    """
    tables = [e for e in elements if e.get("class") == "table"]
    anomalies: list[dict] = []
    per: list[dict] = []
    for e in tables:
        st = _md_table_stats(e.get("text", ""))
        per.append(st)
        if not st["rows"] and not st["cols"]:
            anomalies.append({"type": "table_no_markdown", "bbox": list(box(e))})
        elif st["cols"] < 2 or st["rows"] < 2:
            anomalies.append({"type": "table_degenerate", "bbox": list(box(e)),
                              "rows": st["rows"], "cols": st["cols"]})
        elif st["empty_ratio"] > 0.5:
            anomalies.append({"type": "table_empty_cells", "bbox": list(box(e)),
                              "empty_ratio": round(st["empty_ratio"], 2)})

    grids: list[dict] = []
    if page is not None:
        try:
            info = getattr(page, "layout_information", None)
            for el in (info or []):
                g = el.get("table_grid")
                if not g:
                    continue
                h = g.get("h_lines") if isinstance(g, dict) else None
                v = g.get("v_lines") if isinstance(g, dict) else None
                nh = len(h) if h is not None else 0
                nv = len(v) if v is not None else 0
                grids.append({"h_lines": nh, "v_lines": nv})
                if nh == 0 or nv <= 1:
                    anomalies.append({"type": "table_grid_degenerate",
                                      "h_lines": nh, "v_lines": nv})
        except Exception:  # pragma: no cover - layout_information opzionale
            grids = []

    n = len(tables)
    score = 1.0 if not n else max(0.0, 1.0 - len(anomalies) / n)
    return {
        "score": round(score, 4),
        "anomalies": anomalies,
        "metrics": {"n_tables": n, "grids": grids, "tables": per},
    }


# ── proxy: figure ───────────────────────────────────────────────────────────
def figure_proxy(elements: list[dict], md: str | None = None) -> dict:
    """Presenza/duplicazione/bleed delle **figure** rispetto al markdown.

    ``expected`` = box ``picture`` della content map; ``embedded`` = immagini
    base64 nel md (se fornito). Anomalie: figure attese ma non embeddate,
    duplicati geometrici, testo che si sovrappone a una figura (bleed).
    """
    pics = [e for e in elements if e.get("class") == "picture"]
    texty = [e for e in elements if e.get("class") in TEXTY_CLASSES]
    anomalies: list[dict] = []

    for i, a in enumerate(pics):
        for b in pics[i + 1:]:
            if _overlap_area(a, b) >= 0.9 * min(_area(a) or 1, _area(b) or 1):
                anomalies.append({"type": "figure_duplicate",
                                  "bbox": list(box(a))})
    for t in texty:
        for p in pics:
            if _overlap_area(t, p) >= 0.3 * max(1.0, _area(t)):
                anomalies.append({"type": "figure_text_bleed",
                                  "bbox": list(box(t))})
                break

    embedded = len(_MD_IMG_RE.findall(md)) if md is not None else None
    if md is not None and pics and embedded == 0:
        anomalies.append({"type": "figure_missing", "expected": len(pics)})

    n = len(pics)
    score = 1.0
    if anomalies:
        denom = max(1, n if n else len(texty))
        score = max(0.0, 1.0 - len(anomalies) / denom)
    return {
        "score": round(score, 4),
        "anomalies": anomalies,
        "metrics": {"pictures": n, "embedded": embedded},
    }


# ── proxy: testo ────────────────────────────────────────────────────────────
def text_proxy(elements: list[dict], page_width: float = 0.0) -> dict:
    """Frammentazione/vuoti del **testo** (completezza misurata altrove dal gate)."""
    texty = [e for e in elements if e.get("class") in TEXTY_CLASSES]
    empty = [e for e in texty if not _norm_text(e.get("text", ""))]
    frag = [e for e in texty
            if 0 < len(_norm_text(e.get("text", ""))) < 15]
    full = ([e for e in texty if width(e) >= 0.6 * page_width]
            if page_width else [])
    anomalies: list[dict] = []
    anomalies += [{"type": "text_empty", "bbox": list(box(e))} for e in empty]
    anomalies += [{"type": "text_fragmented", "bbox": list(box(e))} for e in frag]
    n = len(texty)
    score = 1.0 if not n else max(
        0.0, 1.0 - (len(empty) + 0.3 * len(frag)) / n)
    return {
        "score": round(score, 4),
        "anomalies": anomalies,
        "metrics": {"n_texty": n, "n_empty": len(empty),
                    "n_fragmented": len(frag), "n_full": len(full)},
    }


# ── classe di layout (per campionamento stratificato) ───────────────────────
def _looks_like_index(elements: list[dict]) -> bool:
    """True se la pagina è un **indice** (molte voci "termine, numero").

    Tarato sui ``page_boxes`` (pochi blocchi, non le molte righe del testo
    grezzo): basta una forte densità di voci, non un numero assoluto di righe.
    """
    lines: list[str] = []
    for e in elements:
        if e.get("class") in TEXTY_CLASSES:
            lines.extend(ln for ln in (e.get("text") or "").splitlines() if ln.strip())
    if len(lines) < 5:
        return False
    hits = sum(1 for ln in lines if _INDEX_ENTRY_RE.search(ln))
    return hits >= max(4, 0.4 * len(lines))


def layout_class(elements: list[dict], page_width: float) -> str:
    """Classe di layout della pagina (una delle 6 del piano).

    Priorità: ``indici`` > ``tabelle`` > ``equazioni`` > ``figure`` >
    ``colonne/box-liste`` > ``prosa``.
    """
    classes = {e.get("class") for e in elements}
    if _looks_like_index(elements):
        return "indici"
    if "table" in classes:
        return "tabelle"
    if "formula" in classes:
        return "equazioni"
    if "picture" in classes:
        return "figure"
    splits = column_splits(elements, page_width)
    if splits or "list-item" in classes:
        return "colonne/box-liste"
    return "prosa"


# ── confidenza + escalation (Fase 0.3) ──────────────────────────────────────
def confidence(proxies: dict, gate_ok: bool = True,
               gate_reason: str = "") -> float:
    """Confidenza 0..1 della pagina, da gate + proxy (soglie **provvisorie**).

    Il gate deterministico è un segnale forte: se fallisce, la pagina è incerta.
    Tra i proxy vale il **peggiore** (una pagina con tabelle rotte resta incerta
    anche se l'ordine è pulito). Da **calibrare** sul golden prima dell'uso a
    runtime.
    """
    scores = [
        float((proxies.get(k) or {}).get("score", 1.0))
        for k in ("order", "table", "figure", "text")
    ]
    worst = min(scores) if scores else 1.0
    gate_conf = 1.0 if (gate_ok or gate_reason == "solo-figure") else 0.0
    return round(0.4 * gate_conf + 0.6 * worst, 4)


def all_proxies(elements: list[dict], page_width: float,
                md: str | None = None, gate_ok: bool = True,
                gate_reason: str = "") -> dict:
    """Tutti i proxy + classe di layout + confidenza, in un unico dict."""
    out = {
        "order": order_proxy(elements, page_width),
        "table": table_proxy(elements),
        "figure": figure_proxy(elements, md),
        "text": text_proxy(elements, page_width),
        "layout_class": layout_class(elements, page_width),
    }
    out["confidence"] = confidence(out, gate_ok, gate_reason)
    out["escalate"] = out["confidence"] < ESCALATION_THRESHOLD
    return out
