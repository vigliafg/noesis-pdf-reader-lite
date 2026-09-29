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


def _body_blocks(page, exclude: Sequence[tuple] = ()) -> tuple[list[tuple], list[dict]]:
    """Return (table_regions, body_blocks) come in ``_column_aware_markdown``."""
    from main import _collect_blocks  # lazy: evita import circolare

    table_regions: list[tuple] = []
    for t in _data_tables(page, exclude):
        table_regions.append(tuple(t.bbox))

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


def _apply_reorder_columns(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    from main import _column_aware_markdown
    return _column_aware_markdown(page, move_title=False, exclude=exclude) or md


def _apply_reorder_columns_title(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    from main import _column_aware_markdown
    return _column_aware_markdown(page, move_title=True, exclude=exclude) or md


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
_STANDALONE_NUM_RE = re.compile(r"^\s*\**\s*\d{1,4}\s*\**\s*$")


def _norm_noise(text: str) -> str:
    """Normalizza una riga per confrontare md e testo di pagina (header/footer)."""
    return re.sub(r"\s+", " ", re.sub(r"[*_`~]", "", text)).strip().lower()


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


def _iter_margin_lines(page):
    """Yield ``(text, x0, x1, y1)`` for the lines in the top/bottom margin band."""
    try:
        height = page.rect.height
        blocks = page.get_text("dict")["blocks"]
    except Exception:
        return
    top, bottom = 0.08 * height, 0.92 * height
    for blk in blocks:
        if blk.get("type") != 0:
            continue
        for line in blk["lines"]:
            y0, y1 = line["bbox"][1], line["bbox"][3]
            if y1 <= top or y0 >= bottom:
                yield (
                    "".join(s["text"] for s in line["spans"]),
                    line["bbox"][0], line["bbox"][2], y1,
                )


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
        if norm and len(norm) <= 120 and len(norm.split()) <= 15:
            found.add(norm)
    # Frequenza tra pagine: un header/footer che ricorre su più pagine è chrome
    # di pagina anche quando la singola pagina non lo riconosce (T5.1).
    found |= _document_noise(page)
    return found


def _is_noise_line(line: str, candidates: set[str]) -> bool:
    """True se la riga è (o contiene solo) un header/footer di stampa."""
    if not candidates or len(line) >= 200:
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
    return False


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


def _normalize_headings(md: str) -> str:
    """Unisce titoli spezzati, elimina artefatti, retrocede le didascalie."""
    md = md.replace("~~", "").replace("■", "")
    lines = md.split("\n")
    out: list[str] = []
    for line in lines:
        if _CAPTION_HEADING_RE.match(line):
            line = re.sub(r"^#{1,6}\s*", "", line)  # didascalia, non heading
        m = _HEADING_RE.match(line)
        if m and out:
            # Guarda l'ultima riga NON vuota: i titoli spezzati sono separati
            # da una riga vuota nell'output di pymupdf4llm.
            j = len(out) - 1
            while j >= 0 and out[j].strip() == "":
                j -= 1
            pm = _HEADING_RE.match(out[j]) if j >= 0 else None
            if pm and len(pm.group(1)) == len(m.group(1)):
                prev_text = pm.group(2).rstrip()
                cur_text = m.group(2).strip()
                # Solo un titolo che riprende tra parentesi è chiaramente la
                # continuazione di quello precedente (es. "ABIOTROPHIA AND" +
                # "(NUTRITIONALLY VARIANT …)"). Niente euristica ALL-CAPS:
                # fonderebbe titoli legittimi come "INTRODUCTION"/"METHODS".
                starts_like_continuation = cur_text[:1] in "(["
                if (
                    starts_like_continuation
                    and len(cur_text) < 80
                    and not prev_text.endswith((".", "!", "?", ":", ";"))
                ):
                    out[j] = out[j].rstrip() + " " + cur_text
                    continue
        out.append(line)
    return "\n".join(out)


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


def _normalize_emphasis(md: str) -> str:
    """Mette uno spazio dopo l'abbreviazione nei corsivi (``_S.pyogenes_``)."""
    return re.sub(r"(?<=[_*])([A-Z])\.(?=[a-z])", r"\1. ", md)


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
        return "\n".join(f"> {ln}" if ln.strip() else ">" for ln in body.split("\n"))

    return _PICTURE_TEXT_RE.sub(_to_quote, md)


def _apply_uncomment_picture_text(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    return _uncomment_picture_text_md(md)


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
    r"(?P<label>table)\s*(?P<num>\d[\w.\-–]*)"
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
    parsed = [r + [""] * (ncols - len(r)) for r in parsed]
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


#: Blocchi di codice fenced: il cleanup non deve toccarne il contenuto.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _apply_cleanup(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    md = _strip_running_headers(md, page)

    # Protegge i blocchi di codice: dentro un fence `#`, `~~`, `•` sono letterali.
    fenced: list[str] = []

    def _stash(match: "re.Match[str]") -> str:
        fenced.append(match.group(0))
        return f"\x00FENCE{len(fenced) - 1}\x00"

    md = _FENCE_RE.sub(_stash, md)
    md = _normalize_headings(md)
    md = _repair_lists(md)
    md = _normalize_emphasis(md)
    for i, block in enumerate(fenced):
        md = md.replace(f"\x00FENCE{i}\x00", block)
    return md


def _when_dehyphenate(p: LayoutProfile, b: str) -> bool:
    return b == "Docling 🧠"


def _when_split_glued(p: LayoutProfile, b: str) -> bool:
    return b == "Docling 🧠" and p.columns >= 2


def _when_reorder_columns(p: LayoutProfile, b: str) -> bool:
    return b != "Docling 🧠" and p.columns >= 2 and p.columns_overlap


def _when_reorder_columns_title(p: LayoutProfile, b: str) -> bool:
    # Solo manuale: lo scheduler "auto" non lo propone mai da solo.
    return False


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
        "reorder_columns_title",
        "Come sopra + titolo del capitolo in testa (solo manuale)",
        35,
        _when_reorder_columns_title,
        _apply_reorder_columns_title,
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
