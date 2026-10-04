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


def _table_col_count(text: str) -> int:
    """Numero di colonne del markdown-tabella della content map."""
    n = 0
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = s.strip("|").split("|")
            if all(set(c.strip()) <= set("-: ") for c in cells):
                continue
            n = max(n, len(cells))
    return n


def _grid_table_from_page(page, bbox: tuple, ncol: int = 0) -> str:
    """Ricostruisce una tabella **grid** dalle righe di pagina.

    Divide le celle per i **gap orizzontali** tra parole: robusto quando il testo
    delle celle della content map è garbled (es. p231). ``ncol`` è il minimo.
    """
    def _inside(b, r, pad=3.0):
        return (b[0] >= r[0] - pad and b[2] <= r[2] + pad
                and b[1] >= r[1] - pad and b[3] <= r[3] + pad)

    words = [w for w in page.get_text("words") if _inside(w[:4], bbox)]
    if not words:
        return ""
    words.sort(key=lambda w: (round(w[1] / 3), w[0]))
    rows: list[list] = []
    cur: list = []
    ly = None
    for w in words:
        if ly is not None and abs(w[1] - ly) > 5:
            rows.append(cur)
            cur = []
        cur.append(w)
        ly = w[1]
    if cur:
        rows.append(cur)
    # confini di colonna: x coperte da **poche** righe (gap verticale). Robusto a
    # righe a tutta larghezza (titolo) e a celle lunghe su y indipendenti.
    x0r, x1r = bbox[0], bbox[2]
    W = int(x1r - x0r) + 1
    rowcov = [0] * W
    for r in rows:
        cov = [0] * W
        for w in r:
            if not w[4].strip():
                continue
            a = max(0, int(w[0] - x0r))
            b = min(W, int(w[2] - x0r))
            for i in range(a, b):
                cov[i] = 1
        for i in range(W):
            rowcov[i] += cov[i]
    thr = 0.25 * len(rows)
    gaps: list[tuple[int, int]] = []
    i = 0
    while i < W:
        if rowcov[i] <= thr:
            j = i
            while j < W and rowcov[j] <= thr:
                j += 1
            if j - i >= 4:
                gaps.append((i, j))
            i = j
        else:
            i += 1
    want = max(ncol - 1, 0)
    if want and len(gaps) > want:
        gaps = sorted(gaps, key=lambda g: g[1] - g[0], reverse=True)[:want]
        gaps.sort()
    bounds = [x0r + (a + b) / 2 for a, b in gaps]
    n = max(len(bounds) + 1, ncol, 1)

    def col_of(w) -> int:
        return min(sum(1 for b in bounds if w[0] >= b), n - 1)

    md_rows: list[list[str]] = []
    for r in rows:
        cells = [""] * n
        for w in r:
            if not w[4].strip():
                continue
            ci = col_of(w)
            cells[ci] = (cells[ci] + " " + w[4]).strip()
        md_rows.append(cells)
    out = ["| " + " | ".join(r) + " |" for r in md_rows]
    if len(out) >= 2:
        out.insert(1, "|" + "|".join(["---"] * n) + "|")
    return "\n".join(out)


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


def _strip_cell_junk(md: str, page) -> str:
    """Toglie dalle celle i token di 1 lettera che NON sono parole della pagina.

    Artefatto della content map: un carattere di controllo del font (es. ``\\x07``)
    diventa ``<br>i`` in fondo a una cella (es. ox16 p506). Si rimuove **solo** se
    il token non esiste come parola isolata nella pagina (niente falsi positivi
    su articoli/lettere reali).
    """
    try:
        page_words = {w[4].strip().lower() for w in page.get_text("words")}
    except Exception:
        return md

    def _fix_cell(cell: str) -> str:
        def _rep(m):
            return "" if m.group(1).lower() not in page_words else m.group(0)
        return re.sub(r"(?i)<br\s*/?>\s*([A-Za-z])\s*$", _rep, cell.strip())

    out: list[str] = []
    for ln in (md or "").splitlines():
        s = ln.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = s.strip("|").split("|")
            if all(set(c.strip()) <= set("-: ") for c in cells):
                out.append(ln)
                continue
            out.append("|" + "|".join(_fix_cell(c) for c in cells) + "|")
        else:
            out.append(ln)
    return "\n".join(out)


def _split_side_by_side_tables(seg: str):
    """Divide una tabella che in realtà ne contiene **due affiancate**.

    La content map a volte fonde due tabelle vicine in un'unica griglia (es.
    fe22 p2685: TABLE 2 | TABLE 4). Se la prima riga contiene ≥2 marker
    ``TABLE/FIG n`` in celle diverse, si taglia lì. Ritorna ``(sx, dx)`` o None.
    """
    rows = [ln for ln in (seg or "").splitlines() if ln.strip()]
    if not rows or not rows[0].strip().startswith("|"):
        return None
    first = rows[0].strip().strip("|").split("|")
    marks = [i for i, c in enumerate(first)
             if re.match(r"(?i)^\s*\**\s*(?:table|fig)\s*\.?\s*\d", c.strip())]
    if len(marks) < 2:
        return None
    cut = marks[1]
    if not 0 < cut < len(first):
        return None
    left: list[str] = []
    right: list[str] = []
    for ln in rows:
        s = ln.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        cells = s.strip("|").split("|")
        left.append("|" + "|".join(cells[:cut]) + "|")
        right.append("|" + "|".join(cells[cut:]) + "|")
    return "\n".join(left), "\n".join(right)


def _column_splits(elements: list[dict], page_width: float) -> list[float]:
    """Confini di colonna dalle sole geometrie dei box di TESTO (Fase 1).

    Primario: il merge degli intervalli (comportamento consolidato del motore,
    affidabile quando le colonne sono pulite). **Fallback**: la proiezione a
    copertura di ``layout_proxies`` quando il merge **non trova** colonne — caso
    in cui un box che "pontica" le colonne fa collassare il merge (difetto noto
    "colonne collassate"). Così non si regredisce sulle pagine già corrette.
    """
    import main  # lazy: main importa layout_engine
    import layout_proxies

    texty = [e for e in elements
             if e["class"] in ("text", "section-header", "title")]
    splits = main._detect_column_splits(
        [{"x0": e["bbox"][0], "x1": e["bbox"][2],
          "y0": e["bbox"][1], "y1": e["bbox"][3]} for e in texty],
        page_width,
    )
    if splits:
        return splits
    return layout_proxies.column_splits(elements, page_width)


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


def _separated_by_split(a: dict, b: dict, splits: list[float]) -> bool:
    """True se un confine di colonna sta **tra** due box (colonne diverse).

    Serve a non fondere tabelle **affiancate** in colonne diverse (es. TABLE
    257-4 e TABLE 257-5): sono indipendenti e vanno lette in sequenza di colonna,
    non unite come intestazioni multiple della stessa tabella.
    """
    ax0, ax1 = a["bbox"][0], a["bbox"][2]
    bx0, bx1 = b["bbox"][0], b["bbox"][2]
    return any(ax1 <= s <= bx0 or bx1 <= s <= ax0 for s in splits)


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


def reorder_boxes(elements: list[dict], page_width: float) -> list[dict]:
    """Sequenza **piatta** degli elementi in ordine di lettura (Fase 1).

    Wrapper testabile di ``_order``: bande e separatori a tutta larghezza
    interleavati esattamente come nell'emissione di ``build_markdown``. È il
    punto in cui intervenire per l'ordine (float, colonne, refs) senza toccare
    il GNN.
    """
    bands, seps, _ncol = _order(elements, page_width)
    ordered: list[dict] = []
    for i, band in enumerate(bands):
        ordered.extend(band)
        if i < len(seps):
            ordered.append(seps[i])
    return ordered


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


#: legature tipografiche → espansione ASCII (PyMuPDF le emette come singolo char)
_LIGATURES = {0xFB00: "ff", 0xFB01: "fi", 0xFB02: "fl",
              0xFB03: "ffi", 0xFB04: "ffl"}
#: spazio spurio dopo una legatura, prima di una continuazione minuscola
_LIG_SPACE_RE = re.compile(r"(ffi|ffl|ff|fi|fl)\s+(?=[a-z])")


def _expand_ligatures(text: str) -> str:
    """Espande le legature e toglie lo **spazio spurio** che PyMuPDF vi appone.

    ``Deﬁ nition`` → ``Definition``. Lo spazio si toglie **solo** se il testo
    conteneva davvero una legatura: il pattern ``ff|fi|fl`` da solo è troppo
    comune (``staff in`` non va toccato).
    """
    if not any(ord(c) in _LIGATURES for c in text):
        return text
    return _LIG_SPACE_RE.sub(r"\1", text.translate(_LIGATURES))


def _ligature_fixes(page) -> dict[str, str]:
    """Coppie ``parola-spezza`` → ``parola-unita`` dalle legature della pagina.

    La content map espande la legatura in ``fi`` ma **conserva** lo spazio
    (``Defi nition``): qui si ricostruisce cosa sostituire nel markdown.
    """
    fixes: dict[str, str] = {}
    try:
        d = page.get_text("dict")
    except Exception:
        return fixes
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            for s in ln.get("spans", []):
                raw = s.get("text", "")
                if not any(ord(c) in _LIGATURES for c in raw):
                    continue
                broken = raw.translate(_LIGATURES)
                fixed = _LIG_SPACE_RE.sub(r"\1", broken)
                if fixed != broken:
                    fixes[broken] = fixed
    return fixes


def _page_text_in_bbox(page, bbox: tuple, pad: float = 2.0) -> str:
    """Testo di pagina nel bbox, su una riga (spazi normalizzati)."""
    try:
        r = (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad)
        return " ".join(page.get_text("text", clip=r).split())
    except Exception:
        return ""


def _heading_from_page(page, bbox: tuple, seg: str) -> str | None:
    """Ricostruisce un **header** dal testo di pagina quando il motore lo spezza.

    Il motore a volte divide una parola dell'header a un confine di decorazione
    (es. to22 p780: ``Dif~~ f ~~erences`` mentre la pagina ha un unico span
    ``Differences``). Si usa il testo di pagina **solo** se una sua parola
    corrisponde alla concatenazione di ≥2 token del motore (split spurio):
    decorazioni/markdown non fanno scattare nulla. Solo header su riga singola.
    """
    if (bbox[3] - bbox[1]) > 30:
        return None
    page_t = _page_text_in_bbox(page, bbox)
    if not page_t or "\n" in page_t:
        return None
    core = re.sub(r"[*_`~]", "", re.sub(r"^[#\s]*", "", seg)).strip()

    def _alnum(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    ct = [t for t in core.split() if _alnum(t)]
    pt = [t for t in page_t.split() if _alnum(t)]
    if not ct or _alnum("".join(ct)) != _alnum("".join(pt)):
        return None
    i = 0
    split_found = False
    for p in pt:
        pn = _alnum(p)
        acc = ""
        j = i
        while j < len(ct) and len(acc) < len(pn):
            acc += _alnum(ct[j])
            j += 1
        if acc != pn:
            return None
        if j - i > 1:
            split_found = True
        i = j
    if not split_found or i != len(ct):
        return None
    prefix = re.match(r"^\s*#+\s*", seg)
    return (prefix.group(0) if prefix else "") + page_t


def _span_md(spans: list[dict]) -> str:
    """Markdown di una riga dai suoi span (grassetto ``**`` e corsivo ``_``)."""
    parts: list[str] = []
    for s in spans or []:
        t = _expand_ligatures(s.get("text", ""))
        if not t:
            continue
        lead = " " if t[:1].isspace() else ""
        trail = " " if t[-1:].isspace() else ""
        core = t.strip()
        if core:
            flags = s.get("flags", 0) or 0
            if flags & 2:       # italic
                core = "_" + core + "_"
            if flags & 16:      # bold
                core = "**" + core + "**"
        parts.append(lead + core + trail)
    return re.sub(r"[ \t]{2,}", " ", "".join(parts)).strip()


def _is_index_page(page, elements: list[dict]) -> bool:
    """True se la pagina è un indice (content map **o** righe fisiche).

    La content map può **fondere** le voci in poche righe: in tal caso il
    rilevatore sui box non scatta, quindi si guardano le righe fisiche della
    pagina (le stesse usate da ``index_markdown``).
    """
    import layout_proxies
    if layout_proxies._looks_like_index(elements):
        return True
    lines: list[str] = []
    try:
        for blk in page.get_text("dict").get("blocks", []):
            if blk.get("type") != 0:
                continue
            for ln in blk.get("lines", []):
                t = "".join(s.get("text", "") for s in ln.get("spans", [])).strip()
                if t:
                    lines.append(t)
    except Exception:
        return False
    if len(lines) < 15:
        return False
    hits = sum(1 for ln in lines if layout_proxies._INDEX_ENTRY_RE.search(ln))
    return hits >= 0.4 * len(lines)


def index_markdown(page, elements: list[dict], page_width: float) -> str:
    """Markdown dedicato per una pagina d'**indice**: una voce per riga.

    La content map di PyMuPDF4LLM **fonde** le voci in un unico paragrafo; qui
    si ricostruisce la struttura riga-per-riga dalle righe fisiche della pagina,
    ordinate per colonna (sx→dx) e top→bottom. Le continuazioni che contengono
    **solo numeri di pagina** (es. ``2185``) si riuniscono alla voce precedente.
    Chrome (header/footer) esclusi.
    """
    splits = _column_splits(elements, page_width)
    chrome = [e["bbox"] for e in elements
              if e.get("class") in _DROP_CLASSES]

    def _in_chrome(bb) -> bool:
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        return any(r[0] - 2 <= cx <= r[2] + 2 and r[1] - 2 <= cy <= r[3] + 2
                   for r in chrome)

    def _col(x: float) -> int:
        return sum(1 for s in splits if x > s)

    rows: list[tuple[int, float, float, str]] = []
    try:
        d = page.get_text("dict")
    except Exception:
        return ""
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            bb = ln.get("bbox", (0, 0, 0, 0))
            if _in_chrome(bb):
                continue
            txt = _span_md(ln.get("spans", []))
            if not txt:
                continue
            rows.append((_col((bb[0] + bb[2]) / 2), bb[1], bb[0], txt))
    if not rows:
        return ""
    rows.sort(key=lambda r: (r[0], r[1], r[2]))

    out: list[str] = []
    only_nums = re.compile(r"^[\d,;\s\u2013\u2014\-]+[a-z]?\.?$")
    for _c, _y, _x, txt in rows:
        if out and only_nums.match(txt) and re.search(r"\d", txt):
            out[-1] = out[-1].rstrip() + " " + txt
        else:
            out.append(txt)
    return "\n".join(out)


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

    # Percorso dedicato per le pagine d'**indice**: la content map fonde le voci
    # in un paragrafo, quindi si ricostruisce riga-per-riga (vedi index_markdown).
    # Solo se non ci sono tabelle/figure: un indice non ne ha, e così una
    # eventuale misclassificazione non scarta contenuto strutturato.
    if (_is_index_page(page, els)
            and not any(e.get("class") in ("table", "picture") for e in els)):
        md = index_markdown(page, els, pw)
        if md.strip():
            if return_meta:
                return md, {"captions": set(), "rects": []}
            return md

    # scarta chrome, etichette di margine e `picture` **spurie** (decorativi)
    ph = page.rect.height
    keep: list[dict] = []
    for e in els:
        c = e["class"]
        x0, y0, x1, y1 = e["bbox"]
        if c in _DROP_CLASSES:
            # un chrome (header/footer) che in realtà contiene una **didascalia di
            # figura** (es. ce24 p1775: FIGURE 154-1 in fondo pagina classificata
            # `page-footer`) non va scartato: la si recupera come `caption`.
            if c != "page-number" and _is_figure_caption(e.get("text", "")):
                e = dict(e)
                e["class"] = "caption"
                keep.append(e)
            continue
        if c == "picture":
            w, h = x1 - x0, y1 - y0
            if w < 40 and (x0 < 30 or x1 > pw - 30):
                continue  # etichetta verticale di margine
            if w * h < 2500:
                # minuscolo: se il testo interno è un'**etichetta** (es. "C"), la
                # si emette come didascalia; altrimenti è un decorativo → scarta
                label = " ".join(_internal_lines(e["text"])).strip()
                if 0 < len(label) <= 3 and label.isalnum():
                    e = dict(e)
                    e["class"] = "caption"
                    e["text"] = label
                    keep.append(e)
                continue
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
            if _separated_by_split(t, u, splits):
                continue  # tabelle affiancate in colonne diverse: NON fondere
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
                # a tutta larghezza solo se attraversa le colonne; altrimenti
                # resta nel flusso della sua colonna (non spezza il testo)
                spanning = any(region[0] < s < region[2] for s in splits)
                tbl_synth.append({
                    "class": "_table_block", "bbox": region,
                    "w": pw if spanning else region[2] - region[0],
                    "y0": region[1], "text": text,
                })
        keep2 = [e for e in keep2 if id(e) not in remove_ids] + tbl_synth

    ordered = reorder_boxes(keep2, pw)

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

    def _emit_clip(rect: tuple, alt: str) -> None:
        """Embed di un ritaglio di pagina come JPEG base64 (equazioni/formule)."""
        if not (embed_figures and figures_dir is not None):
            return
        import pymupdf
        try:
            pix = page.get_pixmap(clip=pymupdf.Rect(rect),
                                   matrix=pymupdf.Matrix(2, 2))
            jpg = pix.tobytes("jpeg")
        except Exception:
            jpg = None
        if jpg:
            import base64
            out.append(f"![{alt}](data:image/jpeg;base64,"
                       + base64.b64encode(jpg).decode("ascii") + ")")
            emitted_rects.append(rect)

    def emit(e: dict) -> None:
        c = e["class"]
        seg = (e["text"] or "").strip()
        if c in ("section-header", "title") and seg:
            fixed = _heading_from_page(page, e["bbox"], seg)
            if fixed:
                seg = fixed
        if c == "formula":
            # l'equazione è un grafico: il testo è perso (operatori/frazioni) →
            # la si rende come immagine del ritaglio
            r = e["bbox"]
            if r[2] - r[0] >= 60 and r[3] - r[1] >= 8:
                _emit_clip((r[0] - 5, r[1] - 4, r[2] + 5, r[3] + 4), "equazione")
            elif r[3] - r[1] >= 6:
                # etichetta "(n)" a destra: l'equazione sta a sinistra
                _emit_clip((max(0.0, r[0] - 200), r[1] - 8,
                            r[2] + 5, r[3] + 8), "equazione")
            return
        if (c == "text" and (e["bbox"][3] - e["bbox"][1]) <= 16
                and re.search(r"\(\d\)\s*$", re.sub(r"<[^>]+>", "", seg))):
            # riga-equazione sfuggita come testo (operatori persi) → immagine
            r = e["bbox"]
            _emit_clip((r[0] - 5, r[1] - 4, r[2] + 5, r[3] + 4), "equazione")
            return
        if c == "_figure_block":
            # Immagini e didascalie raggruppate **per riga**: figure distinte
            # impilate verticalmente (es. Fig. 6.7 sopra Fig. 6.8) restano
            # ciascuna con la propria didascalia, invece di mettere tutte le
            # immagini prima di tutte le didascalie. I pannelli **affiancati**
            # (stessa riga) restano insieme, poi le didascalie in ordine di y.
            pics = sorted(e["pics"], key=lambda p: (round(p["bbox"][1] / 12),
                                                    p["bbox"][0]))
            rows: list[list[dict]] = []
            for p in pics:
                if rows and abs(rows[-1][0]["bbox"][1] - p["bbox"][1]) <= 12:
                    rows[-1].append(p)
                else:
                    rows.append([p])
            for grp in rows:
                for p in grp:
                    _emit_image(p["bbox"])
                caps = [cap for p in grp for cap in attached.get(id(p), [])]
                for x in sorted(caps, key=lambda c: c["bbox"][1]):
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
            if tuple(e["bbox"]) in rot_tables:
                # tabella **ruotata**: non linearizzabile → immagine fedele
                _emit_clip(e["bbox"], "tabella")
                return
            # 1 colonna: pymupdf4llm può fondere voci → ricostruisci dalle righe
            if _table_is_single_col(seg):
                rebuilt = _single_col_table_from_page(page, e["bbox"])
                if rebuilt.strip():
                    out.append(rebuilt)
                    return
            else:
                # grid: ricostruisci le celle dalle righe di pagina e usala SOLO
                # se la content map è **davvero rotta** (recall < 0.90), non se è
                # solo imperfetta: la griglia a copertura disallinea le celle
                # quando la content map è corretta (es. fe22 p1101, titolo
                # spezzato tra celle ma dati perfetti).
                base = _normalize_md_table(_strip_cell_junk(seg, page))
                chosen = base
                grid = _grid_table_from_page(page, e["bbox"],
                                             _table_col_count(seg))
                if grid.strip():
                    r0, r1 = e["bbox"][0], e["bbox"][2]
                    ry0, ry1 = e["bbox"][1], e["bbox"][3]
                    ref = " ".join(
                        w[4] for w in page.get_text("words")
                        if r0 - 3 <= w[0] and w[2] <= r1 + 3
                        and ry0 - 3 <= w[1] and w[3] <= ry1 + 3)
                    base_r = main._word_recall(ref, base)
                    grid_r = main._word_recall(ref, grid)
                    if ref and base_r < 0.90 and grid_r > base_r + 0.02:
                        chosen = grid
                # due tabelle **affiancate** fuse in una griglia: separale
                split = _split_side_by_side_tables(chosen)
                if split:
                    out.append(_normalize_md_table(split[0]))
                    out.append(_normalize_md_table(split[1]))
                    return
                out.append(chosen)
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
                            candidate = rebuilt
                        else:
                            candidate = stripped
                        # Guardia d'**integrità**: se la pulizia del bleed perde
                        # troppo testo rispetto all'originale (es. la picture
                        # ingloba un paragrafo, ce24 p1775), si tiene l'originale.
                        orig_words = _norm_words(seg)
                        lost = orig_words - _norm_words(candidate)
                        if not orig_words or len(lost) <= 0.15 * len(orig_words):
                            seg = candidate
            out.append(seg)

    rot_tables = {tuple(r) for r in main._rotated_table_rects(page, keep)}
    for e in ordered:
        emit(e)
    md = "\n\n".join(out)
    fixes = _ligature_fixes(page)
    for broken, fixed in fixes.items():
        md = md.replace(broken, fixed)
    if return_meta:
        served = {
            main._norm_text(cap["text"])[:30]
            for caps in attached.values() for cap in caps
        }
        return md, {"captions": served, "rects": emitted_rects}
    return md
