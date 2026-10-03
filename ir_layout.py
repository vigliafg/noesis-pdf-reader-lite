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


def _internal_lines(pic_text: str) -> list[str]:
    """Righe del testo interno di una figura (dalla content map, separatore `<br>`)."""
    raw = re.sub(r"<!--.*?-->", "", pic_text or "")
    parts = re.split(r"<br\s*/?>|\n", raw)
    return [re.sub(r"\s+", " ", p).strip() for p in parts if p.strip()]


def _strip_figure_bleed(seg: str, lines: list[str]) -> str:
    """Toglie dal testo le etichette-figura **intrecciate** (bleed), con cautela.

    pymupdf4llm intreccia il testo interno delle figure nel paragrafo. Tre
    livelli, dal più sicuro:
    1. etichetta **incollata** a una parola (preceduta da lettera minuscola):
       ``ther60 apies`` → ``therapies``;
    2. etichetta **multi-parola** distintiva (``17p deletion``, ``No. AT Risk``);
    3. numero **isolato tra spazi** (assi): ``trisomy 80 12`` → ``trisomy 12``.
    Non tocca i numeri seguiti da unità (``60%``) né le parole intere; se non
    cambia nulla restituisce il testo originale.
    """
    orig = seg
    ordered = sorted(lines, key=len, reverse=True)
    # token numerici interni (anche dentro righe lunghe, es. asse dei mesi o
    # tabella No. AT Risk): rimossi **solo se incollati e seguiti da spazio**,
    # mai i numeri isolati né quelli dentro un locus (es. "q14.3").
    nums = sorted({tok for l in lines for tok in re.findall(r"\d+", l)},
                  key=len, reverse=True)
    for _ in range(4):  # più passate: una rimozione può sbloccarne un'altra
        before = seg
        for l in ordered:
            if l.isdigit():
                # numero isolato tra spazi: "trisomy 80 12" -> "trisomy 12"
                seg = re.sub(r"(?<=\s)" + re.escape(l) + r" (?=\S)", "", seg)
                continue
            esc = re.escape(l)
            # incollato a parola — **spazio obbligatorio** dopo: non tocca "q14.3"
            seg = re.sub(r"(?<=[a-z])" + esc + r"\s", "", seg)
            if " " in l and len(l) >= 6:
                seg = re.sub(r"(?<![\w])" + esc + r"\s?", "", seg)
        for tok in nums:
            seg = re.sub(r"(?<=[a-z])" + re.escape(tok) + r"\s", "", seg)
        if seg == before:
            break
    if seg == orig:
        return orig
    return re.sub(r"[ \t]{2,}", " ", seg)


def _has_bleed(seg: str, lines: list[str]) -> bool:
    """True se ``seg`` contiene bleed di figure (numero incollato o riga interna)."""
    if re.search(r"[a-z]{3,}\d", seg):
        return True
    return any(" " in l and len(l) >= 6 and l in seg for l in lines)


def _norm_words(t: str) -> set[str]:
    """Parole lunghe (≥5) normalizzate, con de-sillabazione (per il gate di sicurezza)."""
    t = re.sub(r"-\s*\n\s*", "", t.lower())
    t = re.sub(r"-\s+", "", t)
    return {w for w in re.sub(r"[^a-z0-9]+", " ", t).split() if len(w) >= 5}


def _rebuild_from_words(page, bbox: tuple, excl: list[tuple], pad: float = 6.0) -> str:
    """Ricostruisce il testo di ``bbox`` dalle parole, escludendo ``excl`` (figure/didascalie).

    Il ``pad`` compensa il fatto che i bbox della content map non coprono
    esattamente le parole dello slice.
    """
    eb = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)

    def _inside(b, r):
        return (b[0] >= r[0] and b[2] <= r[2] and b[1] >= r[1] and b[3] <= r[3])

    def _center_in(b, r):
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        return r[0] <= cx <= r[2] and r[1] <= cy <= r[3]

    # esclusione **center-based**: cattura anche le etichette a ridosso del bbox
    # della figura (es. "100" appena sopra il grafico) senza toccare il corpo.
    words = [w for w in page.get_text("words")
             if _inside(w[:4], eb) and not any(_center_in(w[:4], r) for r in excl)]
    words.sort(key=lambda w: (round(w[1] / 3), w[0]))
    lines: list[list[str]] = []
    cur: list[str] = []
    ly = None
    for w in words:
        if ly is not None and abs(w[1] - ly) > 5:
            lines.append(cur)
            cur = []
        cur.append(w[4])
        ly = w[1]
    if cur:
        lines.append(cur)
    # de-sillabazione: "suppres-" + "sor" -> "suppressor"
    out: list[str] = []
    for ln in (" ".join(l) for l in lines):
        if out and out[-1].endswith("-") and ln[:1].islower():
            out[-1] = out[-1][:-1] + ln
        else:
            out.append(ln)
    return "\n".join(out)


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


def _table_is_single_col(text: str) -> bool:
    """True se il markdown-tabella della content map ha una sola colonna."""
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = s.strip("|").split("|")
            if all(set(c.strip()) <= set("-: ") for c in cells):
                continue  # riga separatore
            if len(cells) > 1 and any(c.strip() for c in cells):
                return False
    return True


def _single_col_table_from_page(page, bbox: tuple) -> str:
    """Ricostruisce una tabella a **1 colonna** dalle righe di pagina.

    pymupdf4llm a volte fonde più voci in una cella; le righe reali della pagina
    no. Si usa la geometria delle parole entro il bbox della tabella.
    """
    def _inside(b, r, pad=3.0):
        return (b[0] >= r[0] - pad and b[2] <= r[2] + pad
                and b[1] >= r[1] - pad and b[3] <= r[3] + pad)

    words = [w for w in page.get_text("words") if _inside(w[:4], bbox)]
    words.sort(key=lambda w: (round(w[1] / 3), w[0]))
    rows: list[list[str]] = []
    cur: list[str] = []
    ly = None
    for w in words:
        if ly is not None and abs(w[1] - ly) > 5:
            rows.append(cur)
            cur = []
        cur.append(w[4])
        ly = w[1]
    if cur:
        rows.append(cur)
    return "\n".join(" ".join(r) for r in rows if r)


def _normalize_md_table(text: str) -> str:
    """Rende valido il markdown-tabella: separatore con il n. di colonne giusto."""
    rows = [ln.rstrip() for ln in (text or "").splitlines() if ln.strip()]
    if not rows:
        return text
    ncol = 0
    parsed: list[list[str]] = []
    for ln in rows:
        s = ln.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = s.strip("|").split("|")
            if all(set(c.strip()) <= set("-: ") for c in cells):
                parsed.append([])  # separatore (posizione preservata)
                continue
            ncol = max(ncol, len(cells))
            parsed.append(cells)
        else:
            parsed.append(None)  # riga non-tabella: lascia com'è
    if ncol < 2:
        return text
    out: list[str] = []
    sep_done = False
    for cells in parsed:
        if cells is None:
            out.append("")
        elif cells == []:
            if not sep_done:
                out.append("|" + "|".join(["---"] * ncol) + "|")
                sep_done = True
        else:
            cells = cells + [""] * (ncol - len(cells))
            out.append("| " + " | ".join(c.strip() for c in cells) + " |")
    if not sep_done and len(out) >= 2:
        out.insert(1, "|" + "|".join(["---"] * ncol) + "|")
    return "\n".join(out)


def _column_splits(elements: list[dict], page_width: float) -> list[float]:
    """Confini di colonna dai soli blocchi di TESTO (le figure non 'pontano')."""
    import main  # lazy: main importa layout_engine

    texty = [e for e in elements
             if e["class"] in ("text", "section-header", "title")]
    return main._detect_column_splits(
        [{"x0": e["bbox"][0], "x1": e["bbox"][2],
          "y0": e["bbox"][1], "y1": e["bbox"][3]} for e in texty],
        page_width,
    )


def _group_figure_blocks(elements: list[dict], splits: list[float],
                         page_width: float, gap: float = 40.0) -> list[dict]:
    """Raggruppa le `picture` in blocchi per vicinanza verticale.

    Un blocco che **attraversa le colonne** (o è molto largo) va trattato come
    figura a tutta larghezza: i pannelli di una stessa figura restano insieme.
    """
    pics = sorted((e for e in elements if e["class"] == "picture"),
                  key=lambda e: e["bbox"][1])
    clusters: list[dict] = []
    for p in pics:
        y0, y1 = p["bbox"][1], p["bbox"][3]
        for c in clusters:
            if y0 <= c["y1"] + gap and y1 >= c["y0"] - gap:
                c["pics"].append(p)
                c["y0"] = min(c["y0"], y0)
                c["y1"] = max(c["y1"], y1)
                break
        else:
            clusters.append({"pics": [p], "y0": y0, "y1": y1})
    for c in clusters:
        c["x0"] = min(p["bbox"][0] for p in c["pics"])
        c["x1"] = max(p["bbox"][2] for p in c["pics"])
        c["spanning"] = (
            (c["x1"] - c["x0"]) >= 0.6 * page_width
            or any(c["x0"] < s < c["x1"] for s in splits)
        )
    return clusters


def _multi_header_table(page, headers: list[dict], all_elements: list[dict]):
    """Tabella a più colonne con **intestazioni separate** (es. Medical|Lifestyle).

    Ritorna ``(testo, region, member_ids)``: ricostruisce per colonna (sx→dx) le
    righe sotto ciascuna intestazione, così le due liste non si interlacciano.
    """
    hs = sorted(headers, key=lambda e: e["bbox"][0])
    y0 = min(h["bbox"][1] for h in hs)
    x0 = min(h["bbox"][0] for h in hs) - 5
    x1 = max(h["bbox"][2] for h in hs)
    y = max(h["bbox"][3] for h in hs)
    members = list(hs)
    items = sorted(
        (e for e in all_elements
         if e["class"] in ("list-item", "text", "table")
         and id(e) not in {id(h) for h in hs}
         and e["bbox"][1] >= y0 - 5 and e["bbox"][0] >= x0 - 10),
        key=lambda e: e["bbox"][1])
    for e in items:
        if e["bbox"][1] - y > 40:
            break
        members.append(e)
        y = max(y, e["bbox"][3])
        x1 = max(x1, e["bbox"][2])
    region = (x0, y0, x1 + 5, y + 3)

    def _inside(b, r, pad=3.0):
        return (b[0] >= r[0] - pad and b[2] <= r[2] + pad
                and b[1] >= r[1] - pad and b[3] <= r[3] + pad)

    words = [w for w in page.get_text("words") if _inside(w[:4], region)]
    # confine di colonna: tra la fine reale della colonna sx (max x1 delle parole
    # che iniziano prima dell'intestazione dx) e l'inizio dell'intestazione dx
    bounds: list[float] = []
    for a, b in zip(hs, hs[1:]):
        left_x1 = max([a["bbox"][2]] + [w[2] for w in words if w[0] < b["bbox"][0]])
        bounds.append((left_x1 + b["bbox"][0]) / 2)
    cols: list[list] = [[] for _ in hs]
    for w in words:
        ci = sum(1 for b in bounds if w[0] >= b)
        cols[min(ci, len(hs) - 1)].append(w)
    blocks: list[str] = []
    for cw in cols:
        cw.sort(key=lambda w: (round(w[1] / 3), w[0]))
        rows: list[list[str]] = []
        cur: list[str] = []
        ly = None
        for w in cw:
            if ly is not None and abs(w[1] - ly) > 5:
                rows.append(cur)
                cur = []
            cur.append(w[4])
            ly = w[1]
        if cur:
            rows.append(cur)
        blocks.append("\n".join(" ".join(r) for r in rows))
    return "\n\n".join(b for b in blocks if b), region, {id(e) for e in members}


def _is_figure_caption(text: str) -> bool:
    """True se la didascalia inizia con 'fig' (è la didascalia della figura)."""
    return bool(re.match(r"(?i)^\W*fig", (text or "").strip()))


def _order(elements: list[dict], page_width: float) -> list[list[dict]]:
    """Restituisce gli elementi divisi in bande (lista di liste), in ordine.

    Una banda è delimitata dagli elementi a **tutta larghezza**; dentro la banda
    l'ordine è: colonna per colonna (sinistra→destra), top→bottom. Gli elementi
    a tutta larghezza sono separatori (banda a sé, emessi al loro posto).
    """
    full = [e for e in elements if e["w"] >= 0.6 * page_width]
    body = [e for e in elements if e["w"] < 0.6 * page_width]

    splits = _column_splits(body, page_width)

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
    # blocchi-figura che **attraversano le colonne** → trattati come a tutta
    # larghezza: i pannelli di una stessa figura restano insieme e nel punto di
    # lettura (invece di essere spezzati dall'ordine colonna-major).
    splits = _column_splits(keep, pw)
    spanning_ids: set[int] = set()
    synthetic: list[dict] = []
    for cl in _group_figure_blocks(keep, splits, pw):
        if not cl["spanning"]:
            continue
        for p in cl["pics"]:
            spanning_ids.add(id(p))
        synthetic.append({
            "class": "_figure_block",
            "bbox": (cl["x0"], cl["y0"], cl["x1"], cl["y1"]),
            "w": pw, "y0": cl["y0"], "text": "", "pics": cl["pics"],
        })
    keep2 = [e for e in keep if id(e) not in spanning_ids] + synthetic

    # tabelle a più colonne con **intestazioni separate** (es. Medical|Lifestyle):
    # raggruppa le righe per colonna per non interlacciare le liste
    tables = [e for e in keep2 if e["class"] == "table"]
    seen: set[int] = set()
    groups: list[list[dict]] = []
    for t in tables:
        if id(t) in seen:
            continue
        grp = [t]
        seen.add(id(t))
        for u in tables:
            if id(u) in seen:
                continue
            if not (t["bbox"][3] < u["bbox"][1] - 3
                    or u["bbox"][3] < t["bbox"][1] - 3):
                grp.append(u)
                seen.add(id(u))
        if len(grp) >= 2:
            groups.append(grp)
    if groups:
        remove_ids: set[int] = set()
        tbl_synth: list[dict] = []
        for grp in groups:
            text, region, member_ids = _multi_header_table(page, grp, keep2)
            if text.strip():
                remove_ids |= member_ids
                tbl_synth.append({
                    "class": "_table_block", "bbox": region,
                    "w": pw, "y0": region[1], "text": text,
                })
        keep2 = [e for e in keep2 if id(e) not in remove_ids] + tbl_synth

    bands, seps, ncol = _order(keep2, pw)

    # figure (per il bleed): rect + righe del testo interno
    pics_keep = [e for e in keep if e["class"] == "picture"]
    pic_lines = {id(e): _internal_lines(e["text"]) for e in pics_keep}

    def _overlaps(a: tuple, b: tuple) -> bool:
        return (min(a[2], b[2]) - max(a[0], b[0]) > 0
                and min(a[3], b[3]) - max(a[1], b[1]) > 0)

    out: list[str] = []
    emitted_rects: list[tuple] = []  # rect delle picture effettivamente emesse

    def _emit_image(rect: tuple) -> None:
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

    def emit(e: dict) -> None:
        c = e["class"]
        seg = (e["text"] or "").strip()
        if c == "_figure_block":
            # immagini ed etichette pannello in ordine di **riga** (y con
            # tolleranza, poi x), poi la didascalia della figura
            def _row(e2):
                return (round(e2["bbox"][1] / 12), e2["bbox"][0])

            pics = sorted(e["pics"], key=_row)
            for p in pics:
                _emit_image(p["bbox"])
            caps = [cap for p in pics for cap in attached.get(id(p), [])]
            panel = [x for x in caps if not _is_figure_caption(x["text"])]
            figc = [x for x in caps if _is_figure_caption(x["text"])]
            for x in (sorted(panel, key=_row)
                      + sorted(figc, key=lambda x: x["bbox"][1])):
                t = (x["text"] or "").strip()
                if t:
                    out.append(t)
            return
        if c == "_table_block":
            if seg:
                out.append(seg)
            return
        if c == "picture":
            _emit_image(e["bbox"])
            for cap in attached.get(id(e), []):
                ct = (cap["text"] or "").strip()
                if ct:
                    out.append(ct)
            return
        if c == "caption" and id(e) in used:
            return  # già emessa con la figura
        if c == "table":
            # 1 colonna: pymupdf4llm può fondere voci → ricostruisci dalle righe
            if _table_is_single_col(seg):
                rebuilt = _single_col_table_from_page(page, e["bbox"])
                if rebuilt.strip():
                    out.append(rebuilt)
                    return
            out.append(_normalize_md_table(seg))
            return
        if seg:
            # Bleed: se il blocco si sovrappone a una figura, togli le etichette
            # interne intrecciate (assi/legenda/tabella).
            if c in _TEXTY and pics_keep:
                near = [p for p in pics_keep if _overlaps(e["bbox"], p["bbox"])]
                if near:
                    lines = [l for p in near for l in pic_lines[id(p)]]
                    if _has_bleed(seg, lines):
                        # Fase 2b: ricostruzione **word-level** spaziale (esclude
                        # le parole dentro figure/didascalie). Il gate di
                        # sicurezza confronta con il risultato token-removal
                        # (contenuto reale): si usa il rebuild SOLO se non perde
                        # parole; altrimenti si resta sulla token-removal (che
                        # preserva il markdown).
                        stripped = _strip_figure_bleed(seg, lines)
                        excl = [o["bbox"] for o in keep
                                if o is not e and _overlaps(o["bbox"], e["bbox"])]
                        rebuilt = _rebuild_from_words(page, e["bbox"], excl)
                        expected = _norm_words(stripped)
                        missing = expected - _norm_words(rebuilt)
                        if rebuilt and len(missing) <= max(1, int(0.1 * len(expected))):
                            seg = rebuilt
                        else:
                            seg = stripped
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
