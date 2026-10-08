"""Reconstructed automatic checklist for the page↔markdown arbiter.

The original harness scripts were lost in a machine restart (see PIANO §17 /
HANDOFF Pack 7).  This module is a faithful-but-simplified rebuild of the
detectors documented in PIANO §14–§16.  The **authoritative** judge remains the
visual arbitration; these flags only point at candidate defects, and the score
is indicative (it will NOT reproduce the lost harness numbers exactly).
"""
from __future__ import annotations

import re

# --- text-level patterns ----------------------------------------------------
_FFFD = re.compile("\ufffd")
_HTML_TAG = re.compile(
    r"</?(?:mark|sup|sub|br|hr|span|div|b|i|u|em|strong|font|a|p|table|tr|td|th|img)\b[^>]*>",
    re.I)
_FIG_CAPTION = re.compile(
    r"(?im)^[\s>#*|_-]*(?:e[-\s]?)?fig(?:ure|\.)?\s*[A-Z]?\d")
_IMG_LINK = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_GLUE = re.compile(r"[a-z]{2,}[A-Z][a-z]")           # "sysTem", "skIlls"
_CAPS_MERGED = re.compile(r"\b[A-Z]{3,}[a-z]{1,2}[A-Z]{2,}\b")
_EMPTY_HEADING = re.compile(r"(?m)^#{1,6}\s*$")
_PAGE_NUM_LINE = re.compile(
    r"(?m)^(?:>\s*)?(?:\*\*)?\d{1,4}(?:\s+(?:Section|CHAPTER|Chapter)\b.*)?(?:\*\*)?\s*$")
_HEADING = re.compile(r"(?m)^#{1,6}\s+")
_TABLE_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_BOLD_LINE = re.compile(r"^\s*(?:>\s*)?\*\*[^*]+\*\*\s*$")
_SENT_END = re.compile(r"[.:;!?)]\s*$")


def _count(rx: re.Pattern, text: str) -> int:
    return len(rx.findall(text or ""))


def table_misalign(md: str) -> int:
    """Rows whose cell count differs from the header row of the same table."""
    bad = 0
    lines = (md or "").splitlines()
    i = 0
    while i < len(lines):
        if (lines[i].strip().startswith("|") and i + 1 < len(lines)
                and _TABLE_SEP.match(lines[i + 1])):
            width = lines[i].count("|")
            j = i + 2
            while j < len(lines) and lines[j].strip().startswith("|"):
                if lines[j].count("|") != width:
                    bad += 1
                j += 1
            i = j
        else:
            i += 1
    return bad


def caption_fragment(md: str) -> int:
    """Proxy: a bold-only short line that is NOT a heading (labels/fragments)."""
    n = 0
    for line in (md or "").splitlines():
        s = line.strip()
        if not s or _HEADING.match(line):
            continue
        if _BOLD_LINE.match(s) and len(s.split()) <= 4 and not _SENT_END.search(s):
            n += 1
    return n


def heading_glued(md: str) -> int:
    """Heading lines that end without punctuation and continue the text."""
    return _count(re.compile(r"(?m)^#{1,6}\s+\S.*[a-z]$"), md)


# --- image-level (import **lazy**: pymupdf + numpy) -------------------------
#: sotto questa deviazione standard dei pixel (0..255) l'immagine è "piatta"
#: (bianca/uniforme): una figura così è un **artefatto di rendering**, non
#: contenuto. Cattura la picture CMYK fuori pagina (es. `su19 p1050`).
IMG_BLANK_STD = 3.0
#: alternativa: quasi tutti i pixel al massimo (bianco) → vuota.
IMG_WHITE_MIN = 0.995


def _img_type(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:2] == b"BM":
        return "bmp"
    return None


def image_stats(data: bytes) -> dict | None:
    """Statistiche di un'immagine ``bytes`` (lazy pymupdf+numpy); ``None`` se
    indecifrabile. ``blank`` = piatta/bianca (artefatto di rendering)."""
    ft = _img_type(data)
    if not ft:
        return None
    try:
        import numpy as np
        import pymupdf

        d = pymupdf.open(stream=data, filetype=ft)
        try:
            pix = d[0].get_pixmap()
        finally:
            d.close()
        arr = np.frombuffer(pix.samples, dtype=np.uint8).astype(np.float32)
        if arr.size == 0:
            return {"w": pix.width, "h": pix.height, "mean": 0.0, "std": 0.0,
                    "white": 1.0, "blank": True}
        std = float(arr.std())
        white = float((arr >= 250).mean())
        return {"w": pix.width, "h": pix.height, "mean": float(arr.mean()),
                "std": std, "white": white,
                "blank": std < IMG_BLANK_STD or white >= IMG_WHITE_MIN}
    except Exception:
        return None


def figure_render(images: list[bytes]) -> dict:
    """Riepilogo della resa delle figure embedded: quante sono **vuote**.

    ``{"n": n, "blank": n_blank, "blank_idx": [...], "stats": [...]}``.
    """
    stats = [image_stats(b) for b in images]
    blank_idx = [i for i, s in enumerate(stats) if s and s.get("blank")]
    return {"n": len(images), "blank": len(blank_idx), "blank_idx": blank_idx,
            "stats": [s for s in stats if s]}


def duplicate_lines(md: str, min_words: int = 8) -> int:
    """Numero di righe lunghe **ripetute** (≥ ``min_words`` parole), case/space
    normalizzati. Proxy della duplicazione (prosa+testo-figura, caption doppia).
    """
    from collections import Counter

    seen: Counter = Counter()
    for ln in (md or "").splitlines():
        s = re.sub(r"\s+", " ", ln).strip(" \t|*_>`#-").lower()
        if len(s.split()) >= min_words:
            seen[s] += 1
    return sum(c - 1 for c in seen.values() if c > 1)


def analyze(raw: str, md: str, key: str = "") -> dict:
    md = md or ""
    raw = raw or ""
    n_html = _count(_HTML_TAG, md)
    n_fffd = _count(_FFFD, md)
    n_fig_cap = _count(_FIG_CAPTION, md)
    n_links = _count(_IMG_LINK, md)
    fig_missing = 1 if (n_fig_cap and n_links == 0) else 0
    counts = {
        "html_markup": n_html,
        "fffd": n_fffd,
        "fig_missing": fig_missing,
        "fig_links": n_links,
        "table_misalign": table_misalign(md),
        "caption_fragment": caption_fragment(md),
        "glue": _count(_GLUE, md),
        "caps_merged": _count(_CAPS_MERGED, md),
        "empty_heading": _count(_EMPTY_HEADING, md),
        "page_num_leak": _count(_PAGE_NUM_LINE, md),
        "heading_glued": heading_glued(md),
    }
    # indicative score (NOT comparable with the lost harness numbers).
    penalty = (
        min(counts["fffd"], 20) * 0.5
        + counts["html_markup"] * 1.0
        + counts["fig_missing"] * 8.0
        + counts["table_misalign"] * 2.0
        + counts["caption_fragment"] * 1.0
        + counts["glue"] * 2.0
        + counts["caps_merged"] * 2.0
        + counts["empty_heading"] * 5.0
        + counts["page_num_leak"] * 2.0
        + counts["heading_glued"] * 1.0
    )
    score = max(0.0, round(100.0 - penalty, 1))
    out = {"score": score, "defects": counts, "fig_missing": bool(fig_missing),
           "fig_links": n_links}
    return out
