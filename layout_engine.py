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
import re
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


def _margin_noise(page) -> set[str]:
    """Stringhe che compaiono nei margini alto/basso della pagina (header/footer)."""
    if page is None:
        return set()
    try:
        height = page.rect.height
        blocks = page.get_text("dict")["blocks"]
    except Exception:
        return set()
    top, bottom = 0.08 * height, 0.92 * height
    found: set[str] = set()
    for blk in blocks:
        if blk.get("type") != 0:
            continue
        for line in blk["lines"]:
            y0, y1 = line["bbox"][1], line["bbox"][3]
            if y1 <= top or y0 >= bottom:
                text = "".join(s["text"] for s in line["spans"])
                norm = _norm_noise(text)
                if norm:
                    found.add(norm)
    return found


def _strip_running_headers(md: str, page) -> str:
    """Rimuove header/footer di stampa e numeri di pagina isolati."""
    candidates = _margin_noise(page)
    lines = md.split("\n")
    kept: list[str] = []
    for line in lines:
        norm = _norm_noise(line)
        if norm and norm in candidates and len(line) < 200:
            continue
        kept.append(line)
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
                starts_like_continuation = (
                    cur_text[:1] in "([" or (cur_text and cur_text.isupper())
                )
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
    """Converte i bullet inline e ricuce i bullet di continuazione."""
    md = re.sub(r"\s•\s", "\n- ", md)
    lines = md.split("\n")
    out: list[str] = []
    for line in lines:
        if line.startswith("- ") and out:
            j = len(out) - 1
            while j >= 0 and out[j].strip() == "":
                j -= 1
            if j >= 0 and out[j].startswith("- "):
                prev = out[j].rstrip()
                rest = line[2:].lstrip()
                if (
                    rest[:1].islower()
                    and not prev.endswith((".", "!", "?", ":", ";"))
                ):
                    out[j] = prev + " " + rest
                    continue
        out.append(line)
    return "\n".join(out)


def _normalize_emphasis(md: str) -> str:
    """Mette uno spazio dopo l'abbreviazione nei corsivi (``_S.pyogenes_``)."""
    return re.sub(r"(?<=[_*])([A-Z])\.(?=[a-z])", r"\1. ", md)


def _apply_cleanup(md: str, page, profile: LayoutProfile, exclude: Sequence[tuple] = ()) -> str:
    md = _strip_running_headers(md, page)
    md = _normalize_headings(md)
    md = _repair_lists(md)
    md = _normalize_emphasis(md)
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
