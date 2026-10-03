#!/usr/bin/env python3
"""Pipeline IR — ricostruisce il markdown dalla **content map** di PyMuPDF4LLM.

Invece di "indovinare" le colonne con soglie sul testo, si usa la mappa tipizzata
del modello di layout di PyMuPDF (``page_boxes``): ogni elemento ha
``class`` (text / picture / caption / section-header / page-header / …), ``bbox``
e il proprio testo (via ``pos`` = offset nel testo di pagina).

Qui **noi** decidiamo l'**ordine di lettura**:
- si scartano i chrome (page-header/footer) e le etichette di margine;
- gli elementi a tutta larghezza fanno da **separatori di banda**;
- i testi vengono assegnati alle **colonne** e emessi banda per banda,
  colonna per colonna, top→bottom;
- le **didascalie** si ancorano alla figura sopra (stessa colonna);
- le **figure** vengono rese come JPEG embedded (riuso di ``main``).

Obiettivo: dare tutto (testo/figure/tabelle) una volta, nell'ordine di lettura.
"""

from __future__ import annotations

import re

#: classi di "chrome" da non emettere come contenuto
_DROP_CLASSES = {"page-header", "page-footer", "page-number"}
#: classi che sono testo (inclusi heading e caselle/tabelle semplici)
_TEXTY = {"text", "section-header", "title", "list-item", "footnote",
          "formula", "table", "caption"}


def page_chunk(doc, page_index: int) -> dict:
    """Unico ``to_markdown(page_chunks=True)`` per pagina (una sola passata)."""
    import pymupdf4llm

    return pymupdf4llm.to_markdown(doc, pages=[page_index], page_chunks=True)[0]


def _elements_from_chunk(chunk: dict) -> tuple[str, list[dict]]:
    text = chunk.get("text", "") or ""
    els: list[dict] = []
    for b in chunk.get("page_boxes", []) or []:
        a, z = b.get("pos", (0, 0))
        bb = tuple(b.get("bbox", (0, 0, 0, 0)))
        els.append({
            "class": b.get("class", "text"),
            "bbox": bb,
            "w": bb[2] - bb[0],
            "y0": bb[1],
            "text": text[a:z],
        })
    return text, els


def page_elements(doc, page_index: int) -> tuple[str, list[dict]]:
    """(testo_pagina, elementi) dalla content map di PyMuPDF4LLM."""
    return _elements_from_chunk(page_chunk(doc, page_index))


def _order(elements: list[dict], page_width: float) -> list[list[dict]]:
    """Restituisce gli elementi divisi in bande (lista di liste), in ordine.

    Una banda è delimitata dagli elementi a **tutta larghezza**; dentro la banda
    l'ordine è: colonna per colonna (sinistra→destra), top→bottom. Gli elementi
    a tutta larghezza sono separatori (banda a sé, emessi al loro posto).
    """
    import main  # lazy: main importa layout_engine

    full = [e for e in elements if e["w"] >= 0.6 * page_width]
    body = [e for e in elements if e["w"] < 0.6 * page_width]

    # confini colonna dai soli blocchi di TESTO (le figure non devono "pontare")
    texty = [e for e in body if e["class"] in ("text", "section-header", "title")]
    splits = main._detect_column_splits(
        [{"x0": e["bbox"][0], "x1": e["bbox"][2],
          "y0": e["bbox"][1], "y1": e["bbox"][3]} for e in texty],
        page_width,
    )

    def col(e: dict) -> int:
        mid = (e["bbox"][0] + e["bbox"][2]) / 2
        return sum(1 for s in splits if mid > s)

    ncol = len(splits) + 1

    # bande: indice = numero di separatori con y0 <= elemento.y0
    seps = sorted(full, key=lambda e: e["y0"])
    sep_y = [e["y0"] for e in seps]

    bands: list[list[dict]] = [[] for _ in range(len(seps) + 1)]
    for e in body:
        band = sum(1 for y in sep_y if e["y0"] >= y)
        bands[band].append(e)
    for b in bands:
        b.sort(key=lambda e: (col(e), e["y0"], e["bbox"][0]))
    return bands, seps, ncol


def _attach_captions(elements: list[dict]) -> dict[int, list[dict]]:
    """Mappa picture→[didascalie] (didascalia subito sotto, stessa colonna)."""
    pics = [e for e in elements if e["class"] == "picture"]
    caps = [e for e in elements if e["class"] == "caption"]
    attached: dict[int, list[dict]] = {id(p): [] for p in pics}
    used = set()
    for c in caps:
        cx0, cy0, cx1, _ = c["bbox"]
        best, best_dy = None, 1e9
        for p in pics:
            px0, py0, px1, py1 = p["bbox"]
            if py1 <= cy0 + 2 and cy0 - py1 <= 120 and min(px1, cx1) - max(px0, cx0) > 0:
                dy = cy0 - py1
                if dy < best_dy:
                    best, best_dy = p, dy
        if best is not None:
            attached[id(best)].append(c)
            used.add(id(c))
    # la didascalia resta "usata" (emessa con la figura), non nel flusso testo
    return attached, used


def build_markdown(page, doc, page_index: int, figures_dir=None,
                   embed_figures: bool = True, return_meta: bool = False,
                   chunk: dict | None = None):
    """Markdown della pagina ricostruito dalla content map (ordine di lettura).

    Con ``return_meta=True`` restituisce ``(markdown, meta)`` dove ``meta`` è
    ``{"captions": set, "rects": list}``: le didascalie (normalizzate) già emesse
    e i **rect delle picture emesse**. Servono a non duplicare le figure quando
    si unisce la rilevazione di ``main`` (``skip_captions`` / ``skip_rects``).
    Se ``chunk`` è fornito (da ``page_chunk``), NON si ri-esegue ``to_markdown``
    (una sola passata di layout).
    """
    import main  # lazy

    if chunk is None:
        text, els = page_elements(doc, page_index)
    else:
        text, els = _elements_from_chunk(chunk)
    pw = page.rect.width

    # scarta chrome, etichette di margine e `picture` **spurie** (decorativi)
    ph = page.rect.height
    keep: list[dict] = []
    for e in els:
        c = e["class"]
        x0, y0, x1, y1 = e["bbox"]
        if c in _DROP_CLASSES:
            continue
        if c == "picture":
            w, h = x1 - x0, y1 - y0
            if w < 40 and (x0 < 30 or x1 > pw - 30):
                continue  # etichetta verticale di margine
            if w * h < 2500:
                continue  # decorativo minuscolo (es. 7x7)
            if y1 <= 0.08 * ph and h < 60:
                continue  # banner decorativo in testa (es. 82x49)
        keep.append(e)

    attached, used = _attach_captions(keep)
    bands, seps, ncol = _order(keep, pw)

    out: list[str] = []
    emitted_rects: list[tuple] = []  # rect delle picture effettivamente emesse

    def emit(e: dict) -> None:
        c = e["class"]
        seg = (e["text"] or "").strip()
        if c == "picture":
            rect = e["bbox"]
            if embed_figures and figures_dir is not None:
                try:
                    jpg = main._figure_jpeg(page, rect, "")
                except Exception:
                    jpg = None
                if jpg:
                    import base64
                    uri = "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii")
                    out.append(f"![figura]({uri})")
                    emitted_rects.append(rect)
            for cap in attached.get(id(e), []):
                ct = (cap["text"] or "").strip()
                if ct:
                    out.append(ct)
            return
        if c == "caption" and id(e) in used:
            return  # già emessa con la figura
        if seg:
            out.append(seg)

    for i, band in enumerate(bands):
        for e in band:
            emit(e)
        if i < len(seps):
            emit(seps[i])
    md = "\n\n".join(out)
    if return_meta:
        served = {
            main._norm_text(cap["text"])[:30]
            for caps in attached.values() for cap in caps
        }
        return md, {"captions": served, "rects": emitted_rects}
    return md
