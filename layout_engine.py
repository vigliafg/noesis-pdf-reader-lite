#!/usr/bin/env python3
"""Engine adattativo dei fix di layout — "Profilo → Piano → Pipeline".

Il modulo NON importa PyQt (è puro: pymupdf + tipi), quindi è testabile senza
GUI. Le funzioni dei fix vivono in ``main.py`` e vengono importate *lazy* solo
quando servono, per evitare dipendenze circolari.

Componenti:

- ``LayoutProfile`` — il profiler ispeziona la pagina UNA volta e produce i
  segnali del layout (colonne, tabelle, font, indice…).
- ``Fix`` / ``FIX_REGISTRY`` — il "database" dei fix: id, priorità, predicato
  ``when(profile, backend)`` e funzione ``apply(md, page, profile)``.
- ``plan_fixes(profile, backend, mode)`` — lo scheduler: filtra i fix del
  registro i cui ``when`` sono veri, li ordina per priorità (con override
  utente da ``fix_rules.json``).
- ``apply_plan(md, page, profile, plan)`` — la pipeline: applica i fix in
  ordine.

Uso tipico (da ``main.py``)::

    profile = layout_engine.profile_page(doc[page_num])
    plan = layout_engine.plan_fixes(profile, backend, mode="auto")
    out = layout_engine.apply_plan(text, doc[page_num], profile, plan) or text
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Sequence

# ─────────────────────────────────────────────────────────────────────────────
#  profilo
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LayoutProfile:
    columns: int                      # 1..N (0 = pagina vuota/non testuale)
    splits: tuple[float, ...]         # N-1 confini di colonna
    columns_overlap: bool             # le colonne si affiancano verticalmente
    has_tables: bool                  # find_tables ha trovato tabelle dati
    full_width_tables: int            # tabelle a tutta larghezza
    has_small_text: bool              # blocchi body < 7.5pt (note/referenze)
    has_references: bool              # entry numerate "1." "2." in >=2 colonne
    has_index: bool                   # >=3 colonne e righe con numeri-pagina
    body_blocks: int                  # blocchi di testo fuori dalle tabelle
    has_toc: bool = False             # il documento ha un indice (get_toc)


def _data_tables(page, exclude: Sequence[tuple] = ()) -> list:
    """Data tables come le vedono i fix (esclude i blocchi titolo 1x1/1x2 e le zone escluse)."""
    try:
        tabs = page.find_tables()
    except Exception:
        return []
    from main import _is_excluded  # lazy: evita import circolare
    out = []
    for t in tabs.tables:
        if t.row_count <= 1 and t.col_count <= 2:
            continue
        if _is_excluded(tuple(t.bbox), exclude):
            continue
        out.append(t)
    return out


def figure_text_regions(page, exclude: Sequence[tuple] = (), pad: float = 4.0) -> list[tuple]:
    """Rettangoli delle figure da cui escludere il **testo interno**.

    Etichette degli assi, legende e titoli dei grafici sono testo, ma sono
    dentro l'immagine della figura. Sono più stretti del 60% della pagina e non
    stanno in una tabella: senza escluderli fanno da "ponte" tra le colonne e il
    rilevatore collassa la pagina a una colonna (l'ordine di lettura resta
    intrecciato). Si usa l'unione grafico+didascalia perché le etichette degli
    assi stanno proprio tra i due; la didascalia resta contenuto (chi la usa la
    protegge con ``_FIGURE_CAPTION_RE``). Vedi ha22/p101 (FIG. 10-1).
    """
    from main import _figure_regions  # lazy: evita import circolare

    regions: list[tuple] = []
    try:
        for f in _figure_regions(page, exclude):
            x0, y0, x1, y1 = f["rect"]
            c = f.get("caption_rect")
            if c:
                x0, y0 = min(x0, c[0]), min(y0, c[1])
                x1, y1 = max(x1, c[2]), max(y1, c[3])
            regions.append((x0 - pad, y0 - pad, x1 + pad, y1 + pad))
    except Exception:
        return []
    return regions


def _body_blocks(page, exclude: Sequence[tuple] = ()) -> tuple[list[tuple], list[dict]]:
    """Return (table_regions, body_blocks) come in ``_column_aware_markdown``."""
    from main import _collect_blocks  # lazy: evita import circolare

    table_regions: list[tuple] = []
    for t in _data_tables(page, exclude):
        table_regions.append(tuple(t.bbox))

    figure_regions = figure_text_regions(page, exclude)

    def _inside(b: dict, r: tuple) -> bool:
        return (
            b["x0"] >= r[0] - 2 and b["x1"] <= r[2] + 2
            and b["y0"] >= r[1] - 2 and b["y1"] <= r[3] + 2
        )

    pw = page.rect.width
    blocks = [b for b in _collect_blocks(page, exclude) if b["max_size"] >= 6.5]
    body = [
        b for b in blocks
        if not any(_inside(b, r) for r in table_regions)
        and not any(_inside(b, r) for r in figure_regions)
        and (b["x1"] - b["x0"]) < 0.6 * pw
        and (b["x1"] - b["x0"]) >= 25
    ]
    return table_regions, body


def _block_text(b: dict) -> str:
    return " ".join(s["text"] for line in b["lines"] for s in line)


# Pattern "termine, PAGINA" tipico di una voce d'indice: virgola + numero di
# pagina (anche intervallo "1125–1126" e suffissi "f"/"b"/"t").
_INDEX_ENTRY_RE = re.compile(r",\s*\d{1,4}(?:[–\-]\d{1,4})?[a-z]?\b")


def _has_toc(page) -> bool:
    doc = getattr(page, "parent", None)
    if doc is None:
        return False
    try:
        return bool(doc.get_toc())
    except Exception:
        return False


#: Soglia minima di caratteri verticali perché una pagina sia "sideways".
_SIDEWAYS_MIN_CHARS = 60


def detect_sideways_rotation(page) -> int:
    """Rotazione da applicare (0/90/270) se il testo è disegnato a 90°.

    Alcune pagine landscape (tabelle grandi) hanno il testo ruotato di 90°
    *senza* ``page.rotation``: pymupdf4llm le legge lungo l'asse sbagliato e
    fonde/persé righe. Rilevata la direzione, ``page.set_rotation`` la
    raddrizza. Ritorna 0 se la pagina è già orizzontale o prevalentemente tale.
    """
    if page is None:
        return 0
    horiz = vert = 0
    dy_sum = 0.0
    try:
        for b in page.get_text("dict").get("blocks", []):
            for ln in b.get("lines", []):
                n = sum(len(s.get("text", "")) for s in ln.get("spans", []))
                dx, dy = ln.get("dir", (1, 0))
                if abs(dx) >= 0.7:
                    horiz += n
                elif abs(dy) >= 0.7:
                    vert += n
                    dy_sum += dy * n
    except Exception:
        return 0
    if vert < _SIDEWAYS_MIN_CHARS or vert <= horiz:
        return 0
    return 90 if dy_sum < 0 else 270


def profile_page(page, exclude: Sequence[tuple] = ()) -> LayoutProfile:
    """Misura il layout della pagina UNA volta (puro, deterministico).

    ``exclude``: zone escluse a mano (rettangoli in punti PDF) che il profiler
    ignora, così colonne/tabelle vengono misurate sulla sorgente "pulita".
    """
    from main import _detect_column_splits, _strip_margin_blocks  # lazy

    pw = page.rect.width
    ph = page.rect.height
    _table_regions, body = _body_blocks(page, exclude)
    # Headers/footers/watermarks non devono fare da "ponte" tra due colonne:
    # escluderli prima di calcolare i confini colonna.
    splits = tuple(_detect_column_splits(_strip_margin_blocks(body, ph), pw))
    n_cols = len(splits) + 1

    # columns_overlap: prima e ultima colonna condividono spazio verticale.
    columns_overlap = False
    if n_cols >= 2:
        def _col(b: dict) -> int:
            return sum(1 for s in splits if (b["x0"] + b["x1"]) / 2 > s)
        cols: list[list[dict]] = [[] for _ in range(n_cols)]
        for b in body:
            cols[_col(b)].append(b)
        cols = [c for c in cols if c]
        if len(cols) >= 2:
            first, last = cols[0], cols[-1]
            columns_overlap = any(
                lb["y0"] <= rb["y1"] and rb["y0"] <= lb["y1"]
                for lb in first for rb in last
            )

    tables = _data_tables(page, exclude)
    has_tables = bool(tables)
    full_width_tables = sum(
        1 for t in tables if (t.bbox[2] - t.bbox[0]) >= 0.6 * pw
    )

    has_small_text = any(b["max_size"] < 7.5 for b in body)

    # has_references: entry numerate "1." in >=2 colonne + testo piccolo.
    has_references = False
    if n_cols >= 2 and has_small_text and body:
        numbered = sum(1 for b in body if re.match(r"^\s*\d+\.", _block_text(b)))
        has_references = numbered >= 0.5 * len(body)

    # has_index: >=3 colonne e la maggior parte dei blocchi contiene una voce
    # d'indice ("termine, 1125" / "termine, 86, 87, 90"). Nota: pymupdf fonde
    # più voci in un unico blocco, quindi si guarda il pattern ovunque nel
    # testo, NON a fine riga (l'euristica "righe corte con suffissi" del piano
    # non rilevava gli indici reali — vedi VERIFICA_5_PDF.md).
    has_index = False
    if n_cols >= 3 and body:
        entries = sum(1 for b in body if _INDEX_ENTRY_RE.search(_block_text(b)))
        has_index = entries >= 0.5 * len(body)

    return LayoutProfile(
        columns=n_cols,
        splits=splits,
        columns_overlap=columns_overlap,
        has_tables=has_tables,
        full_width_tables=full_width_tables,
        has_small_text=has_small_text,
        has_references=has_references,
        has_index=has_index,
        body_blocks=len(body),
        has_toc=_has_toc(page),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  registro dei fix (il "database")
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Fix:
    id: str
    description: str
    order: int                        # priorità nel piano
    when: Callable[[LayoutProfile, str], bool]   # (profile, backend)
    apply: Callable[..., str]         # (md, page, profile) -> md


# I fix incapsulano le funzioni già esistenti in main.py (import lazy).
def _apply_dehyphenate(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    from main import clean_text
    return clean_text(md)


def _apply_split_glued(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    from main import _split_cross_column_paragraphs
    return _split_cross_column_paragraphs(md, page)


_RETAIN_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]+")
_FRAG_LINE_RE = re.compile(r"(?m)^[ \t]*[-*+•]?[ \t]*[0-9A-Za-z][.)]?[ \t]*$")


def _content_retention(a: str, b: str) -> float:
    """Frazione di termini (insiemi, case-insensitive) di ``a`` presenti in ``b``."""
    ta = {t.lower() for t in _RETAIN_TOKEN_RE.findall(a) if len(t) >= 2}
    tb = {t.lower() for t in _RETAIN_TOKEN_RE.findall(b) if len(t) >= 2}
    return (len(ta & tb) / len(ta)) if ta else 1.0


def _fragment_lines(s: str) -> int:
    """Righe che sono un solo token ("- 2", "d", "1."): segnale di garble."""
    return len(_FRAG_LINE_RE.findall(s))


def _apply_reorder_columns(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    from main import _column_aware_markdown
    reordered = _column_aware_markdown(page, move_title=False, exclude=exclude) or md
    # Rete di sicurezza: il riordino rigenera la pagina dai blocchi e può
    # **perdere** contenuto (box/tabelle laterali) o introdurre righe-frammento
    # (falsa tabella). Meglio conservare il raw (ordine eventualmente da
    # sistemare) che perdere contenuto o degradarlo: si ripiega sul raw se
    # (a) perde >15% dei termini oppure (b) aggiunge ≥5 righe-frammento.
    if is_fix_disabled("reorder_guard"):
        return reordered
    if _content_retention(md, reordered) < 0.85:
        return md
    if _fragment_lines(reordered) > _fragment_lines(md) + 4:
        return md
    return reordered


def _apply_spacing(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    from main import _spacing_fixes
    return _spacing_fixes(md)


# ── cleanup del markdown (Pack 1) ───────────────────────────────────────────
# Un unico fix con quattro passate pure: header/footer di stampa, heading,
# liste, corsivi. Conservativo: agisce solo su pattern riconoscibili.

_HEADING_RE = re.compile(r"^(#{1,6})\s*(.*)$")
_CAPTION_HEADING_RE = re.compile(
    r"^#{1,6}\s*\**\s*(table|fig|figure|box|exhibit|chart)\b", re.IGNORECASE
)
_STANDALONE_NUM_RE = re.compile(r"^\s*(?:#{1,6}\s*|[-*+]\s+|\d+\.\s+)*\**\s*\d{1,4}\s*\**\s*$")

#: Prefisso markdown (heading, lista, numero) da ignorare nel confronto con il
#: testo di pagina: ``## ENDOCRINE DISORDERS`` e ``- 1123`` sono ancora
#: l'header/il numero di pagina, il ``#``/``-`` è solo la forma che ha preso
#: nel markdown. Senza questo la riga "travestita" non veniva riconosciuta.
_MD_PREFIX_RE = re.compile(r"^\s*(?:#{1,6}\s*|[-*+]\s+|\d+\.\s+)*")

#: Running head "48 CHAPTER 1 Cellular structure and function" (numero pagina
#: davanti al marcatore): è chrome di pagina, non un titolo di capitolo.
_RUNNING_HEAD_RE = re.compile(
    r"^\s*\**\s*\d{1,4}\s*\**\s+(?:chapter|part|section)\s+\d+\b", re.IGNORECASE
)


def _norm_noise(text: str) -> str:
    """Normalizza una riga per confrontare md e testo di pagina (header/footer)."""
    s = re.sub(r"<[^>]{0,40}>", "", text)  # tag HTML (<mark>, <sup>, <br>…)
    s = re.sub(r"[*_`~]", "", s)
    s = _MD_PREFIX_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    # header con apice: "Section II n" (get_text) vs "Section IIn" (md) → uguali
    s = re.sub(r" (?=\w\b)", "", s)
    return s


#: Tag HTML che pymupdf4llm emette per evidenziazioni/apici e che non hanno
#: senso in un markdown "pulito": vanno rimossi (rumore) e, soprattutto, non
#: devono impedire il riconoscimento di header/footer (`<mark>`/`<sup>` li
#: "travestivano", impedendone la rimozione — vedi Pack 5).
_HTML_TAG_RE = re.compile(
    r"</?(?:mark|sup|sub|span|b|i|u|em|strong|small|big|font|a|code|pre|"
    r"blockquote|div|p|br|hr)\b[^>]*>",
    re.IGNORECASE,
)


def _normalize_html_tags(md: str) -> str:
    """Rimuove i tag HTML residui; ``<br>`` diventa uno spazio."""
    if "<" not in md:
        return md
    md = re.sub(r"<br\s*/?>", " ", md, flags=re.IGNORECASE)
    return _HTML_TAG_RE.sub("", md)


_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _normalize_replacement_chars(md: str) -> str:
    """Normalizza U+FFFD (glifo assente) e i caratteri di controllo in spazi.

    Nei titoli/indici con font senza mappatura pymupdf emette ``\ufffd`` (o un
    carattere di controllo C0) al posto di un separatore (es. indice ox2:
    "synthesis\ufffd 108"). Uno spazio è la resa più leggibile; quei caratteri
    non portano contenuto recuperabile.
    """
    if "\ufffd" not in md and not _CTRL_RE.search(md):
        return md
    md = re.sub(r"[ \t]*\ufffd[ \t]*", " ", md)
    return _CTRL_RE.sub(" ", md)


def _normalize_soft_hyphens(md: str) -> str:
    """Ricomporre le parole spezzate da un soft hyphen (U+00AD).

    ``dom\\xad peridone`` → ``domperidone``: il soft hyphen è invisibile e
    indica sempre una sillabazione a fine riga, non un trattino vero.
    """
    if "\u00ad" not in md:
        return md
    return re.sub(r"\u00ad[ \t]*", "", md)


#: Numero "spaziato" dal font su un intervallo: "3 0-6 0" → "30-60".
_SPLIT_NUM_RANGE_RE = re.compile(
    r"(?<!\d)(\d+) (\d+)[ ]?([-–—])[ ]?(\d+) (\d+)(?!\d)"
)
#: Lettera iniziale separata dal resto della parola (font spaziato dei box).
#: Esclude A/I (parole legittime di una lettera: "A diagnosis", "I think").
_SPLIT_LETTER_RE = re.compile(r"\b([B-HJ-Z]) (?=[a-z]{2,}\b)")


def _despace_numbers(md: str) -> str:
    """Ricomponi i numeri spezzati dal font negli intervalli ("3 0-6 0")."""
    if not re.search(r"\d \d", md):
        return md
    return _SPLIT_NUM_RANGE_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}",
        md,
    )


def _despace_blockquote_letters(md: str) -> str:
    """Ricomponi la prima lettera separata nelle righe-citazione (box/figure).

    Solo dentro ``>`` e solo se la riga contiene ≥2 occorrenze del pattern
    ("C hloramphenicol · E rythromycin"): così non tocca prosa normale.
    """
    if "> " not in md and not md.startswith(">"):
        return md
    out: list[str] = []
    for ln in md.split("\n"):
        if ln.lstrip().startswith(">") and len(_SPLIT_LETTER_RE.findall(ln)) >= 2:
            out.append(_SPLIT_LETTER_RE.sub(r"\1", ln))
        else:
            out.append(ln)
    return "\n".join(out)


#: Didascalia numerata ("TABLE 2 …", "FIG. 14.10 …"): è contenuto, non un
#: header di pagina. Le didascalie di tabelle/box stanno spesso a filo del
#: margine alto e senza questo filtro venivano cancellate come running header.
_CAPTION_MARGIN_RE = re.compile(
    r"^\**\s*(?:table|fig(?:ure)?|box|exhibit|chart)\s*\.?\s*\d",
    re.IGNORECASE,
)


def _content_regions_below(page, exclude: Sequence[tuple] = ()) -> list[tuple]:
    """Box della pagina (rect): per non scambiare i loro titoli per header.

    Solo ``get_drawings`` (via ``_detect_boxes``), **senza** ``find_tables``:
    quest'ultimo costa ~1 s/pagina e qui serve solo una banda ``y`` approssimata
    (le didascalie delle tabelle sono comunque protette da ``_CAPTION_MARGIN_RE``).
    """
    try:
        from main import _detect_boxes  # lazy: evita import circolare

        return [b["rect"] for b in _detect_boxes(page, page.rect.width, [])]
    except Exception:
        return []


def _is_title_above(line_x0: float, line_x1: float, y1: float, regions: list[tuple]) -> bool:
    """True se la riga è il titolo/didascalia di un box o tabella che inizia subito sotto."""
    for r in regions:
        if not (-2 <= r[1] - y1 <= 25):
            continue
        overlap = min(line_x1, r[2]) - max(line_x0, r[0])
        if overlap >= 0.5 * max(1.0, min(line_x1 - line_x0, r[2] - r[0])):
            return True
    return False


#: Tolleranza (pt) per considerare due frammenti sulla stessa riga di base e
#: distanza orizzontale massima per unirli in un'unica voce di margine.
_MARGIN_JOIN_Y = 2.0
_MARGIN_JOIN_GAP = 12.0


def _iter_margin_lines(page):
    """Yield ``(text, x0, x1, y1)`` for the lines in the top/bottom margin band.

    I frammenti sulla **stessa riga di base** e **vicini** (es. la testatina
    spezzata da un cambio di stile in ``CHAPTER 10`` + ``QUALITY, SAFETY, AND
    VALUE``) sono uniti in un'unica voce. Altrimenti il singolo frammento
    diventa un candidato a sé e cancella un titolo di capitolo che ha lo stesso
    testo della testatina (la testatina ripete il titolo del capitolo). Restano
    separati i frammenti lontani (es. il numero di pagina a filo del margine).
    """
    try:
        height = page.rect.height
        blocks = page.get_text("dict")["blocks"]
    except Exception:
        return
    top, bottom = 0.08 * height, 0.92 * height
    frags: list[tuple[float, float, float, str]] = []
    for blk in blocks:
        if blk.get("type") != 0:
            continue
        for line in blk["lines"]:
            y0, y1 = line["bbox"][1], line["bbox"][3]
            if y1 <= top or y0 >= bottom:
                frags.append((
                    y1, line["bbox"][0], line["bbox"][2],
                    "".join(s["text"] for s in line["spans"]),
                ))
    frags.sort(key=lambda f: (round(f[0], 1), f[1]))
    merged: list[list] = []  # [y1, x0, x1, testo]
    for y1, x0, x1, txt in frags:
        for m in merged:
            if (abs(m[0] - y1) <= _MARGIN_JOIN_Y
                    and x0 >= m[1] - _MARGIN_JOIN_GAP
                    and x0 - m[2] <= _MARGIN_JOIN_GAP):
                m[2] = max(m[2], x1)
                m[3] = f"{m[3]} {txt}" if m[3] else txt
                break
        else:
            merged.append([y1, x0, x1, txt])
    for y1, x0, x1, txt in merged:
        yield (txt, x0, x1, y1)


_DOC_NOISE_CACHE: dict = {}


def _doc_noise_key(doc) -> tuple:
    name = getattr(doc, "name", "") or ""
    try:
        st = os.stat(name) if name else None
        sig = (st.st_size, int(st.st_mtime)) if st is not None else (0, 0)
    except Exception:
        sig = (0, 0)
    return (name, len(doc), sig, 0 if name else id(doc))


def _document_noise(page, min_pages: int = 3, max_samples: int = 25) -> set[str]:
    """Running headers/footers of the whole document (repeated across pages).

    A short string that shows up in the top/bottom margin of several pages is
    page chrome, not content — even when it is not all-caps or a single page
    misses it. Sampled (spread over the document) and cached per file, because
    the app re-opens the document for every page extraction.
    """
    doc = getattr(page, "parent", None)
    if doc is None:
        return set()
    try:
        n = len(doc)
    except Exception:
        return set()
    if n < min_pages:
        return set()
    key = _doc_noise_key(doc)
    cacheable = bool(getattr(doc, "name", ""))
    if cacheable:
        cached = _DOC_NOISE_CACHE.get(key)
        if cached is not None:
            return cached
        if len(_DOC_NOISE_CACHE) > 32:
            _DOC_NOISE_CACHE.clear()
    samples = min(max_samples, n)
    idxs = sorted({int(i * (n - 1) / max(1, samples - 1)) for i in range(samples)})
    counts: Counter = Counter()
    for i in idxs:
        try:
            pg = doc[i]
        except Exception:
            continue
        for text, _x0, _x1, _y1 in _iter_margin_lines(pg):
            s = text.lstrip()
            if not s or _CAPTION_MARGIN_RE.match(s):
                continue  # vuota o didascalia: mai header
            norm = _norm_noise(text)
            if norm and len(norm) <= 120 and len(norm.split()) <= 15:
                counts[norm] += 1
    noise = {s for s, c in counts.items() if c >= min_pages}
    if cacheable:
        _DOC_NOISE_CACHE[key] = noise
    return noise


def _margin_noise(page, exclude: Sequence[tuple] = ()) -> set[str]:
    """Stringhe che compaiono nei margini alto/basso della pagina (header/footer).

    Solo righe **brevi** (gli header/footer lo sono): una riga lunga di corpo
    che per caso entra nella banda dei margini non deve diventare un candidato,
    altrimenti si rischierebbe di cancellare testo legittimo.
    """
    if page is None:
        return set()
    regions = _content_regions_below(page, exclude)
    found: set[str] = set()
    for text, x0, x1, y1 in _iter_margin_lines(page):
        stripped = text.lstrip()
        # Un header/footer di stampa inizia con maiuscola o cifra
        # ("CHAPTER 18 …", "656", "HEART FAILURE…"). Una continuazione
        # di titolo di box come "or Maldigestion" o i frammenti di
        # corpo che cadono nella banda dei margini iniziano minuscoli:
        # non devono diventare candidati, altrimenti il box viene
        # tagliato a metà.
        if not stripped or not (stripped[0].isupper() or stripped[0].isdigit()):
            continue
        if _CAPTION_MARGIN_RE.match(stripped):
            continue  # didascalia di tabella/box: contenuto, non header
        if _is_title_above(x0, x1, y1, regions):
            continue  # titolo di sezione sopra un box/tabella
        norm = _norm_noise(text)
        if not (norm and len(norm) <= 120 and len(norm.split()) <= 15):
            continue
        # Una frase di prosa a filo pagina (nota/legenda con punto finale) è
        # contenuto, non un header/footer di stampa: gli header non sono frasi.
        # Meglio lasciare un footer di troppo (cosmetico) che cancellare testo.
        if norm.endswith(".") and len(norm.split()) >= 6:
            continue
        found.add(norm)
    # Frequenza tra pagine: un header/footer che ricorre su più pagine è chrome
    # di pagina anche quando la singola pagina non lo riconosce (T5.1).
    found |= _document_noise(page)
    return found


def _is_noise_line(line: str, candidates: set[str]) -> bool:
    """True se la riga è (o contiene solo) un header/footer di stampa."""
    if len(line) >= 200:
        return False
    # Running head tipico "48 CHAPTER 1 Cellular structure and function": il
    # numero di pagina davanti al marcatore è la firma dell'header (un titolo
    # di capitolo vero non ha la pagina davanti). I margini a volte non lo
    # intercettano (bande grafiche), quindi lo si riconosce dal pattern.
    if _RUNNING_HEAD_RE.match(line):
        return True
    if not candidates:
        return False
    norm = _norm_noise(line)
    if norm and norm in candidates:
        return True
    # Il reorder può fondere il numero di pagina con l'header:
    # "656 CHAPTER 18 Endocrine System" o "CHAPTER 18 … 656".
    for pat in (
        r"^\s*\**\s*\d{1,4}\s*\**\s+(.*)$",
        r"^(.*?)\s+\**\s*\d{1,4}\s*\**\s*$",
    ):
        m = re.match(pat, line)
        if m and _norm_noise(m.group(1)) in candidates:
            return True
    # Il reorder può anche fondere due pezzi di header in una sola riga
    # ("CHAPTER 221" + "Medical Issues in Pregnancy"). La riga è rumore se
    # l'intero testo si scompone in ≥2 candidati (spezzandola per candidati).
    return _covers_candidates(norm, candidates)


def _covers_candidates(norm: str, candidates: set[str]) -> bool:
    """True se ``norm`` è interamente coperto da ≥2 candidati in sequenza."""
    if not norm or len(norm) > 120 or len(norm.split()) > 15:
        return False
    s, used = norm, 0
    while s:
        best = ""
        for c in candidates:
            if c and s.startswith(c) and len(c) > len(best):
                best = c
        if best:
            s = s[len(best):].lstrip()
            used += 1
            continue
        m = re.match(r"^\d{1,4}\b\s*", s)  # numero di pagina nel mezzo
        if m:
            s = s[m.end():].lstrip()
            used += 1
            continue
        return False
    return used >= 2


def _strip_running_headers(md: str, page) -> str:
    """Rimuove header/footer di stampa e numeri di pagina isolati."""
    candidates = _margin_noise(page)
    kept: list[str] = [
        line for line in md.split("\n") if not _is_noise_line(line, candidates)
    ]
    # Numeri di pagina isolati in testa/coda (pymupdf4llm li sposta talvolta).
    non_empty = [i for i, ln in enumerate(kept) if ln.strip()]
    if non_empty:
        for idx in (non_empty[0], non_empty[-1]):
            if _STANDALONE_NUM_RE.match(kept[idx]):
                kept[idx] = ""
    return "\n".join(kept)


#: Nel font "spaziato" di alcuni libri (mw15) pymupdf4llm separa i glifi con
#: spazi e marca la lettera ``i`` in corsivo-barrato: ``**D** **~~i~~ vert**``
#: per "Diverticulum". Due passate ricompongono il titolo.
_SPACED_LETTER_RE = re.compile(r"[ \t]*~~([A-Za-z])~~[ \t]*")
_BOLD_RUNS_ONLY_RE = re.compile(
    r"^\s*(#{1,6})\s*((?:\*\*[^*\n]+\*\*)(?:[ \t]+\*\*[^*\n]+\*\*)+)\s*$"
)


def _rejoin_spaced_headings(md: str) -> str:
    """Ricompone i titoli resi a lettere separate (font "spaziato")."""
    if "~~" in md:
        # 1) la lettera corsiva isolata torna attaccata alla parola: "D ~~i~~ vert"
        md = _SPACED_LETTER_RE.sub(r"\1", md)
    # 2) un titolo fatto di soli run in grassetto adiacenti e' un titolo
    #    spezzato: uniscilo. (Un titolo con altro testo tra i run, es.
    #    "**A** = **B**", non viene toccato.) Il segnale e' ``** **``: senza
    #    di esso si salta il passaggio per riga (costoso su markdown grande).
    if "** **" not in md:
        return md
    out: list[str] = []
    for line in md.split("\n"):
        m = _BOLD_RUNS_ONLY_RE.match(line)
        if not m:
            out.append(line)
            continue
        joined = "".join(re.findall(r"\*\*([^*]+)\*\*", line))
        joined = re.sub(r"(?<=-)\s+(?=[A-Za-z])", "", joined)
        out.append(f"{m.group(1)} **{joined}**")
    return "\n".join(out)


def _normalize_headings(md: str) -> str:
    """Unisce titoli spezzati, elimina artefatti, retrocede le didascalie."""
    md = _rejoin_spaced_headings(md)
    md = md.replace("~~", "").replace("■", "")
    lines = md.split("\n")
    out: list[str] = []
    for line in lines:
        # Glifo decorativo isolato (``### »`` / ``- ›``): è il bullet di un box
        # promosso a heading dal renderer, non un titolo. Non porta testo.
        if _is_decor_line(line):
            continue
        if _CAPTION_HEADING_RE.match(line):
            line = re.sub(r"^#{1,6}\s*", "", line)  # didascalia, non heading
        m = _HEADING_RE.match(line)
        bm = _BOLD_ONLY_RE.match(line) if not m else None
        if (m or bm) and out:
            # Guarda l'ultima riga NON vuota: i titoli spezzati sono separati
            # da una riga vuota nell'output di pymupdf4llm.
            j = len(out) - 1
            while j >= 0 and out[j].strip() == "":
                j -= 1
            pm = _HEADING_RE.match(out[j]) if j >= 0 else None
            same_level = (len(pm.group(1)) == len(m.group(1))) if (pm and m) else True
            if pm and same_level:
                prev_text = pm.group(2).rstrip()
                cur_text = (m.group(2).strip() if m else bm.group("body").strip())
                # La continuazione di un titolo è riconoscibile quando:
                #  - inizia tra parentesi/in corsivo, oppure
                #  - la riga precedente finisce con una parola di raccordo
                #    ("... Therapy for" / "HYPOPARATHYROIDISM &"), oppure
                #  - la continuazione inizia minuscola, oppure
                #  - la precedente è un titolo "title case" lungo e la
                #    continuazione è un frammento breve in title case
                #    ("... Papillary Thyroid" + "Microcarcinoma").
                # NON si fondono titoli tutti-maiuscoli distinti
                # ("INTRODUCTION"/"METHODS", "TREATMENT"/"PROGNOSIS").
                dangling = (
                    _last_word(prev_text.strip().strip("*")) in _CONNECTORS
                    or prev_text.strip().strip("*").rstrip().endswith(
                        ("&", "/", "-", "–", "—"))
                )
                title_case_wrap = (
                    len(prev_text.split()) >= 4
                    and not prev_text.isupper()
                    and not cur_text.isupper()
                    and cur_text[:1].isupper()
                    and len(cur_text.split()) <= 4
                    # richiede un titolo numerato/sigle ("B. …", "1) …"):
                    # evita di fondere due titoli distinti ("General
                    # Considerations" + "Clinical Findings").
                    and bool(_ENUM_PREFIX_RE.match(prev_text))
                )
                # Titolo MAIUSCOLO spezzato: "... RELATED MOOD" + "DISORDERS".
                # Solo se la continuazione è un frammento brevissimo (≤2 parole)
                # e non è un'etichetta di sezione autonoma.
                allcaps_wrap = (
                    prev_text.isupper() and cur_text.isupper()
                    and len(prev_text.split()) >= 3
                    and len(cur_text.split()) <= 2
                    and _last_word(cur_text) not in _SECTION_STOP
                )
                # Una continuazione in grassetto non deve essere una didascalia.
                cur_is_caption = bool(
                    bm and _CAPTION_MARKER_RE.match(cur_text)
                )
                if (
                    (cur_text[:1] in "([" or dangling
                     or cur_text[:1].islower() or title_case_wrap or allcaps_wrap)
                    and len(cur_text) < 80
                    and not cur_is_caption
                    and not _CAPTION_HEADING_RE.match(line)
                    and not prev_text.endswith((".", "!", "?", ":", ";"))
                ):
                    out[j] = _join_heading(out[j], cur_text, inner_bold=bm is not None)
                    continue
        out.append(line)
    return "\n".join(out)


def _join_heading(prev_line: str, cur_text: str, inner_bold: bool = False) -> str:
    """Fonde due frammenti di titolo evitando ``**a** **b**`` ridondanti.

    ``inner_bold``: la continuazione era una riga in grassetto, quindi il suo
    testo va dentro i marcatori del titolo (``**… for**`` + ``**X**`` →
    ``**… for X**``).
    """
    tail = prev_line.rstrip()
    if tail.endswith("**") and inner_bold and "**" not in cur_text:
        return tail[:-2].rstrip() + " " + cur_text.strip() + "**"
    if tail.endswith("**") and cur_text.startswith("**") and cur_text.endswith("**"):
        # entrambi in grassetto: si tiene un solo paio di marcatori
        return tail[:-2].rstrip() + " " + cur_text[2:-2].strip() + "**"
    if tail.endswith("**") and cur_text.startswith("**"):
        return tail[:-2].rstrip() + " " + cur_text[2:].lstrip()
    return tail + " " + cur_text


#: Parole di raccordo: un titolo che finisce così è quasi sempre spezzato.
_CONNECTORS = frozenset((
    "for", "and", "of", "the", "with", "in", "on", "to", "a", "an", "or",
    "from", "by", "at", "as", "into", "using", "versus", "vs", "without",
    "after", "before", "during", "between", "including", "such",
))

#: Etichette di sezione che non sono mai la continuazione di un titolo: due
#: titoli MAIUSCOLI adiacenti con queste parole restano separati.
_SECTION_STOP = frozenset((
    "treatment", "prognosis", "introduction", "methods", "summary",
    "conclusion", "conclusions", "references", "further", "reading",
    "overview", "prevention", "definition", "epidemiology",
))

#: Prefisso di titolo numerato/sigla ("B. …", "1) …", "(a) …").
_ENUM_PREFIX_RE = re.compile(r"^\**\s*(?:\(?[A-Za-z0-9]{1,3}[.)]|\([a-z]\))\s")

#: Caratteri decorativi usati come bullet nei box (CMDT et al.).
_DECOR_CHARS = "ºª»«›‹◆◇►▶◀◄▪▫■□◻◼●○◦‣·•∙⋅…†‡§¶–—―➤➔➜✓✔✗✘★☆"
_DECOR_ONLY_RE = re.compile(r"^[" + re.escape(_DECOR_CHARS) + r"]+$")


def _is_decor_line(line: str) -> bool:
    """True per una riga fatta solo di glifi decorativi (nessun testo)."""
    s = _MD_PREFIX_RE.sub("", line).strip()
    return bool(s) and bool(_DECOR_ONLY_RE.match(s))


def _drop_decor_lines(md: str) -> str:
    """Toglie le righe fatte solo di bullet decorativi (``### »`` / ``- ›``).

    Sono i glifi che pymupdf4llm promuove a heading quando un box colorato ha
    un bullet come primo carattere: non portano testo, solo rumore.
    """
    return "\n".join(ln for ln in md.split("\n") if not _is_decor_line(ln))


#: Glifo decorativo in testa a una riga con testo (``»** Titolo**``).
_DECOR_LEAD_RE = re.compile(r"^\s*[" + re.escape(_DECOR_CHARS) + r"]+\s*")


def _strip_leading_decor(md: str) -> str:
    """Toglie un bullet decorativo in testa a una riga che ha comunque testo."""
    out: list[str] = []
    for ln in md.split("\n"):
        new = _DECOR_LEAD_RE.sub("", ln, count=1)
        out.append(new if new.strip() else ln)
    return "\n".join(out)


#: ``**MAIUSCOLO ...**`` seguito da prosa sulla stessa riga: il titolo
#: maiuscolo è un heading che pymupdf ha incollato al paragrafo.
_CAPS_FUSED_RE = re.compile(
    r"^\s*(?P<md>\*{1,3})(?P<label>[A-Z0-9][A-Z0-9 ,/&()’'.:\-]{4,})\*{1,3}"
    r"[ \t]+(?P<rest>\S.*)$"
)


def _normalize_caps_runins(md: str) -> str:
    """Separa i titoletti MAIUSCOLI fusi col paragrafo successivo.

    ``**HYPERTENSIVE DISORDERS OF PREGNANCY** The hypertensive …`` diventa un
    titolo su riga propria (mantenuto in grassetto, senza inventare un livello
    di heading) + paragrafo. Solo per label di **almeno due parole** tutte
    maiuscole: un run-in di una parola (``**TREATMENT** testo``) o una normale
    frase in grassetto (``**In pregnant women**, …``) non vengono toccati.
    Le didascalie numerate (``**TABLE 12-3** …``) sono escluse.
    """
    out: list[str] = []
    for line in md.split("\n"):
        if not line.lstrip().startswith("*") or _CAPTION_HEADING_RE.match(line):
            out.append(line)
            continue
        m = _CAPS_FUSED_RE.match(line)
        if not m:
            out.append(line)
            continue
        label, rest = m.group("label").rstrip(), m.group("rest")
        # Almeno 2 parole tutte maiuscole, label breve, e il residuo che inizia
        # con una maiuscola/cifra (una frase, non una continuazione minuscola).
        if (
            len(label.split()) < 2
            or len(label) > 70
            or re.search(r"[a-z]", label)
            or _CAPTION_MARKER_RE.match(label)
            or label.rstrip().endswith((",", ";", ":"))
            or not (rest[:1].isupper() or rest[:1].isdigit() or rest[:1] in "([")
        ):
            out.append(line)
            continue
        out.append(f"**{label}**")
        out.append("")
        out.append(rest)
    return "\n".join(out)


def _last_word(text: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z.'\-]*", text)
    return words[-1].lower().strip(".'-") if words else ""


def _repair_lists(md: str) -> str:
    """Converte i bullet inline (``• x``) in voci di lista.

    Non tocca le righe di tabella markdown (``| … |``): un ``•`` dentro una
    cella verrebbe trasformato in un a-capo e spezzerebbe la tabella. Non
    ricuce i bullet di continuazione: ``- a`` / ``- b`` sono indistinguibili da
    una continuazione, e fonderebbe liste legittime.
    """
    converted: list[str] = []
    for line in md.split("\n"):
        if line.lstrip().startswith("|"):
            converted.append(line)
        else:
            converted.append(re.sub(r"\s•\s", "\n- ", line))
    return "\n".join(converted)


def _drop_orphan_bullets(md: str) -> str:
    """Toglie gli item di elenco **vuoti** (``- `` senza testo).

    pymupdf4llm talvolta emette un bullet isolato tra due voci reali (artefatto
    di spaziatura): è rumore puro, non contenuto.
    """
    out: list[str] = []
    for ln in md.split("\n"):
        if re.fullmatch(r"\s*[-*+]\s*", ln):
            continue
        out.append(ln)
    return "\n".join(out)


def _normalize_emphasis(md: str) -> str:
    """Mette uno spazio dopo l'abbreviazione nei corsivi (``_S.pyogenes_``)."""
    md = re.sub(r"(?<=[_*])([A-Z])\.(?=[a-z])", r"\1. ", md)
    # ``** testo**`` (spazio dopo l'apertura del grassetto): non rende in
    # grassetto, si chiude lo spazio.
    return re.sub(r"(^|\s)\*\*\s+(?=\S)", r"\1**", md)


# ── figure (Pack 3) ─────────────────────────────────────────────────────────
# pymupdf4llm racchiude il testo dentro le figure (etichette di mappe/grafici)
# in un commento HTML: nel rendering Markdown sparisce. Lo si rende visibile
# come blocco citazione, così resta distinguibile dal corpo.

_PICTURE_TEXT_RE = re.compile(
    r"<!--\s*Start of picture text\s*-->(?P<body>.*?)"
    r"<!--\s*End of picture text\s*-->",
    re.DOTALL | re.IGNORECASE,
)


def _uncomment_picture_text_md(md: str) -> str:
    def _to_quote(m: "re.Match[str]") -> str:
        body = m.group("body").strip("\n").strip()
        if not body:
            return ""
        # I <br> di pymupdf sono a-capo di riga: meglio un blocco citazione
        # multi-riga che tag HTML letterali nel markdown.
        body = re.sub(r"<br\s*/?>", "\n", body, flags=re.IGNORECASE)
        return "\n".join(f"> {ln}" if ln.strip() else ">" for ln in body.split("\n"))

    return _PICTURE_TEXT_RE.sub(_to_quote, md)


def _apply_uncomment_picture_text(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    out = _uncomment_picture_text_md(md)
    if not is_fix_disabled("cleanup_despace"):
        out = _despace_blockquote_letters(out)
    return out


# ── gerarchia heading dal TOC (T5.3) ────────────────────────────────────────
# I livelli dei titoli nel markdown si deducono dalla dimensione del font e
# possono sbagliare. Il TOC del documento (`doc.get_toc()`) dà la gerarchia
# reale: se un titolo compare nel TOC alla pagina corrente, si allinea il
# numero di `#`. Conservativo: si toccano solo titoli già riconosciuti come
# heading o righe che combaciano *esattamente* con una voce del TOC.

def _toc_level_for(norm: str, wanted: dict[str, int]) -> int:
    if not norm:
        return 0
    if norm in wanted:
        return wanted[norm]
    for title, lvl in wanted.items():
        if len(title) >= 12 and (
            norm.startswith(title) or title.startswith(norm)
        ) and abs(len(title) - len(norm)) <= 40:
            return lvl
    return 0


def _toc_headings_md(md: str, page) -> str:
    doc = getattr(page, "parent", None)
    if doc is None:
        return md
    try:
        toc = doc.get_toc()
        pageno = page.number + 1
    except Exception:
        return md
    wanted: dict[str, int] = {}
    for entry in toc:
        if len(entry) < 3:
            continue
        level, title, pno = entry[0], entry[1], entry[2]
        if pno != pageno:
            continue
        norm = _norm_noise(title)
        if norm:
            wanted[norm] = max(1, min(6, int(level)))
    if not wanted:
        return md

    fenced: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        fenced.append(m.group(0))
        return f"\x00TFENCE{len(fenced) - 1}\x00"

    md = _FENCE_PROTECT_RE.sub(_stash, md)
    out: list[str] = []
    for line in md.split("\n"):
        m = _HEADING_RE.match(line)
        if m:
            lvl = _toc_level_for(_norm_noise(m.group(2)), wanted)
            if lvl:
                out.append("#" * lvl + " " + m.group(2).strip())
                continue
        elif not line.lstrip().startswith("|"):
            norm = _norm_noise(line)
            if norm in wanted and len(norm.split()) <= 15 and len(norm) <= 120:
                out.append("#" * wanted[norm] + " " + line.strip().strip("*").strip())
                continue
        out.append(line)
    md = "\n".join(out)
    for i, block in enumerate(fenced):
        md = md.replace(f"\x00TFENCE{i}\x00", block)
    return md


def _apply_toc_headings(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    return _toc_headings_md(md, page)


def _when_toc(p: LayoutProfile, b: str) -> bool:
    return p.has_toc


# ── tabelle (Pack 2) ────────────────────────────────────────────────────────
# Tre fix puri sul markdown + (per il rebuild) ``page.find_tables()``. Sono
# conservativi: se la struttura non è chiara non toccano nulla (meglio un
# difetto cosmetico che perdere contenuto).

#: Blocco tabella in markdown: riga d'intestazione, separatore, righe dati.
_MD_TABLE_RE = re.compile(
    r"^[ \t]*\|[^\n]*\|[ \t]*\n"
    r"[ \t]*\|[\s:|-]+\|[ \t]*\n"
    r"(?:[ \t]*\|[^\n]*\|[ \t]*(?:\n|$))+",
    re.MULTILINE,
)

#: Riga di didascalia di tabella, con eventuale ``#``/grassetto e numero.
#: Solo ``TABLE``: le didascalie delle figure (``**FIGURE 148-1 …**``) sono
#: frasi in grassetto e vanno lasciate intatte.
_TABLE_CAPTION_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?\**\s*"
    r"(?P<label>table)\s*(?P<num>[A-Za-z]?\d[\w.\-–]*)"
    r"\**\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

_FENCE_PROTECT_RE = re.compile(r"```.*?```", re.DOTALL)


def _split_md_row(line: str) -> list[str]:
    """Cells of a markdown table row (leading/trailing pipes stripped)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_separator_row(row: list[str]) -> bool:
    return bool(row) and all(re.fullmatch(r":?-{3,}:?", c or "") for c in row)


def _caption_from_block(block: str) -> str:
    """Caption text of a markdown table's first row, "" when there is none.

    Only *marker* rows count (``TABLE 2 …``): a plain single-cell first row of
    a table that is genuinely one column must not become a caption.
    """
    rows = [ln for ln in block.split("\n") if ln.strip()]
    if not rows:
        return ""
    cells = [c for c in _split_md_row(rows[0]) if c]
    text = re.sub(r"\s+", " ", " ".join(cells).replace("**", "")).strip()
    if _TABLE_CAPTION_RE.match(text):
        return text
    return ""


def _normalize_table_captions_md(md: str) -> str:
    """Force ``**TABLE x** …`` captions on their own line, never a heading.

    Only lines that *start* with a numbered marker become captions, and only
    when what follows is empty or starts like a title (uppercase/digit); a body
    sentence such as ``Table 2 shows that …`` is left untouched. Table rows and
    fenced code are skipped.
    """
    fenced: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        fenced.append(m.group(0))
        return f"\x00PFENCE{len(fenced) - 1}\x00"

    md = _FENCE_PROTECT_RE.sub(_stash, md)

    out: list[str] = []
    for line in md.split("\n"):
        if line.lstrip().startswith("|"):
            out.append(line)
            continue
        # pymupdf leaves control bytes (e.g. \x07) around box/bullet glyphs;
        # they break the caption match and are not text.
        clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", line)
        m = _TABLE_CAPTION_RE.match(clean)
        if m:
            label = f"{m.group('label').upper()} {m.group('num')}"
            rest = m.group("rest").strip().strip("*").strip()
            if rest[:1].isupper() or rest[:1].isdigit() or rest[:1] in "([—–:-":
                out.append(f"**{label}**" + (f" {rest}" if rest else ""))
                continue
        out.append(line)

    md = "\n".join(out)
    for i, block in enumerate(fenced):
        md = md.replace(f"\x00PFENCE{i}\x00", block)
    return md


#: Riga interamente in grassetto: una didascalia può essere spezzata su più
#: righe bold consecutive da pymupdf4llm.
_BOLD_ONLY_RE = re.compile(r"^\s*\*{1,3}(?P<body>.+?)\*{1,3}\s*$")

#: Marcatore di didascalia numerata dentro una riga (anche a metà riga).
_CAPTION_MARKER_RE = re.compile(
    r"\b(?P<label>table|tab\.?|fig(?:ure)?)\s*\.?\s*(?P<num>\d[\w.\-–]*)",
    re.IGNORECASE,
)


def _merge_caption_fragments_md(md: str) -> str:
    """Ricompone una didascalia spezzata su più righe in grassetto.

    pymupdf4llm a volte spacca il titolo di tabella/figura su righe bold
    consecutive, con pezzi ridondanti ("MEDICATIONS TO AVOID IN WOMEN OF" /
    "CHILDBEARING AGE CONSIDERING" / "PREGNANCY" / "TABLE 221-3" / di nuovo il
    titolo intero). Si fondono **solo** gruppi di righe interamente in grassetto
    che contengono *un solo* marcatore di didascalia: il marcatore va in testa
    e i frammenti che sono sotto-stringhe di un altro vengono scartati. Nessun
    testo è inventato: si riusa quello presente.
    """
    lines = md.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if not _BOLD_ONLY_RE.match(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        group: list[tuple[int, str]] = []
        j = i
        markers: list[tuple[str, str]] = []
        while j < n and len(group) < 8:
            if not lines[j].strip():
                j += 1
                continue
            bm = _BOLD_ONLY_RE.match(lines[j])
            if not bm:
                break
            body = bm.group("body").strip()
            merged = list(markers)
            for mm in _CAPTION_MARKER_RE.finditer(body):
                mk = (mm.group("label").lower().rstrip("."), mm.group("num"))
                if mk not in merged:
                    merged.append(mk)
            if len(merged) > 1:
                break  # un secondo marcatore: qui inizia un'altra didascalia
            markers = merged
            group.append((j, body))
            j += 1
            # Una didascalia con marcatore che termina con una frase/source
            # completa ("… (Adapted from …)") chiude il gruppo: le righe bold
            # successive appartengono a un'altra didascalia.
            if (
                markers
                and len(group) >= 2
                and len(body.split()) >= 4
                and body.rstrip().endswith((".", ")", ":"))
            ):
                break
        end = group[-1][0] if group else i
        if len(group) < 2 or len(markers) != 1:
            out.append(lines[i])
            i += 1
            continue
        label, num = markers[0]
        label = label.upper()  # "figure" -> "FIGURE" (la didascalia resta cercabile)
        frags: list[str] = []
        for _, body in group:
            cleaned = _CAPTION_MARKER_RE.sub("", body, count=1)
            cleaned = re.sub(r"\*+", "", cleaned).strip(" \t-–—:.")
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                frags.append(cleaned)
        kept: list[str] = []
        norms = [_norm_noise(f) for f in frags]
        for f, nf in zip(frags, norms):
            if any(nf and nf != other and nf in other for other in norms):
                continue  # frammento ridondante, incluso in un altro più completo
            if nf and nf not in {_norm_noise(k) for k in kept}:
                kept.append(f)
        body = " ".join(kept).strip()
        # Un gruppo di due sole righe in cui il titolo è di una parola è
        # probabilmente il marcatore seguito da un titolo autonomo, non una
        # didascalia spezzata: non si fonde.
        if len(group) < 3 and len(body.split()) < 3:
            out.append(lines[i])
            i += 1
            continue
        out.append(f"**{label} {num}**" + (f" {body}" if body else ""))
        i = end + 1
    return "\n".join(out)


def _clean_table_md(block: str) -> str:
    """Drop empty columns/trailing cells and realign a numeric last column.

    Conservative: a column is removed only when it is empty in **every** row
    (header included), so no text can be lost. Trailing empty cells are dropped
    because markdown rows may be shorter than the header; ``||`` runs are thus
    gone. Finally, when the last column is numeric in the rows that do have a
    value, a value that pymupdf merged into the previous cell (a trailing
    ``+30``/``-10`` token) is moved back into that column.
    """
    rows = [ln for ln in block.split("\n") if ln.strip()]
    if len(rows) < 2:
        return block
    parsed = [_split_md_row(r) for r in rows]
    ncols = max(len(r) for r in parsed)
    header_len = len(parsed[0])
    parsed = [r + [""] * (ncols - len(r)) for r in parsed]
    # Intestazione più corta delle righe dati: pymupdf ha perso la cella vuota
    # della prima colonna (colonna-etichetta, es. TABLE 221-5 con header a 4
    # celle e righe a 5). Ripristino in TESTA, dove stava, così le colonne
    # restano allineate. Con un header "fuso" (una sola cella su molte colonne)
    # si ripiega sul padding in coda, che è la lettura giusta in quel caso.
    if header_len >= 2 and ncols > header_len:
        parsed[0] = [""] * (ncols - header_len) + parsed[0][:header_len]
    if ncols >= 2:
        parsed = _realign_numeric_column(parsed)
    keep = [c for c in range(ncols) if any(r[c] for r in parsed)]
    if not keep:
        return block
    out: list[str] = []
    for row in parsed:
        if _is_separator_row(row):
            out.append("| " + " | ".join("---" for _ in keep) + " |")
            continue
        cells = [row[c] for c in keep]
        while len(cells) > 1 and not cells[-1]:
            cells.pop()
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


#: Valore numerico "spostabile": segno esplicito + cifre (es. "+30", "-10").
_NUMERIC_CELL_RE = re.compile(r"^[+\-]\s?\d+(?:[.,]\d+)?$")
_TRAILING_NUMERIC_RE = re.compile(r"^(.*?)\s+([+\-]\s?\d+(?:[.,]\d+)?)$")


def _realign_numeric_column(parsed: list[list[str]]) -> list[list[str]]:
    """Move a ``+30``/``-10`` merged into the previous cell back to the last column.

    Only when the last column is numeric in most rows that have a value there
    (learned from the well-formed rows) and the moved token carries an explicit
    sign — so a plain ``… in years10`` inside the text is left alone. Never
    loses text: the token is moved, not dropped.
    """
    last = len(parsed[0]) - 1
    data_rows = [r for i, r in enumerate(parsed) if i > 0 and not _is_separator_row(r)]
    filled = [r[last] for r in data_rows if r[last]]
    if len(filled) < 2:
        return parsed
    numeric = sum(bool(_NUMERIC_CELL_RE.match(v)) for v in filled)
    if numeric < 0.6 * len(filled):
        return parsed
    for row in parsed:
        if _is_separator_row(row) or row[last]:
            continue
        m = _TRAILING_NUMERIC_RE.match(row[last - 1])
        if m and m.group(1).strip():
            row[last - 1] = m.group(1).strip()
            row[last] = m.group(2).replace(" ", "")
    return parsed


def _fix_empty_cells_md(md: str) -> str:
    return _MD_TABLE_RE.sub(lambda m: _clean_table_md(m.group(0)), md)


def _table_needs_rebuild(block: str) -> bool:
    """True when pymupdf4llm's table is malformed enough to rebuild it.

    Rebuilding a table that pymupdf4llm rendered fine can *degrade* it (e.g.
    find_tables merges the header row into the first data row and the header is
    lost — co23/p301 TABLE 4). So the grid is rebuilt only when the markdown
    shows a real defect: an adjacent empty cell (``||``) or rows with a
    different number of cells (caption glued in, columns truncated).
    """
    rows = [ln for ln in block.split("\n") if ln.strip()]
    if len(rows) < 2:
        return False
    if any(re.search(r"\|\s*\|", r) for r in rows):
        return True
    return len({len(_split_md_row(r)) for r in rows}) > 1


def _rebuild_tables_md(md: str, page, exclude: Sequence[tuple] = ()) -> str:
    """Replace malformed markdown tables with grids rebuilt from ``find_tables``.

    Only when the number of markdown tables matches the number of data tables
    detected on the page (same reading order): any mismatch means we cannot map
    them safely, so the text is returned unchanged. Tables that are already
    well-formed are left untouched. A caption that lived in the original block
    is re-attached if the rebuilt grid does not already carry one
    (``find_tables`` sometimes starts below the caption row).
    """
    from main import _table_to_md  # lazy: evita import circolare

    if page is None:
        return md
    tables = _data_tables(page, exclude)
    if not tables:
        return md
    # find_tables() order is not guaranteed top-to-bottom; the markdown tables
    # are in reading order, so sort the page tables the same way before zipping
    # (otherwise a caption ends up attached to the wrong grid).
    tables = sorted(tables, key=lambda t: (t.bbox[1], t.bbox[0]))
    matches = list(_MD_TABLE_RE.finditer(md))
    if len(matches) != len(tables):
        return md

    out: list[str] = []
    last = 0
    for m, table in zip(matches, tables):
        block = m.group(0)
        out.append(md[last:m.start()])
        last = m.end()
        if not _table_needs_rebuild(block):
            out.append(block)  # pymupdf4llm's table is fine: keep it
            continue
        rebuilt = _table_to_md(page, table)
        if not rebuilt:
            out.append(block)
            continue
        caption = _caption_from_block(block)
        if caption and not rebuilt.lstrip().startswith("**"):
            rebuilt = f"**{caption}**\n\n{rebuilt}"
        out.append(rebuilt)
    out.append(md[last:])
    return "".join(out)


def _apply_rebuild_tables(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    # Su una pagina ruotata (landscape a 90°) find_tables/get_textbox mappano le
    # celle in coordinate sbagliate e la ricostruzione *peggiora* la tabella:
    # l'estrazione ruotata di pymupdf4llm è già corretta, quindi non si tocca.
    if page is not None and getattr(page, "rotation", 0):
        return md
    return _rebuild_tables_md(md, page, exclude)


def _apply_normalize_captions(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    return _normalize_table_captions_md(md)


def _apply_fix_empty_cells(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    return _fix_empty_cells_md(md)


def _when_has_tables(p: LayoutProfile, b: str) -> bool:
    return p.has_tables


def _when_rebuild_tables(p: LayoutProfile, b: str) -> bool:
    """Rebuild only when the reorder did **not** already rebuild the page.

    On a two-column overlapping layout ``reorder_columns`` regenerates the
    whole page (tables included) from ``find_tables``; running the rebuild
    again on that markdown can pair a caption with the wrong grid when the
    reading order differs from the page's top-to-bottom order. The text-level
    fixes (captions, empty cells) stay active everywhere.
    """
    return p.has_tables and not (p.columns >= 2 and p.columns_overlap)


#: ``### `` senza testo seguito da una riga in grassetto: il titolo è rimasto
#: vuoto e il testo è finito nella riga sotto (``### `` + ``**FURTHER READING**``).
_EMPTY_HEADING_RE = re.compile(r"(?m)^(#{1,6})[ \t]*\n+(?=[ \t]*\*\*)")


def _join_empty_headings(md: str) -> str:
    return _EMPTY_HEADING_RE.sub(r"\1 ", md)


_NUM_HEADING_RE = re.compile(r"^(#{1,6})[ \t]*(\*\*\d{1,3}\*\*\s*[:.)].*)$")


def _demote_numbered_headings(md: str) -> str:
    """Un heading che è una voce numerata ("### **3**: …") torna paragrafo.

    Il riordino può promuovere a titolo la prima riga di una lista numerata.
    Qui si toglie il ``#`` e si ricuce l'eventuale frase avvolta nella riga
    successiva (che riprende minuscola).
    """
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = _NUM_HEADING_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        cur = m.group(2)
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if (j < len(lines) and lines[j][:1].islower()
                and not lines[j].lstrip()[:1] in "#->|*"):
            cur = cur.rstrip() + " " + lines[j].strip()
            lines[j] = ""
        out.append(cur)
        i += 1
    return "\n".join(out)


#: Titolo in grassetto incollato in coda a una riga di testo:
#: "... heel stick. **Abnormal findings**" → la riga va spezzata.
_TRAILING_BOLD_RE = re.compile(r"(?m)^(?P<pre>.*\S)[ \t]+(?P<b>\*\*[^*\n]{2,50}\*\*)[ \t]*$")
_TITLE_CASE_RE = re.compile(r"[A-Z][A-Za-z0-9&/\-]*(?: [A-Z][A-Za-z0-9&/\-]*)*")
_ALLCAPS_RE = re.compile(r"[A-Z0-9 &/\-]{3,}")


def _split_trailing_bold_heading(md: str) -> str:
    """Separa un titolo bold incollato in coda a una frase.

    Conservativo: agisce solo se il grassetto sembra un titolo (Title Case o
    maiuscolo), è corto (≤5 parole), la frase prima ha ≥6 parole e termina con
    punteggiatura di fine periodo. Non rimuove nulla: cambia solo l'a-capo.
    """
    def _sub(m: "re.Match[str]") -> str:
        pre, b = m.group("pre"), m.group("b")
        inner = b[2:-2].strip()
        if len(inner.split()) > 5 or len(pre.split()) < 6:
            return m.group(0)
        if not pre.rstrip().endswith((".", ":", "!", "?")):
            return m.group(0)
        # titolo plausibile: inizia con maiuscola, non è una frase (niente punto)
        if not inner[:1].isupper() or inner.endswith((".", ":", "!", "?")):
            return m.group(0)
        if not (_TITLE_CASE_RE.fullmatch(inner) or _ALLCAPS_RE.fullmatch(inner)
                or inner[:1].isupper()):
            return m.group(0)
        return f"{pre}\n\n{b}"

    return _TRAILING_BOLD_RE.sub(_sub, md)


#: Blocchi di codice fenced: il cleanup non deve toccarne il contenuto.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _apply_cleanup(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    if not is_fix_disabled("cleanup_html_tags"):
        md = _normalize_html_tags(md)
    if not is_fix_disabled("cleanup_fffd"):
        md = _normalize_replacement_chars(md)
    if not is_fix_disabled("cleanup_soft_hyphens"):
        md = _normalize_soft_hyphens(md)
    md = _strip_running_headers(md, page)

    # Protegge i blocchi di codice: dentro un fence `#`, `~~`, `•` sono letterali.
    fenced: list[str] = []

    def _stash(match: "re.Match[str]") -> str:
        fenced.append(match.group(0))
        return f"\x00FENCE{len(fenced) - 1}\x00"

    md = _FENCE_RE.sub(_stash, md)
    if not is_fix_disabled("cleanup_glyph_lines"):
        md = _drop_decor_lines(md)
        md = _strip_leading_decor(md)
    md = _join_empty_headings(md)
    md = _normalize_headings(md)
    if not is_fix_disabled("cleanup_numbered_headings"):
        md = _demote_numbered_headings(md)
    if not is_fix_disabled("cleanup_split_bold_heading"):
        md = _split_trailing_bold_heading(md)
    if not is_fix_disabled("cleanup_caps_runins"):
        md = _normalize_caps_runins(md)
    if not is_fix_disabled("cleanup_caption_fragments"):
        md = _merge_caption_fragments_md(md)
    md = _repair_lists(md)
    md = _normalize_emphasis(md)
    # De-spacing per ultimo: `_normalize_headings` può unire righe spezzate dal
    # font e produrre i numeri "spaziati" ("30-60" -> "3 0-6 0").
    if not is_fix_disabled("cleanup_despace"):
        md = _despace_numbers(md)
        md = _despace_blockquote_letters(md)
    for i, block in enumerate(fenced):
        md = md.replace(f"\x00FENCE{i}\x00", block)
    return md


def _when_dehyphenate(p: LayoutProfile, b: str) -> bool:
    return b == "Docling 🧠"


def _when_split_glued(p: LayoutProfile, b: str) -> bool:
    return b == "Docling 🧠" and p.columns >= 2


def _when_reorder_columns(p: LayoutProfile, b: str) -> bool:
    return b != "Docling 🧠" and p.columns >= 2 and p.columns_overlap


def _when_spacing(p: LayoutProfile, b: str) -> bool:
    return True


FIX_REGISTRY: Sequence[Fix] = (
    Fix(
        "dehyphenate",
        "De-sillabazione a fine riga + pulizia (backend Docling)",
        10,
        _when_dehyphenate,
        _apply_dehyphenate,
    ),
    Fix(
        "split_glued",
        "Ri-spezza i paragrafi incollati a cavallo colonne (Docling)",
        20,
        _when_split_glued,
        _apply_split_glued,
    ),
    Fix(
        "reorder_columns",
        "Riordino N colonne + tabelle in ordine di lettura",
        30,
        _when_reorder_columns,
        _apply_reorder_columns,
    ),
    Fix(
        "cleanup_markdown",
        "Pulizia: header/footer di stampa, heading, liste, corsivi",
        50,
        lambda p, b: True,
        _apply_cleanup,
    ),
    Fix(
        "uncomment_picture_text",
        "Figure: rende visibile il testo dentro le figure (era un commento HTML)",
        55,
        lambda p, b: True,
        _apply_uncomment_picture_text,
    ),
    Fix(
        "toc_headings",
        "Gerarchia heading allineata al TOC del documento",
        58,
        _when_toc,
        _apply_toc_headings,
    ),
    Fix(
        "rebuild_tables",
        "Tabelle: griglia ricostruita da find_tables al posto di pymupdf4llm",
        60,
        _when_rebuild_tables,
        _apply_rebuild_tables,
    ),
    Fix(
        "normalize_table_captions",
        "Tabelle: didascalie **TABLE x** … su riga propria (mai heading)",
        62,
        _when_has_tables,
        _apply_normalize_captions,
    ),
    Fix(
        "fix_empty_cells",
        "Tabelle: via le celle vuote adiacenti, colonne fantasma rimosse",
        64,
        _when_has_tables,
        _apply_fix_empty_cells,
    ),
    Fix(
        "spacing",
        "Correzioni cosmetiche di spaziatura al markdown",
        90,
        _when_spacing,
        _apply_spacing,
    ),
)

_FIX_BY_ID: dict[str, Fix] = {f.id: f for f in FIX_REGISTRY}

# ─────────────────────────────────────────────────────────────────────────────
#  override utente (fix_rules.json, opzionale)
# ─────────────────────────────────────────────────────────────────────────────

_OVERRIDE_PATH = Path(__file__).with_name("fix_rules.json")


@lru_cache(maxsize=8)
def _load_overrides(path: str) -> dict:
    """Legge fix_rules.json (se presente) e lo valida in modo conservativo."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict = {}
    if isinstance(data.get("disable"), list):
        out["disable"] = [str(x) for x in data["disable"] if isinstance(x, str)]
    if isinstance(data.get("rules"), list):
        out["rules"] = data["rules"]
    return out


_OPS = {"eq": lambda a, b: a == b, "gte": lambda a, b: a >= b, "lte": lambda a, b: a <= b}


def is_fix_disabled(fix_id: str) -> bool:
    """True when ``fix_id`` is listed in ``fix_rules.json`` → ``disable``."""
    try:
        return fix_id in set(_load_overrides(str(_OVERRIDE_PATH)).get("disable", []))
    except Exception:
        return False


def _rule_matches(when: dict, profile: LayoutProfile, backend: str) -> bool:
    """Valuta un predicato ``when`` limitato a campi noti del profilo/backend."""
    for field, cond in when.items():
        if field not in ("columns", "backend", "has_tables", "has_index"):
            return False  # campo sconosciuto → regola non applicabile
        val = backend if field == "backend" else getattr(profile, field, None)
        if isinstance(cond, dict):
            for op, target in cond.items():
                fn = _OPS.get(op)
                if fn is None or not fn(val, target):
                    return False
        elif val != cond:
            return False
    return True


def _apply_custom_rules(
    base_plan: list[Fix], rules: list, profile: LayoutProfile, backend: str
) -> list[Fix]:
    """Le regole personalizzate SOSTITUISCONO il piano default quando combaciano."""
    result: list[Fix] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if not _rule_matches(rule.get("when", {}), profile, backend):
            continue
        for fid in rule.get("apply", []):
            f = _FIX_BY_ID.get(fid)
            if f and f not in result:
                result.append(f)
    return result or base_plan


# ─────────────────────────────────────────────────────────────────────────────
#  scheduler + pipeline
# ─────────────────────────────────────────────────────────────────────────────

def plan_fixes(
    profile: LayoutProfile,
    backend: str,
    mode: str = "auto",
    overrides: dict | None = None,
) -> list[Fix]:
    """Ordina i fix del registro i cui ``when`` sono veri.

    ``mode``: ``"auto"`` (piano automatico) oppure l'``id`` di un singolo fix
    (per il combo manuale: quel fix viene applicato a prescindere da ``when``).

    ``overrides``: dict opzionale (default: letto da ``fix_rules.json``).
    """
    if overrides is None:
        overrides = _load_overrides(str(_OVERRIDE_PATH))
    disabled = set(overrides.get("disable", []))

    by_id = _FIX_BY_ID
    if mode != "auto":
        f = by_id.get(mode)
        return [f] if f and f.id not in disabled else []

    plan = [
        f for f in FIX_REGISTRY
        if f.when(profile, backend) and f.id not in disabled
    ]
    rules = overrides.get("rules", [])
    if rules:
        plan = _apply_custom_rules(list(plan), rules, profile, backend)
    return sorted(plan, key=lambda f: f.order)


def apply_plan(
    md: str,
    page,
    profile: LayoutProfile,
    plan: Sequence[Fix],
    exclude: Sequence[tuple] = (),
) -> str:
    """Applica i fix in ordine: md = fix.apply(md, page, profile, exclude)."""
    for fix in plan:
        try:
            out = fix.apply(md, page, profile, exclude)
        except Exception:
            continue  # un fix che fallisce non blocca i successivi
        if out:
            md = out
    return md
