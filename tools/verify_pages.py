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
    t = t.replace("\u00ad", "")            # soft hyphen (discrezionale) → rimosso
    # Apice/etichetta incollata al termine da PyMuPDF4LLM (`GenioglossusXII`,
    # `MasseterVIII`): separa il confine minuscola→MAIUSCOLA prima di minuscolizzare.
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t)
    t = t.lower()
    t = re.sub(r"-\s*\n\s*", "", t)        # trattino a fine riga → unisce
    # Trattino **composto** tra due lettere, con o senza spazi **orizzontali**:
    # unisce (`new - onset` ↔ `New-onset`, `long-term` ↔ `long- term`). Solo
    # spazi/tab (non newline): un trattino a inizio riga (es. un elenco "- voce")
    # non deve fondersi con l'ultima parola della riga precedente.
    t = re.sub(r"(?<=[a-z])[ \t]*-[ \t]*(?=[a-z])", "", t)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


#: Tolleranza bbox (pt) per riconoscere un blocco come **chrome** (testatina/
#: piè/numero): più ampia di quella delle figure (il blocco eccede il bbox).
_CHROME_BBOX_SLACK = 6.0


def _strip_images(t: str) -> str:
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    return re.sub(r"data:[^\s)]+", " ", t)


def _page_text_no_figures(page, elements: list[dict],
                          exclude_chrome: bool = True) -> str:
    """Testo della pagina escludendo le regioni `picture` (rese nell'immagine).

    Con ``exclude_chrome=True`` (default) esclude anche testatine/piè di
    pagina/numero pagina: chrome, non contenuto.
    """
    pics = [e["bbox"] for e in elements if e["class"] == "picture"]
    chrome: list[tuple] = []
    if exclude_chrome:
        chrome = [e["bbox"] for e in elements
                  if e["class"] in ("page-header", "page-footer", "page-number")]

    def _inside(b, rects, slack: float) -> bool:
        x0, y0, x1, y1 = b["bbox"]
        for px0, py0, px1, py1 in rects:
            if (x0 >= px0 - slack and x1 <= px1 + slack
                    and y0 >= py0 - slack and y1 <= py1 + slack):
                return True
        return False

    parts: list[str] = []
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type") != 0 or _inside(blk, pics, 2):
            continue
        if chrome and _inside(blk, chrome, _CHROME_BBOX_SLACK):
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
    """Order score robusto alla formattazione (penalità di interlacciamento).

    Ogni blocco di testo della content map è un *anchor* (prime parole); se ne
    trova la posizione nel md normalizzato. Guardando la **sequenza delle
    colonne** in ordine di posizione, un ordine colona-major ideale ha
    ``ncol-1`` transizioni; ogni transizione in più è interlacciamento.
    ``order = 1 - max(0, transizioni-(ncol-1)) / (n-1)``.
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
    M = _norm(md)
    seq: list[tuple[int, float]] = []
    for e in texty:
        mid = (e["bbox"][0] + e["bbox"][2]) / 2
        col = sum(1 for s in splits if mid > s)
        anchor = " ".join(_norm(_strip_images(e["text"])).split()[:6])
        if len(anchor) < 20:
            continue
        pos = M.find(anchor)
        if pos < 0:
            continue
        seq.append((col, pos))
    if len(seq) < 3:
        return 1.0
    seq.sort(key=lambda t: t[1])
    cols = [c for c, _ in seq]
    transitions = sum(1 for a, b in zip(cols, cols[1:]) if a != b)
    extra = max(0, transitions - (ncol - 1))
    return max(0.0, 1.0 - extra / (len(cols) - 1))


def _lis_length(seq: list[int]) -> int:
    """Lunghezza della più lunga sottosequenza strettamente crescente (O(n log n))."""
    import bisect

    tails: list[int] = []
    for x in seq:
        i = bisect.bisect_left(tails, x)
        if i == len(tails):
            tails.append(x)
        else:
            tails[i] = x
    return len(tails)


def _column_splits_robust(blocks: list[dict], page_width: float,
                          min_gap: float | None = None,
                          max_cross: int = 1) -> list[float]:
    """Splits di colonna robusti (gap di whitespace + tolleranza ai blocchi a ponte).

    Per ogni candidato confine ``s`` si contano i blocchi che lo **attraversano**
    e si misura il gutter (spazio vuoto reale): si sceglie il confine con **meno
    attraversamenti** e, a parità, il gutter più ampio, richiedendo blocchi su
    entrambi i lati. Il rilevatore del motore collassa le colonne quando un
    blocco (es. un titolo) attraversa il confine; qui viene tollerato.
    """
    if min_gap is None:
        min_gap = max(5.0, 0.01 * page_width)

    def _best_split(bs: list[dict]) -> float | None:
        xs = sorted({b["x0"] for b in bs} | {b["x1"] for b in bs})
        cand: list[tuple[int, float, float]] = []
        for i in range(len(xs) - 1):
            s = (xs[i] + xs[i + 1]) / 2
            left = [b for b in bs if b["x1"] <= s]
            right = [b for b in bs if b["x0"] >= s]
            if not left or not right:
                continue
            cross = sum(1 for b in bs if b["x0"] < s < b["x1"])
            gutter = (min(b["x0"] for b in right)
                      - max(b["x1"] for b in left))
            if gutter < min_gap:
                continue
            cand.append((cross, -gutter, s))
        if not cand:
            return None
        cross, _neg, s = min(cand)
        return s if cross <= max_cross else None

    out: list[float] = []

    def _rec(bs: list[dict], depth: int) -> None:
        if depth > 3 or len(bs) < 2:
            return
        s = _best_split(bs)
        if s is None:
            return
        left = [b for b in bs if (b["x0"] + b["x1"]) / 2 < s]
        right = [b for b in bs if (b["x0"] + b["x1"]) / 2 >= s]
        _rec(left, depth + 1)
        out.append(s)
        _rec(right, depth + 1)

    _rec(list(blocks), 0)
    return sorted(out)


def _flat_boxes(elements: list[dict]) -> list[dict]:
    return [{"x0": e["bbox"][0], "x1": e["bbox"][2],
             "y0": e["bbox"][1], "y1": e["bbox"][3]} for e in elements]


def _reference_units(elements: list[dict], page_width: float,
                     boxes: list[tuple] | None = None) -> list[dict]:
    """Unità di prosa in **ordine naturale di lettura** (riferimento geometrico).

    Indipendente dall'ordine emesso dal motore: bande delimitate dai blocchi a
    piena larghezza, poi ordine **colonna-major** (sinistra→destra,
    alto→basso) dentro ogni banda. I **box a tutta larghezza** (``boxes``, es.
    "Key Points") sono una banda a sé: la loro prosa è letta contigua e al posto
    giusto per y (altrimenti la prosa del box viene attribuita a una colonna di
    pagina → falso positivo d'ordine).
    """
    prose = [e for e in elements
             if e["class"] in ("text", "section-header", "title")]
    if not prose:
        return []
    import ir_layout

    boxes = sorted(boxes or [], key=lambda r: r[1])
    # separatori di banda: QUALSIASI blocco a piena larghezza (testo a tutta
    # pagina, tabelle o figure ampie) → un footnote/blocco sotto le colonne
    # finisce nella banda giusta invece di essere letto prima della colonna
    # destra.
    all_full = [e for e in elements
                if e.get("w", 0) >= 0.6 * page_width]
    body = [e for e in prose if e["w"] < 0.6 * page_width]

    # estrai la prosa dentro i box (banda a sé)
    box_prose: dict[int, list[dict]] = {}
    if boxes:
        free: list[dict] = []
        for e in body:
            bi = ir_layout._box_index(e, boxes)
            if bi is None:
                free.append(e)
            else:
                box_prose.setdefault(bi, []).append(e)
        body = free

    global_splits = _column_splits_robust(
        _flat_boxes(body), page_width) if body else []

    def _col(e: dict, splits: list[float]) -> int:
        mid = (e["bbox"][0] + e["bbox"][2]) / 2
        return sum(1 for s in splits if mid > s)

    seps = sorted(all_full, key=lambda e: e["bbox"][1])
    sep_y = [e["bbox"][1] for e in seps]
    bands: list[list[dict]] = [[] for _ in range(len(seps) + 1)]
    for e in body:
        band = sum(1 for y in sep_y if e["bbox"][1] >= y)
        bands[band].append(e)
    ordered: list[dict] = []
    for b in bands:
        regions = ir_layout._band_regions(b, page_width)
        if len(regions) <= 1:
            splits = (_column_splits_robust(
                [{"x0": e["bbox"][0], "x1": e["bbox"][2],
                  "y0": e["bbox"][1], "y1": e["bbox"][3]} for e in b],
                page_width) or global_splits)
            b.sort(key=lambda e: (_col(e, splits),
                                  e["bbox"][1], e["bbox"][0]))
            ordered.extend(b)
            continue
        for region in regions:
            splits = _column_splits_robust(
                [{"x0": e["bbox"][0], "x1": e["bbox"][2],
                  "y0": e["bbox"][1], "y1": e["bbox"][3]} for e in region],
                page_width,
            )
            ordered.extend(sorted(
                region, key=lambda e: (_col(e, splits),
                                       e["bbox"][1], e["bbox"][0])))

    # inserisci la prosa dei box al loro posto per y (banda contigua)
    if box_prose:
        box_orders: dict[int, list[dict]] = {}
        for bi, els in box_prose.items():
            splits = _column_splits_robust(_flat_boxes(els), page_width)
            box_orders[bi] = sorted(
                els, key=lambda e: (_col(e, splits),
                                    e["bbox"][1], e["bbox"][0]))
        segs: list[list[dict]] = [[] for _ in range(len(boxes) + 1)]
        for e in ordered:
            k = 0
            while k < len(boxes) and e["bbox"][1] >= boxes[k][1]:
                k += 1
            segs[k].append(e)
        merged: list[dict] = []
        for k in range(len(boxes)):
            merged.extend(segs[k])
            merged.extend(box_orders.get(k, []))
        merged.extend(segs[len(boxes)])
        ordered = merged

    return [{"text": e["text"], "bbox": e["bbox"], "cls": e["class"],
             "col": -1 if e["w"] >= 0.6 * page_width
             else _col(e, global_splits)}
            for e in ordered]


def _flow_score(md: str, elements: list[dict], page_width: float,
                min_units: int = 3, anchor_words: int = 6,
                boxes: list[tuple] | None = None) -> dict:
    """Fedeltà del **flusso di lettura** dell'md rispetto all'ordine naturale.

    Golden Rule #1 (vedi ``REGOLE-TEST.md``): unità = blocchi di prosa in
    ordine geometrico colonna-major. Per ogni unità si cercano **tutte** le
    occorrenze a parola intera delle sue prime parole nell'md, poi si assegna
    in modo **greedy crescente** (ogni unità prende la prima occorrenza dopo la
    precedente): il massimo numero di unità collocabili in ordine. Il punteggio
    è ``assegnate / unità_trovate``: ``1.0`` = flusso perfetto, basso =
    colonne/righe intrecciate.

    Restituisce ``{flow, n, found, lis, transitions, seq}``.
    """
    units = _reference_units(elements, page_width, boxes)
    n_units = len(units)
    if n_units < min_units:
        return {"flow": 1.0, "n": n_units, "found": 0, "lis": n_units,
                "transitions": 0, "seq": []}
    # Guard: il flow si applica solo a layout a 2+ colonne con **prosa reale**
    # (>=2 blocchi `text` per colonna). Evita i falsi positivi su pagine
    # figura/sidebar, dove i "lati" non sono colonne di prosa.
    col_text: dict[int, int] = {}
    for u in units:
        if u["col"] >= 0 and u["cls"] == "text":
            col_text[u["col"]] = col_text.get(u["col"], 0) + 1
    cols_present = {u["col"] for u in units if u["col"] >= 0}
    if len(cols_present) >= 2 and any(
            col_text.get(c, 0) < 2 for c in cols_present):
        return {"flow": 1.0, "n": n_units, "found": 0, "lis": n_units,
                "transitions": 0, "seq": []}
    M = " " + _norm(md) + " "
    occ_list: list[list[int]] = []
    for u in units:
        words = _norm(_strip_images(u["text"])).split()
        anchor = " ".join(words[:anchor_words])
        occ: list[int] = []
        if len(anchor) >= 12:
            pat = " " + anchor + " "
            start = 0
            while True:
                i = M.find(pat, start)
                if i < 0:
                    break
                occ.append(i)
                start = i + 1
        occ_list.append(occ)
    n_occ = sum(1 for o in occ_list if o)
    if n_occ < min_units:
        return {"flow": 1.0, "n": n_units, "found": n_occ,
                "lis": n_occ, "transitions": 0, "seq": []}
    last = -1
    assigned: list[tuple[int, int, int]] = []  # (rank, pos, col)
    for rank, occ in enumerate(occ_list):
        cand = [p for p in occ if p > last]
        if cand:
            p = min(cand)
            last = p
            assigned.append((rank, p, units[rank]["col"]))
    flow = len(assigned) / n_occ
    cols = [c for _, _, c in assigned if c >= 0]
    transitions = sum(1 for a, b in zip(cols, cols[1:]) if a != b)
    return {"flow": round(flow, 4), "n": n_units, "found": n_occ,
            "lis": len(assigned), "transitions": transitions,
            "seq": [(r, c, " ".join(units[r]["text"].split())[:40])
                    for r, _, c in assigned]}


def _page_lines_no_figures(page, elements: list[dict]) -> list[tuple]:
    """Righe di testo della pagina in ordine geometrico **naturale**, escluse le
    regioni che non sono prosa di flusso: figure, tabelle (valutate a parte),
    chrome (testatine/piè/numero) e tabelle ruotate.

    Restituisce ``[(bbox, testo), ...]`` in ordine colonna-major per banda.
    """
    import main

    excl = [e["bbox"] for e in elements if e.get("class") == "picture"]
    excl += [e["bbox"] for e in elements if e.get("class") == "table"]
    excl += [e["bbox"] for e in elements
             if e.get("class") in ("page-header", "page-footer", "page-number")]
    try:
        excl += [tuple(r) for r in main._rotated_table_rects(page, elements)]
    except Exception:
        pass

    def _inside(b) -> bool:
        x0, y0, x1, y1 = b
        for px0, py0, px1, py1 in excl:
            if x0 >= px0 - 2 and x1 <= px1 + 2 and y0 >= py0 - 2 and y1 <= py1 + 2:
                return True
        return False

    lines: list[tuple] = []
    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type") != 0 or _inside(blk["bbox"]):
            continue
        for ln in blk["lines"]:
            txt = "".join(s["text"] for s in ln["spans"])
            if txt.strip():
                lines.append((ln["bbox"], txt))
    return lines


def _order_report(md: str, elements: list[dict], page_width: float,
                  anchor_words: int = 6,
                  boxes: list[tuple] | None = None) -> dict:
    """**Ordine stringente** (Golden Rule #1 rafforzata) a livello di **blocco**.

    Unità = blocchi di prosa in ordine geometrico colonna-major. Per ogni unità
    si cerca la posizione nell'md:
    - ``inversions`` = coppie di unità con **ancora univoca** fuori ordine
      (0 = nessun intreccio);
    - ``unassigned`` = unità non collocabili in ordine crescente (greedy), come
      nel ``_flow_score``.

    Obiettivo: **inversioni = 0 e unassigned = 0**. Include
    ``content_recall``/``content_precision`` a parole (≥ 4).
    """
    units = _reference_units(elements, page_width, boxes)
    M = " " + _norm(_strip_images(md)) + " "
    occ_list: list[list[int]] = []
    for u in units:
        words = _norm(_strip_images(u["text"])).split()
        anchor = " ".join(words[:anchor_words])
        occ: list[int] = []
        if len(anchor) >= 12:
            s = 0
            while True:
                i = M.find(" " + anchor + " ", s)
                if i < 0:
                    break
                occ.append(i)
                s = i + 1
        occ_list.append(occ)

    unique = [(rank, occ[0]) for rank, occ in enumerate(occ_list) if len(occ) == 1]
    inv = sum(1 for a in range(len(unique))
              for b in range(a + 1, len(unique))
              if unique[a][1] > unique[b][1])

    last = -1
    assigned = 0
    n_occ = 0
    for occ in occ_list:
        if occ:
            n_occ += 1
        cand = [p for p in occ if p > last]
        if cand:
            last = min(cand)
            assigned += 1
    unassigned = n_occ - assigned

    ref_words: set[str] = set()
    for u in units:
        ref_words |= {w for w in _norm(_strip_images(u["text"])).split()
                      if len(w) >= 4}
    md_words = {w for w in _norm(_strip_images(md)).split() if len(w) >= 4}
    rec = 1.0 if not ref_words else len(ref_words & md_words) / len(ref_words)
    prec = 1.0 if not md_words else len(ref_words & md_words) / len(md_words)
    return {
        "inversions": inv, "unassigned": unassigned, "units": len(units),
        "unique": len(unique), "found": n_occ,
        "content_recall": round(rec, 4), "content_precision": round(prec, 4),
        "missing": len(ref_words - md_words), "extra": len(md_words - ref_words),
    }


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
        # pipeline IR INTEGRATA (content map + figure unite + cosmetica, 1 passata)
        md, _raw, _ok = main._apply_ir_on_page(pdf, idx, figures_dir=figures_dir)
        return md
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
                rec = {"pdf": Path(pdf).name, "page_idx": idx,
                       "page_ui": idx + 1, "pipeline": pipe,
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
