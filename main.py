#!/usr/bin/env python3
"""Noesis PDF Reader Lite — PyQt6 split view: rendered page (left) + extracted text (right).

Versione semplificata: un solo motore di rendering (PyMuPDF), un solo motore di
estrazione (PyMuPDF4LLM) e l'engine adattativo dei fix di layout sempre attivo.
Traduzione e gallery delle figure incluse; nessun dropdown a runtime.
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

import markdown as _md_lib

# Engine adattativo dei fix di layout (puro, senza PyQt): profilo → piano →
# pipeline. Importa lazy alcuni helper puri da questo modulo.
import layout_engine

# Temi dell'interfaccia (chiaro/scuro/sistema). Modulo puro (nessuna dipendenza
# Qt): qui si sceglie il tema attivo e si applicano i QSS/token ai widget.
import theme

# Parsing della specifica pagine del wizard batch (indici 0-based ordinati).
from pages import PageSpecError, parse_pages, format_pages_label, describe_pages

# Internazionalizzazione della sola interfaccia (dict T(), nessuna dipendenza
# Qt): le stringhe del chrome UI passano da qui, la lingua si cambia al volo
# e il config (lingue + preferenze) è gestito da questo modulo.
from i18n import (
    LANGUAGES, TRANSLATION_LANGUAGES, TRANSLATION_ENGINES, DEFAULTS, T,
    get_language, set_language, get_source_lang, set_source_lang,
    get_target_lang, set_target_lang,
    get_translation_engine, set_translation_engine,
    flag_endonym, get_config, get_setting, set_setting,
    init_config, save_config,
)

_MD_EXTENSIONS = ["tables", "fenced_code", "codehilite"]

# Durata tipica stimata delle animazioni "liquide" (ms): il progresso sale
# asintoticamente verso ~90% in questa finestra e chiude a 100% al termine.
_OVERLAY_EST_MS = 6000
# Stima per pagina usata dalla finestra batch (barra + anteprima).
_EXPORT_EST_MS_PER_PAGE = 6000

try:
    import pymupdf4llm
    _has_pymupdf4llm = True
except ImportError:
    pymupdf4llm = None  # type: ignore
    _has_pymupdf4llm = False

try:
    import pymupdf
    _has_pymupdf = True
except ImportError:
    pymupdf = None  # type: ignore
    _has_pymupdf = False

from PyQt6.QtCore import (
    Qt, QThread, QTimer, QEvent, pyqtSignal, QUrl, QStandardPaths, QRectF,
    QLocale, QPropertyAnimation, QEasingCurve, pyqtProperty, QRect,
)
from PyQt6.QtGui import (
    QImage, QPixmap, QFont, QKeySequence, QShortcut, QIcon,
    QPen, QBrush, QColor, QPainter, QDesktopServices, QTextDocument,
    QTextCursor, QTextImageFormat, QLinearGradient, QPainterPath, QTransform,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QSizePolicy,
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QScrollArea,
    QLabel,
    QTextEdit,
    QLineEdit,
    QToolBar,
    QFileDialog,
    QSpinBox,
    QRadioButton,
    QButtonGroup,
    QStackedWidget,
    QDoubleSpinBox,
    QPushButton,
    QCheckBox,
    QDialog,
    QMessageBox,
    QStatusBar,
    QWidget,
    QVBoxLayout,
    QFormLayout,
    QGroupBox,
    QDockWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsEllipseItem,
    QGraphicsTextItem,
    QGraphicsOpacityEffect,
    QGraphicsDropShadowEffect,
    QFrame,
    QToolButton,
    QMenu,
    QProgressBar,
)

# URL della guida online (sito statico pubblicato su GitHub Pages).
# Aprire con QDesktopServices.openUrl apre il browser di sistema: nessuna
# dipendenza QtWebEngine, nessuna pagina incorporata.
HELP_URL = (
    "https://vigliafg.github.io/noesis-pdf-reader-lite/help/"
)

# ═══════════════════════════════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _resolve_theme(mode: str) -> str:
    """Risolve ``system`` nel tema effettivo del sistema operativo."""
    mode = theme.normalize_mode(mode)
    if mode != "system":
        return mode
    try:
        scheme = QApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return "light"
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
    except Exception:  # noqa: BLE001 — in dubbio resta scuro
        pass
    return "dark"


def apply_theme_mode(mode: str) -> None:
    """Imposta il tema attivo (risolvendo ``system``) nel modulo ``theme``."""
    theme.set_mode(mode)
    theme.set_resolved(_resolve_theme(mode))


def _status_style(kind: str) -> str:
    """QSS per una label di stato: ``kind`` è un token colore del tema."""
    return "color: %s; font-size: 12px;" % theme.color(kind)


def clean_text(text: str) -> str:
    """Remove end-of-line hyphenation: "com-\npany" -> "company"."""
    return re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)


def _image_as_png(doc, xref: int) -> tuple[bytes, str] | None:
    """Return an embedded image as ``(png_bytes, "png")`` — always Qt-decodable.

    Raw embedded images may use formats Qt cannot display (JPEG2000/``jpx``,
    JBIG2/``jb2``, …), which would render as a null pixmap in the gallery.
    Rendering through a PyMuPDF ``Pixmap`` normalizes any source format to PNG.
    CMYK images are converted to RGB first (PNG cannot encode CMYK). Falls back
    to the raw bytes (best effort) if that fails.
    """
    try:
        pix = pymupdf.Pixmap(doc, xref)
        if pix.n == 4 and not pix.alpha:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)  # CMYK → RGB
        return pix.tobytes("png"), "png"
    except Exception:
        pass
    try:
        info = doc.extract_image(xref)
    except Exception:
        return None
    data = info.get("image")
    if not data:
        return None
    return data, (info.get("ext") or "png").lower()


def _region_image(
    doc, page_num: int, clip, zoom: float, embedded_only: bool = False
) -> tuple[bytes, str] | None:
    """Extract the image under a PDF-points rect as ``(png_bytes, "png")``.

    Prefers the original embedded raster; otherwise renders the region (whole
    figure, also for composite/vector figures). Returns None when nothing can
    be extracted.

    When ``embedded_only`` is True the zone is treated as a *capture zone*
    (exclude gesture): if the selection targets an embedded image (at least
    half of it inside), the WHOLE selected zone is rendered and captured —
    so composite figures (several embedded images, image + caption/vector
    parts) are kept in full instead of a single fragment. Pure-text zones
    (header/footer/caption, no embedded image) are skipped, so excluding
    them never dumps a rendered PNG into the gallery.
    """
    if not _has_pymupdf:
        return None
    try:
        page = doc[page_num]
        rect = pymupdf.Rect(clip)
        if rect.width < 1.0 or rect.height < 1.0:
            return None

        # embedded_only (exclude gesture): if the selection targets an
        # embedded image (at least half of it inside), render and capture
        # the WHOLE selected zone — composite figures (several embedded
        # images, image + caption/vector parts) are kept in full instead of
        # a single fragment. Pure-text zones (no embedded image) are
        # skipped, so excluding a header/footer/caption never dumps a
        # rendered PNG into the gallery.
        if embedded_only:
            for img in page.get_images(full=True):
                for r in page.get_image_rects(img[0]):
                    area = r.get_area()
                    if area > 0 and (r & rect).get_area() / area >= 0.5:
                        pix = page.get_pixmap(
                            clip=rect, matrix=pymupdf.Matrix(zoom, zoom)
                        )
                        return pix.tobytes("png"), "png"
            return None

        # 1) embedded image fully inside the selection → original raster
        for img in page.get_images(full=True):
            xref = img[0]
            for r in page.get_image_rects(xref):
                if not (
                    r.x0 >= rect.x0 and r.y0 >= rect.y0
                    and r.x1 <= rect.x1 and r.y1 <= rect.y1
                ):
                    continue
                converted = _image_as_png(doc, xref)
                if converted is not None:
                    return converted

        # 2) fallback: render the selected region at high resolution
        pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png"), "png"
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  layout fixes (generic corrections for two-column / chapter-open pages)
# ═══════════════════════════════════════════════════════════════════════════════


def _trim_edge_spaces(spans: list[dict]) -> list[dict]:
    """Rimuove gli span di soli spazi ai bordi di una riga.

    PyMuPDF spezza il testo in span anche sugli spazi (spesso span di soli
    spazi: su pa23/p301 sono il 45% del totale). Scartarli incolla le parole
    ("theyincreasewithareduction"), tenerli tutti lascia spazi inutili ai bordi
    di riga. Qui si preservano gli spazi **interni** e si rifilano solo i bordi.
    """
    while spans and not spans[0]["text"].strip():
        spans.pop(0)
    while spans and not spans[-1]["text"].strip():
        spans.pop()
    if spans:
        spans[0] = {**spans[0], "text": spans[0]["text"].lstrip()}
        spans[-1] = {**spans[-1], "text": spans[-1]["text"].rstrip()}
    return [s for s in spans if s["text"] != ""]


def _collect_blocks(page, exclude: tuple = ()) -> list[dict]:
    """Extract text blocks (with per-span formatting) from a pymupdf page.

    ``exclude`` is a tuple of (x0, y0, x1, y1) rects (PDF points); blocks
    mostly inside one of them are dropped so the adaptive engine can rebuild
    the reading order on a manually-cleaned source.
    """
    blocks: list[dict] = []
    for blk in page.get_text("dict")["blocks"]:
        if blk.get("type") != 0:
            continue
        if _is_excluded(tuple(blk["bbox"]), exclude):
            continue
        lines: list[list[dict]] = []
        max_size = 0.0
        for line in blk["lines"]:
            spans: list[dict] = []
            for s in line["spans"]:
                t = s["text"]
                if t == "":
                    continue
                spans.append(
                    {
                        "text": t,
                        "size": s["size"],
                        "bold": bool(s["flags"] & 16),
                        "italic": bool(s["flags"] & 2),
                        "x0": s["bbox"][0],
                        "x1": s["bbox"][2],
                    }
                )
                max_size = max(max_size, s["size"])
            spans = _trim_edge_spaces(spans)
            if spans:
                lines.append(spans)
        # De-hyphenate words split across a line break ("un-" + "common" →
        # "uncommon"). Only when the next line starts lowercase, so real
        # hyphenated compounds at line ends are left alone.
        if lines:
            fused: list[list[dict]] = [lines[0]]
            for nxt in lines[1:]:
                cur = fused[-1]
                if (
                    cur and nxt
                    and cur[-1]["text"].endswith("-")
                    and len(cur[-1]["text"]) > 1
                    and nxt[0]["text"][:1].islower()
                ):
                    cur[-1]["text"] = cur[-1]["text"][:-1] + nxt[0]["text"]
                    nxt = nxt[1:]
                if nxt:
                    fused.append(nxt)
            lines = fused
        if not lines:
            continue
        x0, y0, x1, y1 = blk["bbox"]
        blocks.append(
            {
                "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                "max_size": max_size, "lines": lines,
            }
        )
    return blocks


def _printed_page_number(page) -> int | None:
    """Numero di pagina **stampato** (letto dal margine alto/basso), o ``None``.

    Molti PDF hanno una numerazione stampata diversa dall'indice del file
    (front matter, tavole intercalate): il numero è una riga di **sole cifre**
    nella fascia del margine. Si prende la più in alto (la più in basso in
    mancanza) per non confonderla con le etichette degli assi quando una figura
    arriva a filo del bordo. È solo informativo: l'indice PDF resta la chiave di
    navigazione.
    """
    if page is None:
        return None
    try:
        h = page.rect.height
        band = min(0.05 * h, 45.0)
        cands: list[tuple[float, float, int]] = []
        for blk in page.get_text("dict").get("blocks", []):
            if blk.get("type") != 0:
                continue
            for line in blk["lines"]:
                y0, y1 = line["bbox"][1], line["bbox"][3]
                t = "".join(s["text"] for s in line["spans"]).strip()
                if not re.fullmatch(r"\d{1,4}", t):
                    continue
                if y1 <= band:
                    cands.append((y0, line["bbox"][0], int(t)))
                elif y0 >= h - band:
                    cands.append((y0, line["bbox"][0], int(t)))
        if not cands:
            return None
        cands.sort(key=lambda c: (c[0], c[1]))
        return cands[0][2]
    except Exception:
        return None


def _strip_margin_blocks(blocks: list[dict], page_height: float) -> list[dict]:
    """Drop blocks that lie entirely in the top/bottom page margins.

    Running headers, footers and watermarks (e.g. a "Made with Xodo" banner)
    are decorative. If left in, a full-ish-width header spanning the gap
    between two columns acts as a bridge in ``_merged_column_intervals`` and
    collapses the page to a single column, breaking the reading order. The
    band is the top/bottom 7% of the page (capped at 70pt), which removes
    page chrome but keeps real body text.
    """
    band = min(0.07 * page_height, 70.0)
    if band <= 0:
        return blocks
    return [
        b for b in blocks
        if b["y1"] > band and b["y0"] < page_height - band
    ]


def _merged_column_intervals(blocks: list[dict], page_width: float) -> list[list[float]]:
    """Merge narrow blocks into per-column x-intervals, dropping margin labels.

    Margin labels, page numbers and vertical side labels are narrow (<40pt)
    and must not be mistaken for a text column.
    """
    col = [b for b in blocks if (b["x1"] - b["x0"]) < 0.6 * page_width]
    if len(col) < 4:
        return []
    intervals = sorted((b["x0"], b["x1"]) for b in col)
    merged = [list(intervals[0])]
    for x0, x1 in intervals[1:]:
        if x0 <= merged[-1][1] + 3:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    # Drop narrow labels that sit in the outer page margins (page numbers,
    # vertical side labels); narrow lines in the middle are real content.
    return [
        m for m in merged
        if not ((m[1] - m[0]) < 40 and (m[0] < 30 or m[1] > page_width - 30))
    ]


def _detect_column_splits(blocks: list[dict], page_width: float) -> list[float]:
    """Return the x boundaries between text columns (N-1 splits for N columns).

    Handles any number of columns (two-column prose, three/four-column
    indexes). Every gap between merged column intervals that is comparable to
    the widest gap is a column boundary; narrow indentation/margin gaps are
    dropped, so they never split a column.
    """
    merged = _merged_column_intervals(blocks, page_width)
    if len(merged) < 2:
        return []
    gaps = [merged[i + 1][0] - merged[i][1] for i in range(len(merged) - 1)]
    widest = max(gaps)
    return [
        (merged[i][1] + merged[i + 1][0]) / 2
        for i, gap in enumerate(gaps)
        if gap >= 8 and gap >= 0.55 * widest
    ]


def _detect_column_split(blocks: list[dict], page_width: float):
    """Return the widest column boundary, or None if single-column."""
    merged = _merged_column_intervals(blocks, page_width)
    if len(merged) < 2:
        return None
    best_gap = 0.0
    split = page_width / 2
    for i in range(len(merged) - 1):
        gap = merged[i + 1][0] - merged[i][1]
        if gap > best_gap:
            best_gap = gap
            split = (merged[i][1] + merged[i + 1][0]) / 2
    return split if best_gap >= 8 else None


def _block_to_md(block: dict, as_column: bool) -> str:
    """Render a block to markdown, marking headings when it's a column block."""
    lines: list[str] = []
    for line in block["lines"]:
        parts: list[str] = []
        prev_x1: float | None = None
        prev_text = ""
        for s in line:
            t = s["text"]
            # Spazio codificato come gap tra due span (non come carattere):
            # PyMuPDF a volte non emette lo spazio, va ricostruito dalla x.
            prefix = ""
            if (
                t
                and not t[:1].isspace()
                and prev_text
                and not prev_text[-1].isspace()
                and prev_x1 is not None
                and s.get("x0") is not None
                and s["x0"] - prev_x1 > 0.8
            ):
                prefix = " "
            if not t.strip():
                parts.append(t)  # spazio interno: niente markdown
            elif s["bold"] and s["italic"]:
                parts.append(prefix + f"***{t}***")
            elif s["bold"]:
                parts.append(prefix + f"**{t}**")
            elif s["italic"]:
                parts.append(prefix + f"*{t}*")
            else:
                parts.append(prefix + t)
            prev_x1 = s.get("x1")
            prev_text = t
        lines.append("".join(parts).strip())

    if not as_column:
        return " ".join(lines)

    size = block["max_size"]
    level = 0
    if size >= 14:
        level = 1
    elif size >= 12:
        level = 2
    elif size >= 9.8 and any(
        s["bold"] and s["text"].strip() for l in block["lines"] for s in l
    ):
        level = 3

    if level == 0:
        return " ".join(lines)
    if level == 1:
        return "# " + " ".join(lines)

    heading = lines[0]
    body = " ".join(lines[1:])
    if body:
        return f"{'#' * level} {heading}\n\n{body}"
    return f"{'#' * level} {heading}"


# A caption row starts with one of these markers (e.g. "TABLE 166-3",
# "Table 5.6 Lysosomal Storage Diseases") — not a column header.
_CAPTION_RE = re.compile(r"^\s*(table|fig(?:ure)?|box|exhibit|chart)\b", re.IGNORECASE)

# Stricter variant used to keep a *numbered* caption row that spans the whole
# table width (ce24/p489 "TABLE 46-10 | TOPICS THAT SHOULD…"): requiring a
# digit after the marker avoids promoting a normal "| Table | Chairs |" header
# to a caption.
_CAPTION_NUMBERED_RE = re.compile(
    r"^\s*\**\s*(?:table|fig(?:ure)?|box|exhibit|chart)\s*\d", re.IGNORECASE
)


def _table_to_md(page, table) -> str:
    """Render a pymupdf table as a markdown table using clean per-cell text."""
    cells = sorted(table.cells, key=lambda c: (c[1], c[0]))  # by y0 then x0
    if not cells:
        return ""

    def _cell_text(cell) -> str:
        x0, y0, x1, y1 = cell
        clip = (x0 + 1.5, y0 + 1.5, max(x0 + 1.5, x1 - 1.5), max(y0 + 1.5, y1 - 1.5))
        return " ".join(page.get_textbox(clip).split()).strip()

    # Group cells into rows by their top y-coordinate.
    rows: list[tuple[float, list[tuple[float, float, str]]]] = []
    for cell in cells:
        txt = _cell_text(cell)
        if not txt:
            continue
        x0, y0, x1, _y1 = cell
        if rows and abs(rows[-1][0] - y0) < 5.0:
            rows[-1][1].append((x0, x1, txt))
        else:
            rows.append((y0, [(x0, x1, txt)]))
    for _, row in rows:
        row.sort(key=lambda c: c[0])

    if not rows:
        return ""

    def _row_xrange(row) -> tuple[float, float]:
        return min(c[0] for c in row), max(c[1] for c in row)

    max_cells = max(len(r) for _, r in rows)
    tw = table.bbox[2] - table.bbox[0]

    out: list[str] = []
    # A leading row that is not a real header is the table caption: either a
    # single full-width cell, or a row with fewer cells than the widest row
    # that spans the table width and starts with a caption marker. The latter
    # is the classic "TABLE 166-3 | EXAMPLES OF TARGETED CANCER THERAPIES",
    # which pymupdf splits into 2 cells — using it as the header would
    # truncate every data row to 2 columns.
    first_y, first = rows[0]
    x0, x1 = _row_xrange(first)
    caption_text = " ".join(t.replace("\n", " ") for _, _, t in first)
    is_caption = (
        len(first) == 1
        or (
            len(first) < max_cells
            and (x1 - x0) >= 0.9 * tw
            and bool(_CAPTION_RE.match(caption_text))
        )
        or (
            # Numbered caption that spans the full width even when it fills
            # every column (ce24/p489 "TABLE 46-10 | TOPICS …"): without this
            # the two-cell caption becomes the table header and the all-empty
            # second column is kept as a phantom column.
            (x1 - x0) >= 0.9 * tw
            and bool(_CAPTION_NUMBERED_RE.match(caption_text))
        )
    )
    if is_caption:
        out.append(f"**{caption_text}**")
        out.append("")  # blank line so the markdown renderer sees the table
        rows = rows[1:]

    if not rows:
        return "\n".join(out)

    # Column count comes from the widest row, never from the header alone:
    # a caption row (or a merged header) must not truncate the data columns.
    ncols = max(len(r) for _, r in rows)
    header = [txt.replace("\n", " ") for _, _, txt in rows[0][1]]
    if len(header) >= 2 and ncols > len(header):
        header = [""] * (ncols - len(header)) + header  # cella-vuota di testa persa
    header = (header + [""] * ncols)[:ncols]
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join("---" for _ in range(ncols)) + " |")
    for _, row in rows[1:]:
        cell_md = [txt.replace("\n", "<br>") for _, _, txt in row]
        cell_md = (cell_md + [""] * ncols)[:ncols]
        out.append("| " + " | ".join(cell_md) + " |")
    return "\n".join(out)


def _box_to_md(text: str) -> str:
    """Render a box/sidebar (a flat list of lines) as a list, not a table.

    A bordered box is not a data table: its text is a list of short items
    (bullets, labels) or a few wrapped paragraphs. Rendering it as a
    single-column markdown table (a row per line) put ``|`` on every line and
    hurt readability (over-tabling). So:
    - wrapped lines are re-joined into units (a continuation starts lowercase
      and the previous line has no final punctuation, or the previous line ends
      with a hyphen);
    - short ALL-CAPS units become bold sub-headings;
    - when most units are short the box is a list → ``- item`` bullets;
    - a box made of long units is prose → paragraphs.
    """
    raw: list[str] = []
    for ln in text.splitlines():
        ln = re.sub(r"[\x07\t]+", " ", ln)
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            raw.append(ln)
    if not raw:
        return ""
    units: list[str] = []
    for ln in raw:
        if units:
            prev = units[-1]
            if prev.endswith("-"):
                units[-1] = prev[:-1] + ln
                continue
            if not re.search(r"[.:;!?]$", prev) and ln[:1].islower():
                units[-1] = prev + " " + ln
                continue
        units.append(ln)
    n_short = sum(1 for u in units if len(u.split()) <= 12)
    listy = n_short >= max(1, (len(units) + 1) // 2)
    if not listy:
        return "\n\n".join(units)  # prosa: paragrafi
    parts: list[str] = []
    for u in units:
        if u.isupper() and len(u.split()) <= 8:
            if parts:
                parts.append("")
            parts.append(f"**{u}**")
            parts.append("")
        else:
            parts.append("- " + re.sub(r"^[•·]\s*", "", u))
    return "\n".join(parts).strip("\n")


def _box_title(page, rect: tuple, exclude: tuple = ()) -> tuple[str, tuple | None]:
    """Text block directly above a box (same x-range) → (title, bbox or None)."""
    x0, y0, x1, y1 = rect
    box_w = x1 - x0
    for b in _collect_blocks(page, exclude):
        if not (b["y1"] <= y0 and b["y1"] >= y0 - 30):
            continue
        # Il titolo deve stare sopra il box e condividerne la x: un numero di
        # pagina nel margine laterale (es. "2199") non è il titolo del box.
        overlap = min(b["x1"], x1) - max(b["x0"], x0)
        if overlap < 0.5 * max(1.0, min(box_w, b["x1"] - b["x0"])):
            continue
        t = " ".join(s["text"] for line in b["lines"] for s in line)
        t = re.sub(r"\s+", " ", t).strip()
        if not t or re.fullmatch(r"[\d\s.,–-]+", t):
            continue  # solo un numero di pagina
        return t, (b["x0"], b["y0"], b["x1"], b["y1"])
    return "", None


def _is_table_legend(b: dict, table_regions: list[tuple]) -> bool:
    """Small-text legend/footnote directly below a data table.

    Abbreviation keys and footnotes under tables are real content even at
    <6.5pt (e.g. hockberg p.1430's 6.0pt key under TABLE 164.3). Free-floating
    small text (figure sub-labels like "(a) (b)", watermarks) is not content
    and stays filtered out by the 6.5pt floor.
    """
    if b["max_size"] < 5.0:
        return False
    for r in table_regions:
        if b["y0"] >= r[3] - 4 and b["y0"] - r[3] <= 45:
            if b["x0"] >= r[0] - 30 and b["x1"] <= r[2] + 30:
                return True
    return False


def _rect_overlap_area(r1: tuple, r2: tuple) -> float:
    """Intersection area of two (x0, y0, x1, y1) rects."""
    ox = max(0.0, min(r1[2], r2[2]) - max(r1[0], r2[0]))
    oy = max(0.0, min(r1[3], r2[3]) - max(r1[1], r2[1]))
    return ox * oy


def _rect_overlaps_any(rect: tuple, rects, thresh: float = 0.5) -> bool:
    """True se ``rect`` copre almeno ``thresh`` del **minore** con un altro rect.

    Serve alla dedup delle figure: se una regione rilevata dalle didascalie
    coincide (in gran parte) con una `picture` già emessa dalla content map, è
    la **stessa** figura e non va ri-emessa.
    """
    a = (rect[2] - rect[0]) * (rect[3] - rect[1])
    if a <= 0:
        return False
    for r in rects:
        b = (r[2] - r[0]) * (r[3] - r[1])
        if b <= 0:
            continue
        inter = _rect_overlap_area(rect, r)
        if inter > 0 and inter / min(a, b) >= thresh:
            return True
    return False


def _is_excluded(rect: tuple, exclude: tuple = ()) -> bool:
    """True when ``rect`` is mostly covered by one of the excluded zones.

    A user-drawn exclusion (header, footer, figure, caption…) hides any
    block/table/box whose area is ≥50% inside it, so the adaptive engine
    rebuilds the reading order on the remaining content only.
    """
    if not exclude:
        return False
    area = (rect[2] - rect[0]) * (rect[3] - rect[1])
    if area <= 0:
        return False
    return any(_rect_overlap_area(rect, ex) / area >= 0.5 for ex in exclude)


def _norm_strip_text(t: str) -> str:
    """Normalize text for title-strip matching.

    Strips markdown table furniture and hyphens (incl. soft hyphens \u00ad)
    and collapses whitespace, so the same words written as
    ``In-\xadHospital`` or ``In-Hospital`` compare equal.
    """
    t = t.replace("\u00ad", "")
    t = re.sub(r"[|\u2014\-]", " ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _is_title_strip(bx: dict, boxes: list[dict]) -> bool:
    """True when ``bx`` is a thin border strip holding the title (or its
    *start*, which continues inside the box) of the box directly below it.

    Such strips are drawn as a separate rectangle, so they are detected as
    their own box and the title ends up emitted twice: once as the strip's
    markdown table and once as the **bold heading** of the box below.
    """
    r = bx["rect"]
    w, h = r[2] - r[0], r[3] - r[1]
    if h > 0.5 * w:
        return False  # not strip-shaped
    text = _norm_strip_text(bx["md"])
    if len(text) < 8:
        return False
    for other in boxes:
        if other is bx:
            continue
        o = other["rect"]
        # The strip shares its bottom border with the box below (it is the
        # box's title band). A visible gap means a stacked box, not a strip:
        # e.g. stacked citation entries would otherwise look like strips.
        if not (r[3] - 2 <= o[1] <= r[3] + 6):
            continue
        if min(r[2], o[2]) - max(r[0], o[0]) < 0.6 * w:
            continue  # different column
        title = _norm_strip_text(other["title"])
        # The strip's text is the title itself (or its start) and the title
        # belongs to the box below, which emits it as its bold heading.
        if len(title) >= 12 and title.startswith(text):
            return True
    return False


def _dedup_boxes(boxes: list[dict]) -> list[dict]:
    """Drop boxes whose area is mostly covered by a larger kept box.

    Nested/overlapping rectangles (e.g. a chart drawn as several bordered
    cells) would otherwise be emitted as duplicate tables.
    """
    ordered = sorted(
        boxes,
        key=lambda b: (b["rect"][2] - b["rect"][0]) * (b["rect"][3] - b["rect"][1]),
        reverse=True,
    )
    kept: list[dict] = []
    for bx in ordered:
        r = bx["rect"]
        area = (r[2] - r[0]) * (r[3] - r[1])
        if area <= 0:
            continue
        if any(
            _rect_overlap_area(r, k["rect"]) / area >= 0.6
            for k in kept
        ):
            continue
        kept.append(bx)
    return kept


def _looks_like_prose(page, rect: tuple) -> bool:
    """True when the text in ``rect`` is running prose, not a sidebar/box.

    Some pages paint the *columns* with a light background rectangle: the whole
    column is then inside a "closed filled rect" and ``_detect_boxes`` would
    turn every paragraph into a single-column markdown table (breaking word
    wrapping). Real sidebars/tables have short lines (labels, list items); a
    column of prose has long lines (>= 8 words). Only reject when most lines
    are long, so a box of bullets is still treated as a box.
    """
    try:
        d = page.get_text("dict", clip=pymupdf.Rect(rect))
    except Exception:
        return False
    lengths: list[int] = []
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk["lines"]:
            words = " ".join(s["text"] for s in line["spans"]).split()
            if words:
                lengths.append(len(words))
    if len(lengths) < 4:
        return False
    return sum(1 for n in lengths if n >= 8) >= 0.5 * len(lengths)


def _detect_boxes(page, page_width: float, table_regions: list[tuple], exclude: tuple = ()) -> list[dict]:
    """Detect bordered boxes (sidebars) and render them as markdown tables.

    A box is a closed rectangle from ``get_drawings()`` that contains text and
    is not part of a data table. Returns ``[{rect, title, title_bbox, md}]``.
    """
    pw, ph = page.rect.width, page.rect.height
    boxes: list[dict] = []
    for d in page.get_drawings():
        if d["type"] not in ("fs", "s", "f"):
            continue
        r = d["rect"]
        w, h = r[2] - r[0], r[3] - r[1]
        if w < 60 or h < 30:
            continue
        if w > 0.97 * pw or h > 0.97 * ph:
            continue  # full-page frame
        if r[1] < -2 or r[3] > ph + 2:
            continue  # drawn outside the page: decorative edge strip
        if w >= 0.9 * pw and (r[1] < 60 or r[3] > ph - 60):
            continue  # running header/footer, not a content box
        rect = tuple(r)
        if _is_excluded(rect, exclude):
            continue  # manually excluded zone
        # Skip boxes that are (mostly) inside a data table — their content is
        # rendered by the table path, not the box path.
        area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        if area > 0 and any(
            _rect_overlap_area(rect, tr) / area >= 0.5
            for tr in table_regions
        ):
            continue
        text = page.get_text(clip=r).strip()
        if not text:
            continue
        # A light background rectangle behind a whole column contains prose:
        # it is not a sidebar. Without this, the whole column becomes a
        # one-column table and word wrapping is destroyed (pa23/p303,
        # ce24/p489).
        if _looks_like_prose(page, rect):
            continue
        # Skip trivial boxes: a lone page number / short label is not a sidebar.
        n_lines = len([ln for ln in text.splitlines() if ln.strip()])
        if n_lines < 2 and len(text) < 30:
            continue
        title, tbbox = _box_title(page, rect, exclude)
        boxes.append(
            {"rect": rect, "title": title, "title_bbox": tbbox, "md": _box_to_md(text)}
        )
    # A title strip (the start of a box title in its own thin border) would
    # duplicate the title, which is already emitted as the **heading** of the
    # box below.
    boxes = [b for b in boxes if not _is_title_strip(b, boxes)]
    return _dedup_boxes(boxes)


# ── figure linking: "corpo unico + didascalia" ──────────────────────────────
# PyMuPDF4LLM non emette il corpo della figura: lascia solo il testo interno
# (etichette/legenda) e la didascalia come blocchi separati, quindi nel markdown
# la figura appare smontata. Qui si individua la regione grafica sopra la
# didascalia (cluster vettoriali + immagini embedded), la si renderizza in un PNG
# e si inserisce `![figura n](uri)` accanto alla didascalia.

_FIGURE_CAPTION_RE = re.compile(
    # "FIGURE 148-1", "FIG. 14.10", "Figure: scheme", "Figure A — schematic",
    # "E-FIGURE 175-1" (appendice Cecil: prefisso "E-").
    # Senza numero serve un separatore o una maiuscola dopo: una frase di corpo
    # tipo "Figure shows that …" NON è una didascalia.
    r"^\s*\**\s*(?:e[-\s]?)?fig(?:ure)?\b\.?\s*(?:\d|[:\-–—]|(?-i:[A-Z]))",
    re.IGNORECASE,
)


def _bbox_mostly_inside(b: tuple, r: tuple) -> bool:
    area = (b[2] - b[0]) * (b[3] - b[1])
    if area <= 0:
        return False
    return _rect_overlap_area(b, r) / area >= 0.5


def _figure_candidates(page) -> list[tuple]:
    """Graphic regions of the page: vector clusters + embedded image bboxes."""
    out: list[tuple] = []
    try:
        for r in page.cluster_drawings():
            out.append((r.x0, r.y0, r.x1, r.y1))
    except Exception:
        pass
    try:
        for info in page.get_image_info():
            b = info.get("bbox")
            if b:
                out.append(tuple(b))
    except Exception:
        pass
    pw, ph = page.rect.width, page.rect.height
    kept: list[tuple] = []
    for r in out:
        w, h = r[2] - r[0], r[3] - r[1]
        if w < 40 or h < 25:
            continue  # troppo piccolo
        if w > 0.97 * pw and h > 0.97 * ph:
            continue  # sfondo di pagina
        if h < 10 and w > 0.5 * pw:
            continue  # riga orizzontale
        if w < 12 and h > 0.5 * ph:
            continue  # riga verticale
        kept.append(r)
    return kept


def _figure_regions(page, exclude: tuple = ()) -> list[dict]:
    """Regioni-figura: i cluster grafici sopra ogni didascalia ``FIG n``."""
    if not _has_pymupdf or page is None:
        return []
    candidates = _figure_candidates(page)
    if not candidates:
        return []
    figures: list[dict] = []
    for blk in _collect_blocks(page, exclude):
        text = " ".join(s["text"] for line in blk["lines"] for s in line)
        text = re.sub(r"\s+", " ", text).strip()
        if not _FIGURE_CAPTION_RE.match(text):
            continue
        caption = (blk["x0"], blk["y0"], blk["x1"], blk["y1"])
        sel = []
        for c in candidates:
            if not (c[3] <= caption[1] + 2 and caption[1] - c[3] <= 110):
                continue  # deve stare sopra la didascalia, vicino
            if min(c[2], caption[2]) - max(c[0], caption[0]) <= 0:
                continue  # colonna diversa
            # La figura sta nella colonna della didascalia: un cluster grafico
            # a tutta larghezza (banner d'header, righe di tabella) non deve
            # essere "assorbito" e far considerare figura l'intera fascia alta.
            col_pad = 40.0
            cx0 = max(c[0], caption[0] - col_pad)
            cx1 = min(c[2], caption[2] + col_pad)
            if cx1 - cx0 < 40:
                continue
            sel.append((cx0, c[1], cx1, c[3]))
        if not sel:
            continue
        figure_rect = (
            min(c[0] for c in sel), min(c[1] for c in sel),
            max(c[2] for c in sel), max(c[3] for c in sel),
        )
        # Zona esclusa a mano che copre la figura (immagine e/o didascalia):
        # l'utente non vuole quella figura nel markdown (§14).
        if exclude and _is_excluded(figure_rect, exclude):
            continue
        figures.append(
            {
                "rect": figure_rect,
                "caption": text,
                "caption_rect": caption,
            }
        )
    return figures


def _render_figure(page, rect: tuple, dest_dir, page_num: int, index: int) -> str | None:
    """Renderizza la regione-figura in un PNG e ne ritorna il ``file://`` URI."""
    doc = getattr(page, "parent", None)
    if doc is None or not _has_pymupdf or dest_dir is None:
        return None
    pad = 2.0
    clip = pymupdf.Rect(rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)
    res = _region_image(doc, page_num, clip, 3.0)
    if res is None:
        return None
    data, ext = res
    try:
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"page_{page_num + 1:04d}_fig_{index}.{ext}"
        path.write_bytes(data)
        return path.resolve().as_uri()
    except Exception:
        return None


def _norm_text(s: str) -> str:
    """Minuscolo, senza markdown/punteggiatura: per confrontare md e pagina."""
    s = re.sub(r"[*_`~]", "", s)
    return re.sub(r"\s+", " ", re.sub(r"\W+", " ", s)).strip().lower()


def _strip_images(s: str) -> str:
    """Toglie figure/base64 dal testo (per confronti tipo recall)."""
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", s)
    return re.sub(r"data:[^\s)]+", " ", s)


#: voce d'indice tipica: "termine, 1125" / "termine, 86-87" (+ suffisso lettera)
_INDEX_ENTRY_RE = re.compile(r",\s*\d{1,4}(?:[–\-]\d{1,4})?[a-z]?\b")


def _rotated_table_rects(page, elements: list[dict]) -> list[tuple]:
    """bbox dei `table` con testo **ruotato** (``dir`` non orizzontale).

    Una tabella ruotata 90° non è linearizzabile in markdown: va resa come
    immagine. Qui si rileva guardando la direzione delle righe di testo dentro
    il bbox della tabella (PyMuPDF ``dir``: ``(1,0)`` = orizzontale).
    """
    tbl = [e for e in elements if e.get("class") == "table"]
    if not tbl:
        return []
    rects = [tuple(e["bbox"]) for e in tbl]
    vert = horiz = 0
    try:
        d = page.get_text("dict")
    except Exception:
        return []
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for ln in blk.get("lines", []):
            bb = ln.get("bbox", (0, 0, 0, 0))
            cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            if not any(r[0] - 2 <= cx <= r[2] + 2 and r[1] - 2 <= cy <= r[3] + 2
                       for r in rects):
                continue
            dx, dy = ln.get("dir", (1, 0))
            if abs(dy) > abs(dx):
                vert += 1
            else:
                horiz += 1
    if vert > 0 and vert >= 0.5 * (vert + horiz):
        return rects
    return []


#: Tolleranza bbox (pt) per riconoscere un blocco di pagina come **chrome**
#: (testatina/piè di pagina/numero). Più ampia di quella delle figure: il
#: blocco eccede di qualche punto il bbox dell'elemento e va comunque escluso.
_CHROME_BBOX_SLACK = 6.0


def _figure_covered_table_rects(page, elements: list[dict]) -> list[tuple]:
    """Rect delle tabelle **interamente contenute** in una regione-figura.

    Un pannello figura (EEG, tabella-segnapassi) può essere classificato come
    ``table`` dalla content map, ma è reso come **immagine** da ``_link_figures``
    (via didascalia): il suo testo non è una tabella da valutare. Evita il doppio
    (testo + immagine) e i falsi ``table_content`` (es. `fe22 p207`, `ha22 p3355`).
    """
    if not any(e.get("class") == "table" for e in elements):
        return []
    try:
        regions = _figure_regions(page)
    except Exception:
        return []
    if not regions:
        return []

    def _cover_ratio(b: tuple, r: tuple) -> float:
        ix = min(b[2], r[2]) - max(b[0], r[0])
        iy = min(b[3], r[3]) - max(b[1], r[1])
        if ix <= 0 or iy <= 0:
            return 0.0
        area = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
        return (ix * iy) / area

    out: list[tuple] = []
    for e in elements:
        if e.get("class") != "table":
            continue
        b = tuple(e["bbox"])
        # contenuta per intero, oppure **quasi tutta** dentro la regione (il
        # riquadro grafico può sottostimare di poco i bordi del pannello, es.
        # ce24 p68: tabella-modulo E-FIGURE coperta al 92%).
        if any(_cover_ratio(b, f["rect"]) >= 0.85 for f in regions):
            out.append(b)
    return out


def _raster_figure_table_rects(page, elements: list[dict]) -> list[tuple]:
    """Rect delle tabelle-figura il cui contenuto è **OCR di un'immagine raster**.

    Una tabella della content map **dentro una regione-figura** può essere:
    - una tabella **vettoriale** reale (es. `fe22 p207` FIG. E2: va tenuta);
    - un pannello-immagine (EEG, lastra) che PyMuPDF4LLM ha tentato di
      trasformare in tabella producendo **spazzatura** (es. `ha22 p3355`).

    Discriminante deterministico: la tabella è OCR-spazzatura se il suo bbox è
    coperto al ≥50% da un'**immagine raster incorporata** (``page.get_image_info``).
    In tal caso il contenuto è già reso come immagine dalla regione-figura e la
    tabella va tolta dal markdown (niente doppio testo+immagine).
    """
    covered = _figure_covered_table_rects(page, elements)
    if not covered:
        return []
    try:
        imgs = [tuple(i["bbox"]) for i in page.get_image_info()]
    except Exception:  # noqa: BLE001
        return []
    if not imgs:
        return []

    def _cover_ratio(b: tuple, r: tuple) -> float:
        ix = min(b[2], r[2]) - max(b[0], r[0])
        iy = min(b[3], r[3]) - max(b[1], r[1])
        if ix <= 0 or iy <= 0:
            return 0.0
        area = max(1e-6, (b[2] - b[0]) * (b[3] - b[1]))
        return (ix * iy) / area

    return [b for b in covered
            if any(_cover_ratio(b, r) >= 0.5 for r in imgs)]


def _strip_table_blocks(md: str, elements: list[dict],
                        rects: list[tuple]) -> str:
    """Toglie dal markdown i blocchi-tabella corrispondenti a ``rects``.

    Associa ogni blocco ``|...|`` all'elemento ``table`` per **sovrapposizione
    di token** (il md deriva dal testo dell'elemento): robusto ai token OCR che
    il test riga-per-riga di ``_link_figures`` non riconosce (`ha22 p3355`).
    """
    cov = {tuple(r) for r in rects}
    keys: list[set[str]] = []
    for e in elements:
        if e.get("class") == "table" and tuple(e["bbox"]) in cov:
            toks = set(_norm_text(e.get("text") or "").split())
            if toks:
                keys.append(toks)
    if not keys or "|" not in md:
        return md
    lines = md.split("\n")
    out: list[str] = []
    k = 0
    while k < len(lines):
        if lines[k].lstrip().startswith("|"):
            j = k
            block: list[str] = []
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                block.append(lines[j])
                j += 1
            btoks: set[str] = set()
            for b in block:
                btoks |= set(_norm_text(b).split())
            # Soglia bassa (0.3): il markdown riformatta la tabella (righe/colonne
            # diverse dal testo dell'elemento), quindi la copertura non è piena.
            # Il candidato è già ristretto (tabella coperta da figura **e** su
            # immagine raster), perciò il rischio di falsi positivi è trascurabile.
            if btoks and any(len(btoks & key) / len(btoks) >= 0.3
                             for key in keys):
                k = j
                continue
            out.extend(block)
            k = j
            continue
        out.append(lines[k])
        k += 1
    return "\n".join(out)


def _page_text_no_figures(page, elements: list[dict],
                          exclude_chrome: bool = False) -> str:
    """Testo della pagina escludendo le regioni ``picture`` (rese nell'immagine).

    Esclude anche le **tabelle ruotate**, che l'IR rende come immagine. Con
    ``exclude_chrome=True`` esclude anche testatine/piè di pagina/numero pagina:
    sono chrome, non contenuto (Golden Rule: il recall non deve penalizzarli).
    """
    pics = [e["bbox"] for e in elements if e.get("class") == "picture"]
    pics += _rotated_table_rects(page, elements)
    # tabelle-figura (pannelli resi come immagine): il loro testo è nell'immagine
    pics += _figure_covered_table_rects(page, elements)
    chrome: list[tuple] = []
    if exclude_chrome:
        chrome = [e["bbox"] for e in elements
                  if e.get("class") in ("page-header", "page-footer",
                                        "page-number")]

    def _inside(b, rects, slack: float) -> bool:
        x0, y0, x1, y1 = b["bbox"]
        for px0, py0, px1, py1 in rects:
            if (x0 >= px0 - slack and x1 <= px1 + slack
                    and y0 >= py0 - slack and y1 <= py1 + slack):
                return True
        return False

    def _mostly_inside(b, rects, ratio: float = 0.8) -> bool:
        """True se il blocco è **per lo più** dentro una regione (≥ ``ratio``).

        Il bbox dell'elemento può sottostimare di qualche punto il riquadro
        grafico (es. banner-pubblicitario `pa23 p935`: il testo del banner esce
        di ~3 pt dal bbox `picture`). Così si riconosce comunque come figura.
        """
        x0, y0, x1, y1 = b["bbox"]
        area = (x1 - x0) * (y1 - y0)
        if area <= 0:
            return False
        for px0, py0, px1, py1 in rects:
            ix = min(x1, px1) - max(x0, px0)
            iy = min(y1, py1) - max(y0, py0)
            if ix > 0 and iy > 0 and (ix * iy) / area >= ratio:
                return True
        return False

    parts: list[str] = []
    try:
        for blk in page.get_text("dict").get("blocks", []):
            if blk.get("type") != 0:
                continue
            # figure/tabelle ruotate: tolleranza stretta (2 pt) o contenimento
            # per lo più pieno, così non si esclude prosa adiacente alla figura.
            if _inside(blk, pics, 2) or _mostly_inside(blk, pics):
                continue
            # chrome (testatine/piè/numero pagina): tolleranza più ampia. Il
            # blocco di pagina eccede spesso di qualche punto il bbox
            # dell'elemento (es. `mw15 p261`: blocco a y0≈20.5 vs header a 23),
            # e va comunque riconosciuto come chrome (R5: non è contenuto).
            if chrome and _inside(blk, chrome, _CHROME_BBOX_SLACK):
                continue
            for ln in blk["lines"]:
                parts.append("".join(s["text"] for s in ln["spans"]))
    except Exception:
        pass
    return "\n".join(parts)


def _word_recall(ref: str, out: str, minlen: int = 6) -> float:
    """Quota di parole lunghe di ``ref`` presenti in ``out`` (0..1)."""
    def _ws(t: str) -> set[str]:
        t = re.sub(r"-\s*\n\s*", "", t.lower())
        t = re.sub(r"-\s+(?=[a-z])", "", t)
        return {w for w in re.sub(r"[^a-z0-9]+", " ", t).split() if len(w) >= minlen}

    words = _ws(ref)
    if not words:
        return 1.0
    return len(words & _ws(out)) / len(words)


def _looks_like_index(page_text: str) -> bool:
    """True se la pagina è un **indice** (molte voci "termine, numero")."""
    lines = [ln for ln in page_text.splitlines() if ln.strip()]
    if len(lines) < 15:
        return False
    hits = sum(1 for ln in lines if _INDEX_ENTRY_RE.search(ln))
    return hits >= 0.4 * len(lines)


def _ir_gate(page, md: str, elements: list[dict]) -> tuple[bool, str]:
    """Gate d'integrità per la pipeline IR (testo/figure/tabelle).

    False (→ fallback a ``current``) se: body vuoto; **recall** del testo sotto
    soglia (cattura le celle di tabella perse). Le pagine a sole figure non
    vengono penalizzate (il testo-figura è escluso dal confronto). Le pagine
    d'**indice** sono gestite da un percorso dedicato in ``ir_layout``
    (``index_markdown``), quindi non forzano più il fallback.
    """
    if not (md or "").strip():
        return False, "vuoto"
    page_text = _page_text_no_figures(page, elements)
    if _norm_text(page_text).strip() == "":
        return True, "solo-figure"  # pagina di sole figure: niente prosa da perdere
    # tabella **ruotata**: non linearizzabile in markdown → segnalata (l'IR la
    # rende come immagine; `_apply_ir_on_page` la considera gestita, non fallback).
    if _rotated_table_rects(page, elements):
        return False, "tabella-ruotata"
    # recall della prosa: solo se c'è testo sufficiente (evita i falsi positivi
    # sulle pagine-grafico, dove le etichette degli assi non sono prosa).
    ntexty = sum(1 for e in elements
                 if e.get("class") in ("text", "section-header", "title"))
    if ntexty > 2:
        r = _word_recall(page_text, _strip_images(md))
        if r < 0.90:
            return False, f"recall {r:.2f}"
    # tabelle: riferimento = **parole di pagina** nella regione delle tabelle
    # (content map). Robusto dove `find_tables` dà celle garbled (es. p231) e
    # senza il costo di `find_tables` (~1s/pagina). Se l'IR avesse perso la
    # tabella, il recall cala e il gate fallisce. Le tabelle ruotate sono
    # escluse (rese come immagine).
    rotated = {tuple(r) for r in _rotated_table_rects(page, elements)}
    # tabelle-figura (pannelli resi come immagine): non sono tabelle da valutare
    covered = {tuple(r) for r in _figure_covered_table_rects(page, elements)}
    tbl_rects = [e["bbox"] for e in elements
                 if e.get("class") == "table"
                 and tuple(e["bbox"]) not in rotated
                 and tuple(e["bbox"]) not in covered]
    if tbl_rects:
        def _in_tbl(b: tuple) -> bool:
            cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            return any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in tbl_rects)

        ref = " ".join(w[4] for w in page.get_text("words") if _in_tbl(w[:4]))
        if ref and _word_recall(ref, _strip_images(md)) < 0.85:
            return False, "tabella"
    return True, "ok"


def _figure_internal_text(page, fig) -> str:
    """Testo interno alla regione-figura (etichette/assi/numeri), normalizzato.

    Serve a togliere quelle righe dal markdown quando la figura è resa come
    immagine: sono già visibili nel PNG, ripeterle è rumore.
    """
    x0, y0, x1, y1 = fig["rect"]
    try:
        d = page.get_text("dict", clip=pymupdf.Rect(x0 - 4, y0 - 4, x1 + 4, y1 + 4))
    except Exception:
        return ""
    parts: list[str] = []
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk["lines"]:
            t = _norm_text(" ".join(s["text"] for s in line["spans"]))
            if t:
                parts.append(t)
    return " ".join(parts)


# ── figure embedding: JPEG base64 con tetto al 30% + gate OCR ──────────────
_FIGURE_ZOOM = 3.0                 # risoluzione di render della regione
_FIGURE_EMBED_RATIO = 0.30         # tetto: JPEG ≤ 30% del PNG equivalente
_FIGURE_Q_START = 88               # qualità di partenza (4:2:0)
_FIGURE_Q_FLOOR = 75               # qualità minima per rientrare nel 30%
_FIGURE_Q_MAX = 95                 # qualità massima (eccezione leggibilità)
_FIGURE_OCR_RECALL_MIN = 0.90      # soglia del gate OCR


def _tesseract_ocr_image(data: bytes, lang: str = "eng") -> str:
    """OCR di un'immagine (bytes) via CLI Tesseract; ``""`` se non disponibile."""
    exe = shutil.which("tesseract")
    if not exe:
        return ""
    try:
        r = subprocess.run(
            [exe, "stdin", "stdout", "-l", lang],
            input=data, capture_output=True, timeout=30,
        )
        if r.returncode != 0:
            return ""
        return r.stdout.decode("utf-8", "ignore")
    except Exception:
        return ""


_CAPTURE_NATIVE_MIN_CHARS = 8
_TESS_LANGS_CACHE: frozenset[str] | None = None


def _available_tess_langs() -> frozenset[str]:
    """Lingue Tesseract realmente installate (cached); set vuoto se ignote."""
    global _TESS_LANGS_CACHE
    if _TESS_LANGS_CACHE is not None:
        return _TESS_LANGS_CACHE
    langs: set[str] = set()
    exe = shutil.which("tesseract")
    if exe:
        try:
            r = subprocess.run(
                [exe, "--list-langs"], capture_output=True, timeout=10)
            for line in r.stdout.decode("utf-8", "ignore").splitlines():
                token = line.strip()
                if token and " " not in token and ":" not in token:
                    langs.add(token)
        except Exception:  # noqa: BLE001
            pass
    _TESS_LANGS_CACHE = frozenset(langs)
    return _TESS_LANGS_CACHE


def _ocr_lang_candidates(lang: str) -> list[str]:
    """Lingue OCR da tentare, filtrate su quelle installate (+ fallback eng).

    Evita di far tentare a Tesseract lingue mancanti (rumore su stderr e
    tempo sprecato): con solo ``eng`` installato, ``auto`` degrada subito.
    """
    avail = _available_tess_langs()
    out: list[str] = []
    for code in (lang or "").split("+"):
        code = code.strip()
        if not code or code in out:
            continue
        if not avail or code in avail:
            out.append(code)
    if "eng" not in out and (not avail or "eng" in avail):
        out.append("eng")
    if not out:
        out.append((lang or "eng").split("+")[0] or "eng")
    return out


def _capture_ocr_once(page, rect, lang: str) -> str:
    """Un tentativo OCR: textpage MuPDF (layout-aware), poi Tesseract CLI."""
    try:
        tp = page.get_textpage_ocr(language=lang, dpi=300, full=False)
        txt = page.get_text("text", textpage=tp, clip=rect)
        if txt and txt.strip():
            return txt
    except Exception:  # noqa: BLE001 — lingua mancante / OCR non disponibile
        pass
    try:
        m = max(_FIGURE_ZOOM, 2.0)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(m, m), clip=rect)
        return _tesseract_ocr_image(pix.tobytes("png"), lang)
    except Exception:  # noqa: BLE001
        return ""


def _capture_ocr_text(page, rect, lang: str) -> str:
    """OCR di una regione con fallback di lingua.

    Serializzato con ``ir_layout._PDF_OCR_LOCK``: Tesseract non è rientrante e
    la pipeline IR lo usa già in modo esclusivo.
    """
    try:
        import ir_layout  # lazy: lock OCR condiviso con la pipeline IR
        lock = ir_layout._PDF_OCR_LOCK
    except Exception:  # noqa: BLE001
        lock = None
    for candidate in _ocr_lang_candidates(lang):
        if lock is not None:
            with lock:
                text = _capture_ocr_once(page, rect, candidate)
        else:
            text = _capture_ocr_once(page, rect, candidate)
        if text.strip():
            return text.strip()
    return ""


def _capture_table_markdown(page, rect) -> str:
    """Markdown delle tabelle **sostanziali** dentro ``rect`` (o "").

    Filtra i falsi positivi di ``find_tables`` su semplice prosa: servono
    almeno 2 righe × 2 colonne, una copertura ragionevole della regione e
    almeno 4 celle non vuote.
    """
    try:
        tabs = page.find_tables(clip=rect)
    except Exception:  # noqa: BLE001
        return ""
    out: list[str] = []
    for t in tabs.tables:
        rows = getattr(t, "row_count", 0) or 0
        cols = getattr(t, "col_count", 0) or 0
        if rows < 2 or cols < 2:
            continue
        try:
            bbox = pymupdf.Rect(t.bbox)
            if rect.get_area() > 0:
                cover = (bbox & rect).get_area() / rect.get_area()
                if cover < 0.30:
                    continue
        except Exception:  # noqa: BLE001
            pass
        try:
            md = t.to_markdown()
        except Exception:  # noqa: BLE001
            continue
        cells = [c for c in md.replace("|", " ").replace("---", " ").split() if c]
        if len(cells) < 4:
            continue
        out.append(md)
    return "\n\n".join(out)


def _interpret_region(page, rect) -> tuple[str, str]:
    """Testo di una regione come ``(testo, metodo)``.

    Ordine: tabella nativa sostanziale → markdown; testo nativo (vettoriale,
    niente OCR); altrimenti OCR. Metodo in ``{"table","native","ocr","none"}``.
    """
    table_md = _capture_table_markdown(page, rect)
    if table_md:
        return table_md, "table"
    try:
        native = page.get_text("text", clip=rect)
    except Exception:  # noqa: BLE001
        native = ""
    if native and len(native.strip()) >= _CAPTURE_NATIVE_MIN_CHARS:
        return native.strip(), "native"
    lang = _tess_lang_code(get_source_lang())
    text = _capture_ocr_text(page, rect, lang)
    return text, ("ocr" if text else "none")


def _ocr_token_recall(jpeg: bytes, expected: str) -> float:
    """Frazione dei token attesi riconosciuti dall'OCR sulla figura."""
    exp = _norm_text(expected).split()
    if not exp:
        return 1.0
    got = set(_norm_text(_tesseract_ocr_image(jpeg)).split())
    if not got:
        return 0.0
    hit = sum(1 for w in exp if w in got)
    return hit / len(exp)


def _figure_jpeg(page, rect: tuple, internal_text: str = "") -> bytes | None:
    """JPEG della regione-figura con tetto 30% e, se c'è testo, gate OCR.

    - parte da qualità alta e scende (fino al floor) per rientrare nel 30%;
    - se la figura contiene testo, risale di qualità (anche **sforando** il 30%)
      finché l'OCR riconosce i token attesi; in ultima istanza qualità massima.
    """
    if not _has_pymupdf:
        return None
    try:
        pad = 2.0
        clip = pymupdf.Rect(
            rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)
        pix = page.get_pixmap(
            clip=clip, matrix=pymupdf.Matrix(_FIGURE_ZOOM, _FIGURE_ZOOM))
        target = _FIGURE_EMBED_RATIO * len(pix.tobytes("png"))
        best = None
        for q in (_FIGURE_Q_START, 82, 78, _FIGURE_Q_FLOOR):
            jpg = pix.tobytes("jpeg", jpg_quality=q)
            best = jpg
            if len(jpg) <= target:
                break
        if internal_text.strip():
            for q in (_FIGURE_Q_START, 92, _FIGURE_Q_MAX):
                jpg = pix.tobytes("jpeg", jpg_quality=q)
                if _ocr_token_recall(jpg, internal_text) >= _FIGURE_OCR_RECALL_MIN:
                    return jpg
            return pix.tobytes("jpeg", jpg_quality=_FIGURE_Q_MAX)
        return best
    except Exception:
        return None


def _figure_data_uri(
    page, rect: tuple, internal_text: str = "", cache_path=None,
) -> str | None:
    """``data:image/jpeg;base64,…`` della regione-figura (o None).

    Persistenza (G7): se ``cache_path`` esiste, **riusa** i byte JPEG (dopo un
    riavvio non si ri-encoda né si rifà il gate OCR); altrimenti li calcola e li
    salva. Con ``cache_path=None`` (test) calcola e basta.
    """
    jpg = None
    if cache_path is not None:
        try:
            jpg = Path(cache_path).read_bytes() or None
        except Exception:
            jpg = None
    if not jpg:
        jpg = _figure_jpeg(page, rect, internal_text)
        if jpg and cache_path is not None:
            try:
                p = Path(cache_path)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(jpg)
            except Exception:
                pass
    if not jpg:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(jpg).decode("ascii")


def _link_figures(
    md: str, page, dest_dir, page_num: int, mode: str = "embed", exclude=(),
    skip_captions: set | None = None, skip_rects: list | None = None,
) -> str:
    """Inserisce il corpo-immagine accanto alla didascalia di ogni figura.

    ``mode``: ``"embed"`` (default) inserisce un **data URI JPEG base64** (md
    autocontenuto); ``"link"`` scrive un PNG e inserisce un ``file://`` (per
    debug/back-compat). ``exclude`` sono zone manuali (punti PDF): una figura
    che ricade in una zona esclusa non viene linkata.

    Il testo interno alla figura (etichette/assi, anche quando pymupdf4llm lo
    emette come paragrafo) viene tolto **solo** se sta dentro la regione
    renderizzata, così non resta duplicato con l'immagine. La legenda discorsiva
    (blocco citazione) è conservata sotto l'immagine. Nessun contenuto viene
    eliminato se non è dentro l'immagine.
    """
    if not _has_pymupdf or page is None:
        return md
    if mode == "link" and dest_dir is None:
        return md
    if layout_engine.is_fix_disabled("link_figures"):
        return md
    if not re.search(
        # come _FIGURE_CAPTION_RE: dopo "fig." vale un numero, un separatore o
        # una lettera MAIUSCOLA ("FIG. E3"); "Figure shows that" NON passa.
        r"(?im)^.{0,80}?\bfig(?:ure)?\.?\s*(?:\d|[:\-–—]|(?-i:[A-Z]))", md
    ):
        return md  # nessuna didascalia di figura: nessuna scansione grafica
    regions = _figure_regions(page, exclude)
    if not regions:
        return md
    lines = md.split("\n")
    skipped = {c for c in (skip_captions or set())}
    skip_rects = list(skip_rects or [])
    for i, fig in enumerate(regions, 1):
        # Figura già emessa da un'altra sorgente (es. content map IR): non duplicare.
        if skipped and _norm_text(fig["caption"])[:30] in skipped:
            continue
        # Dedup **geometrica**: stessa regione di una `picture` già emessa dalla
        # content map → è la stessa figura (le didascalie possono differire).
        if skip_rects and _rect_overlaps_any(fig["rect"], skip_rects):
            continue
        internal = _figure_internal_text(page, fig)
        if mode == "link":
            uri = _render_figure(page, fig["rect"], dest_dir, page_num, i)
        else:
            cache_path = None
            if dest_dir is not None:
                cache_path = Path(dest_dir) / f"page_{page_num + 1:04d}_fig_{i}.jpg"
            uri = _figure_data_uri(page, fig["rect"], internal, cache_path)
        if not uri:
            continue
        key = _norm_text(fig["caption"])
        # Confronto anche senza spazi: gestisce "V PE" (nella pagina) vs "VPE"
        # (apice <sup> rimosso dal cleanup) o spaziature dei font.
        key_sq = key.replace(" ", "")
        idx = None
        contains = None
        for j, ln in enumerate(lines):
            n = _norm_text(ln)
            if key[:30] and n.startswith(key[:30]):
                idx = j
                break
            if key_sq and n.replace(" ", "").startswith(key_sq[:30]):
                idx = j
                break
            # Didascalia incastonata in testo OCR/junk: la si trova per
            # contenimento (fallback, usato solo se nessuna riga inizia con la
            # didascalia).
            if contains is None and len(key) >= 15 and key[:25] in n:
                contains = j
        if idx is None:
            idx = contains
        if idx is None:
            continue
        # Toglie il testo interno della figura (già nell'immagine). Non tocca
        # la didascalia né le legende in blocco citazione.
        if internal:
            itokens = set(internal.split())
            kept: list[str] = []
            removed_before = 0
            nlines = len(lines)
            k = 0
            while k < nlines:
                ln = lines[k]
                if k == idx or ln.lstrip().startswith(">"):
                    kept.append(ln)
                    k += 1
                    continue
                # Blocco-tabella **interamente** dentro la figura (es. un
                # pannello EEG o una tabella-didascalia). Viene rimosso in
                # blocco, non riga per riga: altrimenti restano righe lunghe
                # (>8 parole) e separatori spuri (es. `fe22 p207`).
                if ln.lstrip().startswith("|"):
                    j = k
                    block: list[str] = []
                    while j < nlines and lines[j].lstrip().startswith("|"):
                        block.append(lines[j])
                        j += 1
                    if not (k <= idx < j):
                        btokens: set[str] = set()
                        for b in block:
                            btokens |= set(_norm_text(b).split())
                        if btokens and btokens <= itokens:
                            if k < idx:
                                removed_before += len(block)
                            k = j
                            continue
                n = _norm_text(ln)
                if (
                    2 <= len(n) <= 120
                    and len(n.split()) <= 8
                    and (n in internal or set(n.split()) <= itokens)
                ):
                    if k < idx:
                        removed_before += 1
                    k += 1
                    continue
                kept.append(ln)
                k += 1
            if removed_before or len(kept) != len(lines):
                lines = kept
                idx -= removed_before
        legend = None
        k = idx - 1
        while k >= 0 and not lines[k].strip():
            k -= 1
        if k >= 0 and lines[k].lstrip().startswith(">"):
            ks = k
            while ks - 1 >= 0 and lines[ks - 1].lstrip().startswith(">"):
                ks -= 1
            legend = "\n".join(lines[ks:k + 1])
            del lines[ks:k + 1]
            idx -= (k + 1 - ks)
        # corpo-immagine, poi la didascalia, poi la legenda attaccata sotto
        lines.insert(idx, f"![figura {i}]({uri})")
        if legend:
            lines.insert(idx + 2, "")
            lines.insert(idx + 3, legend)
    return "\n".join(lines)


def _column_aware_markdown(page, move_title: bool = False, exclude: tuple = ()) -> str:
    """Reconstruct a page in correct reading order.

    Body text is decomposed into consecutive paragraphs, column by column:
    within each band the left column is emitted top-to-bottom and then the
    right column top-to-bottom. Only elements that span the full page width
    (titles, full-width tables) act as horizontal separators between bands;
    single-column tables stay inside their own column, so they never split
    the other column. Every body paragraph is emitted exactly once.
    """
    page_width = page.rect.width
    page_height = page.rect.height

    # Detect data tables (rendered as markdown) and their bboxes.
    table_regions: list[tuple] = []
    table_items: list[dict] = []  # {y0, x0, x1, md}
    try:
        tabs = page.find_tables()
    except Exception:
        tabs = None
    if tabs:
        for t in tabs.tables:
            if t.row_count <= 1 and t.col_count <= 2:
                continue  # likely a chapter-title block, not a data table
            bbox = tuple(t.bbox)
            if _is_excluded(bbox, exclude):
                continue
            table_regions.append(bbox)
            md = _table_to_md(page, t)
            if md:
                table_items.append(
                    {"y0": bbox[1], "x0": bbox[0], "x1": bbox[2], "md": md}
                )

    # Detect bordered boxes (sidebars) and render them as tables too.
    boxes = _detect_boxes(page, page_width, table_regions, exclude)
    box_regions = [b["rect"] for b in boxes]
    box_titles = {b["title"] for b in boxes if b["title"]}

    def _inside(b: dict, r: tuple) -> bool:
        return (
            b["x0"] >= r[0] - 2 and b["x1"] <= r[2] + 2
            and b["y0"] >= r[1] - 2 and b["y1"] <= r[3] + 2
        )

    blocks = [
        b for b in _collect_blocks(page, exclude)
        if b["max_size"] >= 6.5 or _is_table_legend(b, table_regions)
    ]

    # Il testo **dentro le figure** (etichette degli assi, legende) non deve
    # contare nel rilevamento delle colonne: è più stretto del 60% della
    # pagina e non sta in una tabella, quindi da solo fa da "ponte" e collassa
    # la pagina a una sola colonna, lasciando l'ordine di lettura intrecciato.
    # Resta però nel flusso d'uscita (le legende delle mappe sono contenuto da
    # mostrare — vedi il gold su ha22/p1230); la didascalia ("FIGURE n …") è
    # contenuto e non va mai esclusa. Vedi ha22/p101.
    figure_text = layout_engine.figure_text_regions(page, exclude)

    def _fig_text(b: dict) -> bool:
        t = " ".join(s["text"] for line in b["lines"] for s in line)
        if _FIGURE_CAPTION_RE.match(re.sub(r"\s+", " ", t).strip()):
            return False  # didascalia: contenuto, non testo-figura
        return any(_inside(b, r) for r in figure_text)

    full_width: list[dict] = []  # spans the page → separator
    body: list[dict] = []        # column paragraphs (outside tables/boxes)
    for b in blocks:
        if any(_inside(b, r) for r in table_regions):
            continue  # covered by the markdown table
        if any(_inside(b, r) for r in box_regions):
            continue  # covered by the box table
        if box_titles:
            t = " ".join(s["text"] for line in b["lines"] for s in line)
            if re.sub(r"\s+", " ", t).strip() in box_titles:
                continue  # box title rendered above the box table
        w = b["x1"] - b["x0"]
        if w >= 0.6 * page_width:
            full_width.append(b)
        elif w >= 25:
            body.append(b)

    # Robust column boundaries, computed from all non-table blocks (incl. box
    # content): a column that is entirely a box must still count as a column,
    # otherwise the layout collapses to single-column and the box is misordered.
    split_blocks = [
        b for b in blocks
        if not any(_inside(b, r) for r in table_regions) and not _fig_text(b)
    ]
    # Headers/footers/watermarks must not bridge the column gap (they would
    # collapse the page to a single column).
    splits = _detect_column_splits(
        _strip_margin_blocks(split_blocks, page_height), page_width
    )

    def _col_of(x: float) -> int:
        return sum(1 for s in splits if x > s)

    # Move chapter title(s) out of the column flow when requested.
    titles: list[dict] = []
    if move_title:
        first_split = splits[0] if splits else page_width + 1
        titles = [b for b in body if b["max_size"] >= 14 and b["x0"] < first_split]
        body = [b for b in body if b not in titles]
        titles.sort(key=lambda b: b["max_size"])

    # Full-width separators: full-width blocks + full-width tables + boxes.
    separators: list[tuple[float, str]] = []
    for b in full_width:
        md = _block_to_md(b, as_column=False)
        if md:
            separators.append((b["y0"], md))
    for ti in table_items:
        if (ti["x1"] - ti["x0"]) >= 0.6 * page_width:
            separators.append((ti["y0"], ti["md"]))
    for bx in boxes:
        if (bx["rect"][2] - bx["rect"][0]) >= 0.6 * page_width:
            md = f"**{bx['title']}**\n\n{bx['md']}" if bx["title"] else bx["md"]
            separators.append((bx["rect"][1], md))
    separators.sort(key=lambda s: s[0])

    # Column paragraphs, decomposed top-to-bottom: (y0, x0, md).
    n_cols = len(splits) + 1
    col_items: list[list[tuple[float, float, str]]] = [[] for _ in range(n_cols)]
    for b in body:
        md = _block_to_md(b, as_column=True)
        if not md:
            continue
        item = (b["y0"], b["x0"], md)
        col_items[_col_of((b["x0"] + b["x1"]) / 2)].append(item)
    # Single-column tables and in-column boxes belong to their column, at y.
    for ti in table_items:
        if (ti["x1"] - ti["x0"]) >= 0.6 * page_width:
            continue
        mid = (ti["x0"] + ti["x1"]) / 2
        item = (ti["y0"], ti["x0"], ti["md"])
        col_items[_col_of(mid)].append(item)
    for bx in boxes:
        if (bx["rect"][2] - bx["rect"][0]) >= 0.6 * page_width:
            continue
        mid = (bx["rect"][0] + bx["rect"][2]) / 2
        md = f"**{bx['title']}**\n\n{bx['md']}" if bx["title"] else bx["md"]
        item = (bx["rect"][1], bx["rect"][0], md)
        col_items[_col_of(mid)].append(item)
    for items in col_items:
        items.sort(key=lambda it: (it[0], it[1]))

    out: list[str] = []
    for t in titles:
        out.append(_block_to_md(t, as_column=True))

    sep_marks = [y0 for y0, _ in separators]

    def _band(y0: float) -> int:
        return sum(1 for sy in sep_marks if y0 >= sy)

    n_seps = len(separators)
    for band_idx in range(n_seps + 1):
        for items in col_items:
            out.extend(md for y0, _x, md in items if _band(y0) == band_idx)
        if band_idx < n_seps:
            out.append(separators[band_idx][1])

    return "\n\n".join(out)


def _collect_lines(page) -> list[dict]:
    """Text LINES with bbox + per-span formatting, in document order.

    Unlike ``_collect_blocks`` (which keeps whole paragraphs), this keeps the
    line granularity with each line's bbox and source-block index, so a small
    inclusion zone can capture just a few lines of a larger paragraph.
    """
    lines: list[dict] = []
    for bi, blk in enumerate(page.get_text("dict")["blocks"]):
        if blk.get("type") != 0:
            continue
        for line in blk["lines"]:
            spans: list[dict] = []
            size = 0.0
            for s in line["spans"]:
                t = s["text"]
                if t == "":
                    continue
                spans.append(
                    {
                        "text": t,
                        "size": s["size"],
                        "bold": bool(s["flags"] & 16),
                        "italic": bool(s["flags"] & 2),
                        "x0": s["bbox"][0],
                        "x1": s["bbox"][2],
                    }
                )
                size = max(size, s["size"])
            spans = _trim_edge_spaces(spans)
            if not spans:
                continue
            x0, y0, x1, y1 = line["bbox"]
            lines.append(
                {
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "max_size": size, "spans": spans, "blk": bi,
                }
            )
    return lines


def _lines_to_block(lns: list[dict]) -> dict:
    """Reassemble a group of lines into a block dict for ``_block_to_md``."""
    return {
        "x0": min(l["x0"] for l in lns), "y0": min(l["y0"] for l in lns),
        "x1": max(l["x1"] for l in lns), "y1": max(l["y1"] for l in lns),
        "max_size": max(l["max_size"] for l in lns),
        "lines": [[dict(s) for s in l["spans"]] for l in lns],
    }


def _line_rect(ln: dict) -> tuple:
    return (ln["x0"], ln["y0"], ln["x1"], ln["y1"])


def _inclusion_order_markdown(page, zones, exclude: tuple = ()) -> str:
    """Emit text following the numbered inclusion zones (whitelist + order).

    ``zones`` are (x0, y0, x1, y1) PDF rects in reading order: index 0 is box
    1, index 1 is box 2, etc. Works at LINE level: every line whose area is
    >=50% inside a zone is kept (so a small zone over two/three lines works
    even when the rest of the paragraph is outside). Lines are emitted zone by
    zone, top-to-bottom (then left-to-right) within each zone; consecutive
    lines of the same paragraph stay joined. ``exclude`` zones are dropped
    first (red wins), so the two manual tools compose: red removes noise,
    green sets the order.
    """
    lines = _collect_lines(page)
    if exclude:
        lines = [ln for ln in lines if not _is_excluded(_line_rect(ln), exclude)]

    emitted: set[int] = set()
    out: list[str] = []
    for zone in zones:
        inside = [
            (i, ln) for i, ln in enumerate(lines)
            if i not in emitted and _is_excluded(_line_rect(ln), (zone,))
        ]
        inside.sort(key=lambda il: (il[1]["y0"], il[1]["x0"]))
        # Raggruppa righe consecutive dello stesso blocco in un paragrafo.
        groups: list[tuple[int, list[int]]] = []
        for i, ln in inside:
            if groups and groups[-1][0] == ln["blk"]:
                groups[-1][1].append(i)
            else:
                groups.append((ln["blk"], [i]))
        for _blk, idxs in groups:
            emitted.update(idxs)
            md = _block_to_md(_lines_to_block([lines[i] for i in idxs]), as_column=True)
            if md:
                out.append(md)
    return "\n\n".join(out)


def _page_needs_column_reorder(page) -> bool:
    """Heuristic: does this page need the two-column reorder fix?

    True only when a clear column split exists, both columns are populated
    (≥2 blocks each) and they run side by side (their blocks overlap
    vertically) — the exact condition where a line-by-line extraction
    interleaves the two columns.
    """
    # Data tables are rendered separately; exclude their cells from the split
    # detection (same rule as _column_aware_markdown).
    table_regions: list[tuple] = []
    try:
        tabs = page.find_tables()
    except Exception:
        tabs = None
    if tabs:
        for t in tabs.tables:
            if t.row_count <= 1 and t.col_count <= 2:
                continue  # likely a chapter-title block, not a data table
            table_regions.append(tuple(t.bbox))

    def _inside(b: dict, r: tuple) -> bool:
        return (
            b["x0"] >= r[0] - 2 and b["x1"] <= r[2] + 2
            and b["y0"] >= r[1] - 2 and b["y1"] <= r[3] + 2
        )

    blocks = [
        b for b in _collect_blocks(page)
        if b["max_size"] >= 6.5 and not any(_inside(b, r) for r in table_regions)
    ]
    page_width = page.rect.width
    # Same column filter as _column_aware_markdown: narrow, but not stray marks.
    columns = [
        b for b in blocks
        if (b["x1"] - b["x0"]) < 0.6 * page_width and (b["x1"] - b["x0"]) >= 25
    ]
    # Headers/footers/watermarks must not bridge the column gap.
    columns = _strip_margin_blocks(columns, page.rect.height)
    splits = _detect_column_splits(columns, page_width)
    if not splits:
        return False

    left = [b for b in columns if b["x1"] <= splits[0]]
    right = [b for b in columns if b["x0"] >= splits[0]]
    if len(left) < 2 or len(right) < 2:
        return False

    return any(
        lb["y0"] <= rb["y1"] and rb["y0"] <= lb["y1"]
        for lb in left
        for rb in right
    )


def _reading_normalize(text: str) -> str:
    """Collapse whitespace + de-hyphenate line breaks, lowercased.

    Used to align extracted markdown against pymupdf's per-column raw text
    (the two may differ in line breaks and end-of-line hyphenation).
    """
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


def _longest_prefix_in(s: str, target: str) -> int:
    """Length of the longest word-aligned prefix of ``s`` that is in ``target``."""
    for i in range(len(s), -1, -1):
        if i < len(s) and s[i] != " ":
            continue  # not a word boundary
        if s[:i] in target:
            return i
    return 0


def _longest_suffix_in(s: str, target: str) -> int:
    """Length of the longest word-aligned suffix of ``s`` that is in ``target``."""
    for i in range(len(s), -1, -1):
        start = len(s) - i
        if start > 0 and s[start - 1] != " ":
            continue  # suffix doesn't start at a word boundary
        if s[-i:] in target:
            return i
    return 0


def _split_cross_column_paragraphs(md: str, page) -> str:
    """Re-split paragraphs that a backend glued across the two-column boundary.

    Docling's layout model occasionally merges a left-column element (e.g. the
    last cell of a side box) with the right column's opening sentence into a
    single paragraph. Using the detected column split and pymupdf's per-column
    raw text, a paragraph whose head lives in the left column and whose tail
    lives in the right column is split at that boundary.

    Degrades to ``md`` unchanged when no clear column split exists.
    """
    if not _has_pymupdf:
        return md
    # Headers/footers/watermarks must not bridge the column gap (they would
    # hide the real split and disable the re-split).
    blocks = _strip_margin_blocks(_collect_blocks(page), page.rect.height)
    split = _detect_column_split(blocks, page.rect.width)
    if split is None:
        return md
    width, height = page.rect.width, page.rect.height
    left_text = _reading_normalize(
        page.get_text(clip=pymupdf.Rect(0, 0, split, height))
    )
    right_text = _reading_normalize(
        page.get_text(clip=pymupdf.Rect(split, 0, width, height))
    )

    out: list[str] = []
    for para in md.split("\n\n"):
        stripped = para.strip()
        words = stripped.split()
        if len(words) < 8:
            out.append(stripped)
            continue
        norm = _reading_normalize(stripped)
        p = _longest_prefix_in(norm, left_text)
        s = _longest_suffix_in(norm, right_text)
        if not (p > 0 and s > 0 and p + s >= len(norm) - 1 and p < len(norm) - s):
            out.append(stripped)
            continue
        cut = len(norm[:p].split())
        if not (3 <= cut <= len(words) - 3):
            out.append(stripped)
            continue
        out.append(" ".join(words[:cut]))
        out.append(" ".join(words[cut:]))
    return "\n\n".join(p for p in out if p)


def _spacing_fixes(md: str) -> str:
    """Generic cosmetic spacing fixes for markdown artifacts."""
    # bold chapter cross-reference glued to the following word: **134**and
    md = re.sub(r"\*\*(\d+)\*\*(?=\S)", r"**\1** ", md)
    # underscore-italic word followed by a comma glued to the next word: _a_,_b_
    md = re.sub(r"(_[^_]+_),", r"\1, ", md)
    return md


# ═══════════════════════════════════════════════════════════════════════════════
#  Translation engines — Google Translate and Microsoft Edge (stdlib only, no API key)
# ═══════════════════════════════════════════════════════════════════════════════

_GT_URL = "https://translate.googleapis.com/translate_a/single"
_GT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


# Google progressively blocks the legacy "gtx" client (HTTP 429 "Sorry...")
# on many networks/IPs. Three *free, no-key* endpoints are tried in order:
#   1. the Chrome-extension client "dict-chrome-ex" on the classic endpoint;
#   2. Google's "translate-pa" endpoint with its embedded public browser key
#      (the same endpoint the bookfere calibre plugin ships as
#      "Google (Free) - New");
#   3. the legacy "gtx" client as a last-resort safety net.
_GT_PA_URL = "https://translate-pa.googleapis.com/v1/translate"
_GT_PA_KEY = "AIzaSyDLEeFI5OtFBwYBIoK_jj5m32rZK5CkCXA"


def _gt_request(url: str, params: dict) -> object:
    """GET a Google endpoint and return the parsed JSON (raises on failure)."""
    full_url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url, headers=_GT_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _gt_segments(text: str, source: str, target: str, client: str) -> str:
    """Classic translate_a/single response: list of [text, ...] segments."""
    result = _gt_request(_GT_URL, {
        "client": client, "sl": source, "tl": target, "dt": "t", "q": text,
    })
    if result and result[0]:
        return "".join(item[0] for item in result[0] if item[0])
    return ""


def _gt_pa(text: str, source: str, target: str) -> str:
    """translate-pa v1/translate response: {"translation": "..."}."""
    result = _gt_request(_GT_PA_URL, {
        "params.client": "gtx",
        "query.source_language": source,
        "query.target_language": target,
        "query.display_language": "en-US",
        "data_types": "TRANSLATION",
        "key": _GT_PA_KEY,
        "query.text": text,
    })
    if isinstance(result, dict) and result.get("translation"):
        return result["translation"]
    return ""


def _gt_translate_one(text: str, source: str, target: str) -> str:
    """Call a free Google Translate endpoint for a single chunk of text.

    Only network/HTTP failures move to the next endpoint; an answered-but-
    empty response stops the chain (the engine is reachable, there is simply
    nothing to return).
    """
    attempts = (
        lambda: _gt_segments(text, source, target, "dict-chrome-ex"),
        lambda: _gt_pa(text, source, target),
        lambda: _gt_segments(text, source, target, "gtx"),
    )
    last_error: Exception | None = None
    for attempt in attempts:
        try:
            out = attempt()
            if out:
                return out
            return text
        except Exception as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    return text


# Free Microsoft Edge endpoint (same scheme as the Ebook Translator calibre
# plugin): POST a JSON array of strings, get translations back in order.
_MS_URL = "https://edge.microsoft.com/translate/translatetext"
# Microsoft uses different codes than Google for a few languages.
_MS_LANG_CODES = {
    "zh": "zh-Hans",  # Google "zh" = Simplified Chinese
}


def _ms_lang_code(code: str) -> str:
    """Map an app language code to the Microsoft Edge API code."""
    return _MS_LANG_CODES.get(code, code)


def _ms_translate_one(text: str, source: str, target: str) -> str:
    """Call the free Microsoft Edge Translate API for a single chunk."""
    params = {"isEnterpriseClient": "False", "to": _ms_lang_code(target)}
    if source and source != "auto":
        params["from"] = _ms_lang_code(source)
    full_url = f"{_MS_URL}?{urllib.parse.urlencode(params)}"
    body = json.dumps([text]).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(
        full_url, data=body, headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    try:
        return result[0]["translations"][0]["text"]
    except (IndexError, KeyError, TypeError):
        return text


def _translate_one(
    engine: str, text: str, source: str, target: str,
    _stats: _TranslateStats | None = None,
) -> str:
    """Translate a single chunk with the chosen engine (google|microsoft)."""
    if _stats is not None:
        _stats.attempted()
    if engine == "microsoft":
        out = _ms_translate_one(text, source, target)
    else:
        out = _gt_translate_one(text, source, target)
    if _stats is not None:
        _stats.succeeded()
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  Tesseract OCR (scanned PDFs)
# ═══════════════════════════════════════════════════════════════════════════════

# App language code → Tesseract traineddata (tessdata_fast) name.
_TESS_LANG_CODES = {
    "en": "eng", "it": "ita", "fr": "fra", "de": "deu", "es": "spa",
    "pt": "por", "nl": "nld", "pl": "pol", "ru": "rus", "zh": "chi_sim",
    "ja": "jpn", "ko": "kor", "ar": "ara", "tr": "tur",
}
# Source "auto" → Tesseract language combination: best match per line among
# the Latin/Cyrillic/RTL languages bundled with the app. CJK is excluded
# because mixing scripts degrades accuracy — for zh/ja/ko documents the user
# sets the source language explicitly (and the resource is bundled anyway).
_AUTO_TESS_LANGS = "eng+deu+fra+ita+spa+por+nld+pol+rus+tur+ara"


def _tess_lang_code(code: str | None) -> str:
    """Map an app language code to the Tesseract OCR language string."""
    if not code or code == "auto":
        return _AUTO_TESS_LANGS
    return _TESS_LANG_CODES.get(code, "eng")


def _setup_bundled_tesseract() -> None:
    """Point PyMuPDF OCR at the Tesseract bundled in frozen (PyInstaller) builds.

    In a frozen app the ``tesseract`` binary, its shared libraries (``lib/``)
    and the ``tessdata/`` directory land next to the extracted payload
    (``sys._MEIPASS``).  Make them discoverable via PATH / TESSDATA_PREFIX and
    LD_LIBRARY_PATH / DYLD_LIBRARY_PATH (appended, so the app's own bundled
    libraries keep precedence) so MuPDF's OCR works without a system install.
    """
    if not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    tess_exe = os.path.join(base, "tesseract.exe" if os.name == "nt" else "tesseract")
    if os.path.exists(tess_exe):
        os.environ["PATH"] = base + os.pathsep + os.environ.get("PATH", "")
    lib_dir = os.path.join(base, "lib")
    if os.path.isdir(lib_dir):
        if os.name == "nt":
            os.environ["PATH"] = (
                os.environ.get("PATH", "") + os.pathsep + lib_dir
            )
        else:
            existing = os.environ.get("LD_LIBRARY_PATH") or ""
            os.environ["LD_LIBRARY_PATH"] = (
                existing + os.pathsep + lib_dir if existing else lib_dir
            )
            existing = os.environ.get("DYLD_LIBRARY_PATH") or ""
            os.environ["DYLD_LIBRARY_PATH"] = (
                existing + os.pathsep + lib_dir if existing else lib_dir
            )
    tessdata = os.path.join(base, "tessdata")
    if os.path.isdir(tessdata):
        os.environ["TESSDATA_PREFIX"] = tessdata


#: Revisione dello schema/dell'output della cache di estrazione. Va incrementata
#: ogni volta che un fix cambia il markdown finale: al load i "final" salvati con
#: una revisione diversa vengono **scartati** (i "raw" restano), così l'engine
#: rigira da solo e l'utente non rivede output vecchi dopo un aggiornamento.
#: La revisione 2 aggiunge la **pipeline reale** (`ir`/`current`) al record
#: finalizzato: i "final" di revisione 1 non la contengono e vengono scartati.
_CACHE_REVISION = 2


def _extract_pymupdf4llm(
    path: str, page_num: int, ocr_language: str | None = None
) -> str:
    """Extract a page to Markdown (native text or Tesseract OCR for scans).

    ``ocr_language`` is a Tesseract language or "+"-joined combination.  If
    the requested language is unavailable (e.g. the auto combination on a
    system that bundles only English), it degrades to ``eng`` before giving
    up, so extraction never hard-fails on a language mismatch.
    """
    if not _has_pymupdf4llm:
        return T("extract.no_pymupdf4llm")
    attempts = [ocr_language] if ocr_language else ["eng"]
    if attempts[-1] != "eng":
        attempts.append("eng")
    import ir_layout  # lazy: il lock OCR condiviso con la pipeline IR

    last_error: Exception | None = None
    for lang in attempts:
        try:
            kwargs: dict = {}
            if lang:
                kwargs["ocr_language"] = lang
            # Pagine con testo a 90° (landscape senza /Rotate): raddrizza prima
            # di estrarre, altrimenti pymupdf4llm legge lungo l'asse sbagliato.
            md = None
            # Serializza l'OCR (Leptonica non è thread-safe): vedi
            # ir_layout._PDF_OCR_LOCK.
            with ir_layout._PDF_OCR_LOCK:
                try:
                    with pymupdf.open(path) as doc:
                        rot = layout_engine.detect_sideways_rotation(doc[page_num])
                        if rot:
                            doc[page_num].set_rotation(rot)
                            md = pymupdf4llm.to_markdown(
                                doc, pages=[page_num], **kwargs)
                except Exception:
                    md = None
                if md is None:
                    md = pymupdf4llm.to_markdown(path, pages=[page_num], **kwargs)
            return md.strip() or T("extract.empty_page")
        except Exception as e:  # noqa: BLE001 — degrada al fallback, non crasha
            last_error = e
    return T("extract.error", e=last_error)


def _apply_engine_on_page(
    page, text: str, exclude: tuple = (), include: tuple = (),
    figures_dir=None, page_num: int = 0, figure_mode: str = "embed",
) -> tuple[str, str]:
    """Apply the adaptive layout engine to ``text`` using a pymupdf page.

    ``include`` (numbered inclusion zones, reading order) takes precedence:
    it rebuilds the text as a whitelist ordered by zone number.  Otherwise
    the v1 pipeline applies: with ``exclude`` non-empty the page is rebuilt
    skipping those zones (manual cleaning) before the cosmetic fixes; with
    neither, the automatic plan is applied unchanged.  Returns
    ``(text, label)`` where label is "auto" or "manual".  This is CPU-bound
    (profile_page analyzes the page) and slow on scanned PDFs, so callers
    run it off the GUI thread.

    When ``figures_dir`` is given, figures are linked next to their caption:
    ``figure_mode="embed"`` (default) inserts a **JPEG base64 data URI** (md
    autocontenuto); ``figure_mode="link"`` writes a PNG and inserts a ``file://``.
    """
    label = "manual" if (include or exclude) else "auto"
    rot = 0
    if page is not None:
        try:
            rot = layout_engine.detect_sideways_rotation(page)
            if rot:
                page.set_rotation(rot)
        except Exception:
            rot = 0
    try:
        if include:
            result = _inclusion_order_markdown(page, include, exclude=exclude) or text
        else:
            profile = layout_engine.profile_page(page, exclude=exclude)
            plan = layout_engine.plan_fixes(profile, "PyMuPDF4LLM ⚡", mode="auto")
            if exclude:
                cleaned = _column_aware_markdown(page, exclude=exclude) or text
                plan = [f for f in plan if f.id != "reorder_columns"]
                result = layout_engine.apply_plan(cleaned, page, profile, plan) or cleaned
            else:
                result = layout_engine.apply_plan(text, page, profile, plan) or text
    except Exception:
        return text, label
    finally:
        if rot:
            try:
                page.set_rotation(0)
            except Exception:
                pass
    if figures_dir is not None:
        try:
            result = _link_figures(
                result, page, figures_dir, page_num,
                mode=figure_mode, exclude=exclude,
            )
        except Exception:
            pass
    return result, label


def _apply_engine_standalone(
    path: str, page_num: int, text: str, exclude: tuple = (), include: tuple = (),
    figures_dir=None, figure_mode: str = "embed",
) -> tuple[str, str]:
    """Apply the layout engine on a freshly opened document (background thread)."""
    label = "manual" if (include or exclude) else "auto"
    try:
        with pymupdf.open(path) as doc:
            return _apply_engine_on_page(
                doc[page_num], text, exclude=exclude, include=include,
                figures_dir=figures_dir, page_num=page_num,
                figure_mode=figure_mode,
            )
    except Exception:
        return text, label


def _pipeline_mode() -> str:
    """Pipeline attiva: ``"ir"`` (default) o ``"current"`` (legacy/fallback).

    IR è la pipeline di default (content map + cosmetica + figura unite, con
    gate d'integrità che ricade su ``current`` pagina per pagina). Si può
    forzare ``current`` con la variabile ``NOESIS_PIPELINE=current`` (o il
    setting ``pipeline``).
    """
    v = ""
    try:
        v = str(get_setting("pipeline", "") or "")
    except Exception:
        v = ""
    v = (v or os.environ.get("NOESIS_PIPELINE", "") or "").strip().lower()
    return v if v in ("current", "ir") else "ir"


def _cosmetic_ir(md: str) -> str:
    """Pulizia cosmetica del markdown IR — **nessun fix strutturale**.

    La struttura (colonne, tabelle, header/footer, tipi di blocco) arriva già
    dalla content map: qui restano solo i ritocchi tipografici che il modello
    non fa (tag HTML residui, enfasi, soft-hyphen/FFFD, spaziature, liste).
    Non si portano i fix strutturali (reorder, column-aware, header/footer).
    """
    try:
        md = layout_engine._normalize_html_tags(md)
        md = layout_engine._normalize_replacement_chars(md)
        md = layout_engine._normalize_soft_hyphens(md)
        md = layout_engine._repair_lists(md)
        md = layout_engine._drop_orphan_bullets(md)
        md = layout_engine._normalize_emphasis(md)
        md = layout_engine._despace_numbers(md)
        md = layout_engine._despace_blockquote_letters(md)
        md = md.replace("~~", "").replace("■", "")
    except Exception:
        pass
    return md


def _page_alpha_tokens(page) -> set[str]:
    """Token alfabetici della pagina (lowercased), da ``get_text("text")``.

    Letto sulla pagina **pristina** (prima dell'OCR). ``text`` unisce gli span
    adiacenti, quindi ricostruisce le parole intere (es. ``Esophageal`` anche
    quando l'iniziale è in grassetto) — al contrario di ``get_text("words")``.
    """
    toks: set[str] = set()
    try:
        for t in re.findall(r"[A-Za-z]+", page.get_text("text") or ""):
            if len(t) >= 2:
                toks.add(t.lower())
    except Exception:
        pass
    return toks


#: iniziale in grassetto staccata dalla parola (mnemoniche/drop-cap).
_BOLD_INIT_RE = re.compile(r"\*\*([A-Za-z])\*\* ([a-z][A-Za-z]*)")


def _fix_bold_initials(md: str, vocab: set[str]) -> str:
    """Unisce l'iniziale in grassetto alla parola **solo** se la forma unita è
    una parola realmente presente nella pagina (grounded).

    ``**E** sophageal`` → ``**E**sophageal`` (mnemonica ERP: la parola esiste).
    **Non** tocca «vitamin **D** deficiency» perché ``Ddeficiency`` non è una
    parola di pagina. Deterministico, nessun dizionario esterno.
    """
    if not vocab or not md:
        return md

    def repl(m: re.Match) -> str:
        if (m.group(1) + m.group(2)).lower() in vocab:
            return f"**{m.group(1)}**{m.group(2)}"
        return m.group(0)

    return _BOLD_INIT_RE.sub(repl, md)


# ── scelta IR vs `current` (gate + chooser) ─────────────────────────────────
_CHOOSER_FIG_RECALL_SLACK = 0.15   # IR può perdere recall se porta più figure
_CHOOSER_RECALL_MARGIN = 0.02      # recall (quasi) migliore → IR


def _apply_ir_on_page_full(path: str, page_num: int, figures_dir=None, exclude=()):
    """Come ``_apply_ir_on_page`` ma restituisce anche ``elements`` e il motivo
    del gate (servono al chooser IR/current)."""
    import ir_layout

    with pymupdf.open(path) as doc:
        page = doc[page_num]
        # vocabolo di pagina **pristino** (prima che l'OCR di pymupdf4llm la muti):
        # serve al fix grounded dell'iniziale in grassetto.
        vocab = _page_alpha_tokens(page)
        chunk = ir_layout.page_chunk(doc, page_num)  # UNA sola passata
        md, meta = ir_layout.build_markdown(
            page, doc, page_num, figures_dir=figures_dir,
            embed_figures=True, return_meta=True, chunk=chunk)
        try:
            _t, elements = ir_layout._elements_from_chunk(chunk)
        except Exception:
            elements = []
        # Pannello-immagine (raster) classificato `table` dalla content map:
        # il suo testo è OCR-spazzatura, già reso dall'immagine della figura.
        # Va tolto dal md (niente doppio testo+immagine, es. `ha22 p3355`).
        raster_tables = _raster_figure_table_rects(page, elements)
        if raster_tables:
            md = _strip_table_blocks(md, elements, raster_tables)
        md = _link_figures(md, page, figures_dir, page_num, mode="embed",
                           exclude=exclude, skip_captions=meta["captions"],
                           skip_rects=meta["rects"])
        md = _cosmetic_ir(md)
        md = _fix_bold_initials(md, vocab)
        raw = chunk.get("text", "") or ""
        gate_ok, reason = _ir_gate(page, md, elements)
        # tabella ruotata: l'IR la rende come immagine (fedele), quindi è
        # "gestita" — non si ricade su `current` (che qui è peggiore).
        if not gate_ok and reason == "tabella-ruotata":
            gate_ok = True
    return md, raw, gate_ok, elements, reason


def _apply_ir_on_page(path: str, page_num: int, figures_dir=None, exclude=()):
    """Markdown della pagina con la pipeline IR + **figura-detection unita**.

    Una **sola** passata di layout (``page_chunk``), usata sia per la content map
    sia come "raw" per la cache. Restituisce ``(markdown, raw, gate_ok)``.
    """
    md, raw, gate_ok, _els, _reason = _apply_ir_on_page_full(
        path, page_num, figures_dir, exclude)
    return md, raw, gate_ok


def _choose_output(page, elements: list[dict], ir_md: str, cur_md: str) -> str:
    """Sceglie l'output migliore tra IR e ``current`` quando il gate è incerto.

    Preferisce **IR** se porta più figure di ``current`` e non perde troppo
    recall (il testo "perso" è dentro l'immagine, che IR incorpora), oppure se
    ha recall nettamente migliore. Altrimenti resta ``current``: **non si
    peggiora mai l'output attuale** se IR è chiaramente peggiore.
    """
    ref = _page_text_no_figures(page, elements)
    if _norm_text(ref).strip() == "":
        return ir_md  # pagina di sole figure: IR le incorpora
    r_ir = _word_recall(ref, _strip_images(ir_md))
    r_cur = _word_recall(ref, _strip_images(cur_md))
    f_ir = ir_md.count("data:image")
    f_cur = cur_md.count("data:image")
    if f_ir > f_cur and r_ir >= r_cur - _CHOOSER_FIG_RECALL_SLACK:
        return ir_md
    if r_ir >= r_cur + _CHOOSER_RECALL_MARGIN:
        return ir_md
    return cur_md


def _select_page_output(path: str, page_num: int, figures_dir=None):
    """Decisione completa di pagina: gate + chooser IR/``current``.

    Restituisce ``(testo, engine, raw)`` con ``engine`` in ``{"ir", "current"}``.
    Usata sia dall'app sia dall'harness E2E per coerenza.
    """
    md, raw, gate_ok, elements, _reason = _apply_ir_on_page_full(
        path, page_num, figures_dir)
    if not (md and _norm_text(md).strip()):
        with pymupdf.open(path) as doc:
            cur_md, _ = _apply_engine_on_page(
                doc[page_num], raw, figures_dir=figures_dir, page_num=page_num)
        return cur_md, "current", raw
    if gate_ok:
        return md, "ir", raw
    with pymupdf.open(path) as doc:
        cur_md, _ = _apply_engine_on_page(
            doc[page_num], raw, figures_dir=figures_dir, page_num=page_num)
        chosen = _choose_output(doc[page_num], elements, md, cur_md)
    return chosen, ("ir" if chosen is md else "current"), raw


# Markdown structural patterns protected during translation.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_TABLE_RE = re.compile(
    r"^\s*\|[^\n]*\|\s*\n\s*\|[\s:|-]+\|\s*\n(?:\s*\|[^\n]*\|\s*\n?)+",
    re.MULTILINE,
)
_SEP_CELL_RE = re.compile(r":?-{3,}:?")


_MAX_WORKERS = 8  # concurrent translation requests (I/O-bound)


class TranslationError(RuntimeError):
    """Raised when a translation engine fails on every chunk of a request.

    Individual chunk failures still degrade gracefully to the original text;
    only a *total* failure (engine unreachable/blocked on this network) is
    raised so the UI can surface it instead of silently showing untranslated
    text.
    """


class _TranslateStats:
    """Attempt/ok counters shared across the concurrent translation chunks.

    ``list.append`` is atomic under the GIL, so no lock is needed even though
    paragraphs (and their nested table-cell/sub-chunk calls) run in worker
    threads.
    """

    def __init__(self) -> None:
        self._attempts: list[int] = []
        self._ok: list[int] = []

    @property
    def attempts(self) -> int:
        return len(self._attempts)

    @property
    def ok(self) -> int:
        return len(self._ok)

    def attempted(self) -> None:
        self._attempts.append(1)

    def succeeded(self) -> None:
        self._ok.append(1)


def _translate_cell(
    cell: str, source: str, target: str, engine: str = "google",
    _stats: _TranslateStats | None = None,
) -> str:
    """Translate one table cell, falling back to the original on failure."""
    try:
        return _translate_one(
            engine, cell, source, target, _stats=_stats
        ).strip()
    except Exception:
        return cell


def _translate_table(
    table: str, source: str, target: str, engine: str = "google",
    _stats: _TranslateStats | None = None,
) -> str:
    """Translate the cell contents of a markdown table, keeping its structure.

    All distinct translatable cells are fetched concurrently (one request per
    cell), then placed back in their original positions.
    """
    lines = [ln.strip() for ln in table.strip().splitlines()]
    out: list[str] = []

    def _cells(ln: str) -> list[str]:
        return [c.strip() for c in ln.strip().strip("|").split("|")]

    # Collect the distinct cells that actually need translation.
    unique: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        if not ln.startswith("|"):
            continue
        cells = _cells(ln)
        if all(_SEP_CELL_RE.fullmatch(c) for c in cells):
            continue  # separator row kept verbatim
        for c in cells:
            if c and re.search(r"[A-Za-z]", c) and c not in seen:
                seen.add(c)
                unique.append(c)

    cache: dict[str, str] = {}
    if unique:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(_MAX_WORKERS, len(unique))
        ) as pool:
            futures = {
                pool.submit(_translate_cell, c, source, target, engine, _stats): c
                for c in unique
            }
            for fut in concurrent.futures.as_completed(futures):
                cache[futures[fut]] = fut.result()

    for ln in lines:
        if not ln.startswith("|"):
            out.append(ln)
            continue
        cells = _cells(ln)
        if all(_SEP_CELL_RE.fullmatch(c) for c in cells):
            out.append(ln)  # separator row kept verbatim
            continue
        translated: list[str] = []
        for c in cells:
            # Numbers/symbol-only cells are left untouched (faster, safer).
            if not c or not re.search(r"[A-Za-z]", c):
                translated.append(c)
            else:
                translated.append(cache.get(c, c))
        out.append("| " + " | ".join(translated) + " |")
    return "\n".join(out)


def _translate_paragraph(
    para: str,
    source: str,
    target: str,
    chunk_size: int,
    engine: str = "google",
    _stats: _TranslateStats | None = None,
) -> str:
    """Translate one paragraph, protecting markdown tables and image links."""
    protected: dict[str, str] = {}

    def _protect(kind: str, value: str) -> str:
        tok = f"@@{kind}{len(protected)}@@"
        protected[tok] = value
        return tok

    # Image links: never send URLs to the translator.
    para = _MD_IMAGE_RE.sub(lambda m: _protect("IMG", m.group(0)), para)

    # Tables: translate their cells, then protect the rebuilt table.
    def _table_repl(m):
        return _protect(
            "TBL", _translate_table(m.group(0), source, target, engine, _stats)
        )

    para = _MD_TABLE_RE.sub(_table_repl, para)

    # Nothing left to translate (only protected tokens) → skip the API call.
    if not re.search(r"[^\W\d_]", re.sub(r"@@[A-Z]+\d+@@", "", para)):
        out = para
    elif len(para) <= chunk_size:
        try:
            out = _translate_one(engine, para, source, target, _stats=_stats)
        except Exception:
            out = para
    else:
        # Long paragraph → split at sentence-ish boundaries.
        sub_paras = re.split(r"(?<=[.!?])\s+", para)
        sub_chunks: list[str] = []
        current: list[str] = []
        cur_len = 0
        for sub in sub_paras:
            if cur_len + len(sub) > chunk_size and current:
                sub_chunks.append(" ".join(current))
                current = []
                cur_len = 0
            current.append(sub)
            cur_len += len(sub)
        if current:
            sub_chunks.append(" ".join(current))
        sub_translated: list[str] = []
        for ch in sub_chunks:
            try:
                sub_translated.append(
                    _translate_one(engine, ch, source, target, _stats=_stats)
                )
            except Exception:
                sub_translated.append(ch)
        out = " ".join(sub_translated)

    # Google may add spaces around/inside tokens; normalize them back.
    out = re.sub(r"@@\s*([A-Z]+)\s*(\d+)\s*@@", r"@@\1\2@@", out)
    for tok, value in protected.items():
        out = out.replace(tok, value)
    return out


def translate_text(
    text: str,
    source: str = "en",
    target: str = "it",
    engine: str = "google",
    chunk_size: int = 1500,
) -> str:
    """Translate text using the chosen engine's public API.

    ``engine`` is "google" or "microsoft".  Translates **each paragraph
    independently** (split on ``\n\n``) so paragraph breaks never pass
    through the API, and fetches those paragraphs **concurrently** so a short
    page doesn't wait on many sequential round-trips.  Markdown tables and
    image links are protected so the translator doesn't mangle their syntax;
    table cell contents are translated individually (also concurrently).
    """
    if not text or not text.strip():
        return text

    paragraphs = text.split("\n\n")
    results: list[str] = [""] * len(paragraphs)
    tasks = [(i, p) for i, p in enumerate(paragraphs) if p.strip()]
    if not tasks:
        return text

    stats = _TranslateStats()
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_MAX_WORKERS, len(tasks))
    ) as pool:
        futures = {
            pool.submit(
                _translate_paragraph, p, source, target, chunk_size, engine, stats
            ): i
            for i, p in tasks
        }
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception:
                results[i] = paragraphs[i]

    # Restore blank paragraphs (the ``\n\n`` separators) in their positions.
    for i, p in enumerate(paragraphs):
        if not p.strip():
            results[i] = p

    if stats.attempts > 0 and stats.ok == 0:
        raise TranslationError(
            f"engine '{engine}' failed on all {stats.attempts} request(s)"
        )
    return "\n\n".join(results)


def translate_text_google(
    text: str, source: str = "en", target: str = "it", chunk_size: int = 1500
) -> str:
    """Backward-compatible wrapper: translate text with Google Translate."""
    return translate_text(
        text, source=source, target=target, engine="google", chunk_size=chunk_size
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  app data / i18n bootstrap (Qt helpers)
# ═══════════════════════════════════════════════════════════════════════════════


def _app_data_base() -> Path:
    """User-writable app-data dir (version-independent, survives updates)."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    if not base:
        base = str(Path.home() / ".noesis-pdf-reader")
    return Path(base)


def _config_file_path() -> Path:
    """Path of the UI-language config file (persists across versions)."""
    return _app_data_base() / "config.json"


def _detect_os_lang() -> str:
    """Best-matching UI language for the OS locale, or 'it'."""
    try:
        name = QLocale.system().name()  # e.g. "fr_FR", "en-US", "C"
    except Exception:
        return "it"
    code = name.split("_")[0].split("-")[0].lower()
    return code if code in LANGUAGES else "it"


# ═══════════════════════════════════════════════════════════════════════════════
#  widgets
# ═══════════════════════════════════════════════════════════════════════════════


class PdfPageView(QGraphicsView):
    """Left panel — displays the rendered PDF page.

    In "select mode" the user can drag a rubber-band rectangle; the selection
    is emitted in scene coordinates (full-resolution pixels of the rendered
    page), which the caller converts back to PDF points.
    """

    # x0, y0, x1, y1 in scene (full-res pixmap) coordinates
    region_selected = pyqtSignal(float, float, float, float)
    region_excluded = pyqtSignal(float, float, float, float)
    region_included = pyqtSignal(float, float, float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self._full_pixmap: QPixmap | None = None
        self._pix_item: QGraphicsPixmapItem | None = None
        self._text_item: QGraphicsTextItem | None = None

        self._select_mode = False
        self._exclude_mode = False
        self._include_mode = False
        self._exclusion_items: list[QGraphicsRectItem] = []
        self._inclusion_items: list[QGraphicsRectItem] = []
        self._rubber_item: QGraphicsRectItem | None = None
        self._rubber_origin = None
        self._band_pen = QPen()
        self._band_brush = QBrush()
        self._exclude_pen = QPen()
        self._exclude_brush = QBrush()
        self._include_pen = QPen()
        self._include_brush = QBrush()
        self._start_hint = True  # retranslate() re-shows it only while idle
        self.apply_theme()

        self._show_message_text(T("view.start_hint"))

    def apply_theme(self) -> None:
        """Re-apply the theme colors (background, message text, zone pens)."""
        q = theme.color
        self.setBackgroundBrush(QColor(q("bg")))
        self._band_pen = QPen(QColor(q("zone_band")), 2, Qt.PenStyle.DashLine)
        self._exclude_pen = QPen(QColor(q("zone_exclude")), 2, Qt.PenStyle.DashLine)
        self._include_pen = QPen(QColor(q("zone_include")), 2, Qt.PenStyle.DashLine)
        self._band_brush = self._alpha_brush(q("zone_band"), 70)
        self._exclude_brush = self._alpha_brush(q("zone_exclude"), 70)
        self._include_brush = self._alpha_brush(q("zone_include"), 70)
        if self._full_pixmap is None and self._start_hint:
            self._show_message_text(T("view.start_hint"))

    @staticmethod
    def _alpha_brush(color: str, alpha: int) -> QBrush:
        c = QColor(color)
        c.setAlpha(alpha)
        return QBrush(c)

    # ── scene management ──────────────────────────────────────────────

    def _clear_scene(self):
        self._scene.clear()
        self._pix_item = None
        self._text_item = None
        self._rubber_item = None
        self._rubber_origin = None
        self._exclusion_items = []
        self._inclusion_items = []

    def _show_message_text(self, text: str):
        self._clear_scene()
        item = QGraphicsTextItem(text)
        item.setDefaultTextColor(QColor(theme.color("text5")))
        item.setFont(QFont("Segoe UI", 14))
        self._text_item = item
        self._scene.addItem(item)
        r = item.boundingRect()
        item.setPos(-r.width() / 2, -r.height() / 2)
        self._scene.setSceneRect(
            -r.width() / 2 - 20, -r.height() / 2 - 20,
            r.width() + 40, r.height() + 40,
        )

    def show_page(self, pixmap: QPixmap | None):
        """Store the full-resolution pixmap and scale it to fit the view."""
        if pixmap is None:
            self._full_pixmap = None
            self._start_hint = False
            self._show_message_text(T("view.page_unavailable"))
            return
        self._start_hint = False
        self._full_pixmap = pixmap
        self._clear_scene()
        self._pix_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self._fit_to_view()

    def show_message(self, text: str):
        """Show a plain status message instead of a page (e.g. while loading)."""
        self._full_pixmap = None
        self._start_hint = False
        self._show_message_text(text)

    def retranslate(self):
        """Re-apply UI strings after a language switch."""
        if self._full_pixmap is None and self._start_hint:
            self._show_message_text(T("view.start_hint"))

    # ── selection mode ───────────────────────────────────────────────────

    def set_select_mode(self, enabled: bool):
        """Enable/disable rubber-band region selection."""
        self._select_mode = enabled
        self._clear_rubber()
        if enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        else:
            self.unsetCursor()

    def set_exclude_mode(self, enabled: bool):
        """Enable/disable rubber-band zone exclusion."""
        self._exclude_mode = enabled
        self._clear_rubber()
        if enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        else:
            self.unsetCursor()

    def set_include_mode(self, enabled: bool):
        """Enable/disable rubber-band zone inclusion (numbered reading order)."""
        self._include_mode = enabled
        self._clear_rubber()
        if enabled:
            self.setCursor(Qt.CursorShape.CrossCursor)
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        else:
            self.unsetCursor()

    def _interactive(self) -> bool:
        return self._select_mode or self._exclude_mode or self._include_mode

    def _rubber_style(self) -> tuple[QPen, QBrush]:
        if self._exclude_mode:
            return self._exclude_pen, self._exclude_brush
        if self._include_mode:
            return self._include_pen, self._include_brush
        return self._band_pen, self._band_brush

    def _clear_exclusion_overlay(self):
        for item in self._exclusion_items:
            self._scene.removeItem(item)
        self._exclusion_items = []

    def show_excluded_zones(self, zones):
        """Draw the excluded zones (scene coords) as red overlays."""
        self._clear_exclusion_overlay()
        pen = QPen(QColor(theme.color("zone_exclude")), 2, Qt.PenStyle.SolidLine)
        brush = self._alpha_brush(theme.color("zone_exclude"), 60)
        for x0, y0, x1, y1 in zones:
            item = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
            item.setPen(pen)
            item.setBrush(brush)
            item.setZValue(10)
            self._scene.addItem(item)
            self._exclusion_items.append(item)

    def _clear_inclusion_overlay(self):
        for item in self._inclusion_items:
            self._scene.removeItem(item)
        self._inclusion_items = []

    def show_inclusion_zones(self, zones):
        """Draw the numbered inclusion zones (scene coords) as green overlays."""
        self._clear_inclusion_overlay()
        pen = QPen(QColor(theme.color("zone_include")), 2, Qt.PenStyle.SolidLine)
        brush = self._alpha_brush(theme.color("zone_include"), 60)
        for idx, (x0, y0, x1, y1) in enumerate(zones):
            item = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
            item.setPen(pen)
            item.setBrush(brush)
            item.setZValue(10)
            self._scene.addItem(item)
            self._inclusion_items.append(item)
            # Badge circolare verde scuro con il numero, ben visibile su
            # qualunque sfondo (il verde traslucido del box non basta).
            cx = x0 + (x1 - x0) / 2
            cy = y0 + (y1 - y0) / 2
            badge = QGraphicsEllipseItem(QRectF(cx - 34, cy - 34, 68, 68))
            badge.setBrush(QBrush(QColor(theme.color("zone_badge"))))
            badge.setPen(QPen(QColor(theme.color("text_inv")), 2))
            badge.setZValue(11)
            self._scene.addItem(badge)
            self._inclusion_items.append(badge)
            label = QGraphicsTextItem(str(idx + 1))
            label.setDefaultTextColor(QColor(theme.color("text_inv")))
            label.setFont(QFont("Segoe UI", 48, QFont.Weight.Bold))
            r = label.boundingRect()
            label.setPos(cx - r.width() / 2, cy - r.height() / 2)
            label.setZValue(12)
            self._scene.addItem(label)
            self._inclusion_items.append(label)

    def _clear_rubber(self):
        if self._rubber_item is not None:
            self._scene.removeItem(self._rubber_item)
            self._rubber_item = None
        self._rubber_origin = None

    # ── sizing ──────────────────────────────────────────────────────────

    def _fit_to_view(self):
        """Scale the full-resolution pixmap to fit the current view size.

        Fit-to-pane pure: the left panel never zooms or fits-to-width; the
        reading zoom lives in the detached ``PdfReaderWindow``.
        """
        if self._pix_item is not None:
            self.fitInView(self._pix_item, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_to_view()

    # ── rubber band mouse handling ─────────────────────────────────────

    def mousePressEvent(self, event):
        if (
            self._interactive()
            and self._pix_item is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            try:
                self._clear_rubber()
                pos = self.mapToScene(event.position().toPoint())
                self._rubber_origin = pos
                self._rubber_item = QGraphicsRectItem(QRectF(pos, pos))
                pen, brush = self._rubber_style()
                self._rubber_item.setPen(pen)
                self._rubber_item.setBrush(brush)
                self._scene.addItem(self._rubber_item)
            except Exception:
                # Never let an exception escape a virtual handler: in PyQt6
                # that aborts the whole process.
                self._clear_rubber()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._interactive()
            and self._rubber_item is not None
            and self._rubber_origin is not None
        ):
            try:
                cur = self.mapToScene(event.position().toPoint())
                self._rubber_item.setRect(
                    QRectF(self._rubber_origin, cur).normalized()
                )
            except Exception:
                self._clear_rubber()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if (
            self._interactive()
            and self._rubber_item is not None
            and self._rubber_origin is not None
            and event.button() == Qt.MouseButton.LeftButton
        ):
            try:
                cur = self.mapToScene(event.position().toPoint())
                rect = QRectF(self._rubber_origin, cur).normalized()
                self._clear_rubber()
                if rect.width() >= 4.0 and rect.height() >= 4.0:
                    if self._exclude_mode:
                        self.region_excluded.emit(
                            rect.left(), rect.top(), rect.right(), rect.bottom()
                        )
                    elif self._include_mode:
                        self.region_included.emit(
                            rect.left(), rect.top(), rect.right(), rect.bottom()
                        )
                    else:
                        self.region_selected.emit(
                            rect.left(), rect.top(), rect.right(), rect.bottom()
                        )
            except Exception:
                self._clear_rubber()
            event.accept()
            return
        super().mouseReleaseEvent(event)


_MIN_FONT_SIZE = 8
_MAX_FONT_SIZE = 24


def _clamp_font_size(px: int, lo: int = _MIN_FONT_SIZE, hi: int = _MAX_FONT_SIZE) -> int:
    """Clamp a font size in points to the runtime zoom range (8–24 pt)."""
    return max(lo, min(hi, int(px)))


def _text_key(text: str) -> str:
    """Deterministic short id of a text (stable across app restarts)."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _strip_header(body: str) -> str:
    """Return the body without the extraction header line.

    The header is a single line ``── … ──`` (it contains ``│`` separators
    and ends with a blank line).  It is excluded from the edit-cache key so
    that UI-language, engine and timing changes don't orphan saved edits.
    """
    first, sep, rest = body.partition("\n")
    if sep and first.startswith("──") and "│" in first:
        return rest.lstrip("\n")
    return body


def _uri_qimage(uri: str) -> QImage:
    """QImage da un URI ``file://`` oppure da un ``data:image/…;base64``."""
    if uri.startswith("data:"):
        head, sep, payload = uri.partition(",")
        if sep and ";base64" in head:
            try:
                return QImage.fromData(base64.b64decode(payload))
            except Exception:
                return QImage()
        return QImage()
    return QImage(QUrl(uri).toLocalFile())


def _uri_bytes(uri: str) -> bytes | None:
    """Byte immagine da un URI ``file://`` o ``data:`` (None se non leggibile)."""
    if uri.startswith("data:"):
        _, sep, payload = uri.partition(",")
        if sep:
            try:
                return base64.b64decode(payload)
            except Exception:
                return None
        return None
    try:
        return Path(QUrl(uri).toLocalFile()).read_bytes()
    except Exception:
        return None


class _ImageDocument(QTextDocument):
    """QTextDocument che sa caricare anche i data URI immagine (base64).

    Il motore rich-text di Qt carica ``file:``/``qrc:`` di default ma **non**
    ``data:``: qui si decodifica il payload base64 in una ``QImage``. È il punto
    (Strada 1) che rende visibili nel widget le figure embedded del markdown.
    """

    def loadResource(self, type_, url):  # noqa: N802 — API Qt
        try:
            if url.scheme() == "data":
                raw = bytes(url.toEncoded()).decode("ascii", "ignore")
                head, sep, payload = raw.partition(",")
                if sep and ";base64" in head:
                    img = QImage.fromData(base64.b64decode(payload))
                    if not img.isNull():
                        return img
        except Exception:
            pass
        return super().loadResource(type_, url)


class TextPanel(QTextEdit):
    """Editable text window with a live font zoom (A− / A+).

    Each instance owns its source buffer, font size and plain/rendered mode,
    so the Original and Translated tabs behave as independent editors.  The
    content is shown rendered from Markdown (HTML + CSS); once the user edits
    the text the window falls back to plain text — a re-render would
    reinterpret typed ``*``/``#``/``_`` and destroy the HTML formatting.
    """

    _CSS_TEMPLATE = """
    <style>
      body {{ font-family: 'Segoe UI', sans-serif; font-size: {size}px;
             color: {text}; line-height: 1.7; margin: 0; }}
      h1 {{ font-size: 1.5em; border-bottom: 2px solid {link}; padding-bottom: 4px; }}
      h2 {{ font-size: 1.3em; color: {h2}; margin-top: 1em; }}
      h3 {{ font-size: 1.15em; color: {h3}; }}
      strong {{ color: {strong}; }}
      em {{ color: {em}; }}
      code {{ background: {code_bg}; color: {code_text}; padding: 2px 6px;
             border-radius: 3px; font-family: 'Consolas', monospace;
             font-size: 0.9em; }}
      pre {{ background: {pre_bg}; padding: 12px; border-radius: 6px;
            border: 1px solid {border}; overflow-x: auto; }}
      table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
      th {{ background: {th_bg}; color: {th_text}; padding: 8px 12px;
           text-align: left; font-weight: 600; }}
      td {{ border: 1px solid {border}; padding: 6px 12px; }}
      tr:nth-child(even) {{ background: {row_alt}; }}
      blockquote {{ border-left: 4px solid {link}; margin: 10px 0;
                   padding: 6px 16px; background: {quote_bg};
                   color: {quote_text}; }}
      ul, ol {{ padding-left: 24px; }}
      li {{ margin: 3px 0; }}
      hr {{ border: none; border-top: 1px solid {border}; margin: 16px 0; }}
      a {{ color: {link}; }}
    </style>
    """

    font_size_changed = pyqtSignal(int)
    edited = pyqtSignal(str, str)  # (base_text, edited_text) from the user
    modified = pyqtSignal(bool)    # True: buffer differs from its base

    def __init__(self, parent=None):
        super().__init__(parent)
        # Documento che sa mostrare le figure embedded (data URI base64).
        self.setDocument(_ImageDocument(self))
        self._base_font_size = 12
        self._font_size = 12
        self._render_source: str = ""     # markdown/raw-html source for re-renders
        self._buffer: str = ""            # current plain content (rendered or edited)
        self._as_markdown: bool = True
        self._plain_mode: bool = False    # True once the user has edited
        self._raw_html: bool = False
        self._programmatic: bool = False  # guard against our own setHtml calls
        self._shown_buffer: str | None = None  # last programmatic content
        self._shown_md: bool = True
        self._edit_base: str | None = None  # base the current buffer derives from
        self._rendered_text: str = ""  # plain text of the last programmatic render
        self.setReadOnly(False)
        self.setFont(QFont("Segoe UI", self._font_size))
        self.apply_theme()
        self.textChanged.connect(self._on_text_changed)

    def css(self) -> str:
        """Current HTML stylesheet (font size + document tokens interpolated).

        Uses ``str.format``: the template braces are doubled and the colors
        come from the active theme, so the rendered document follows it.
        """
        return self._CSS_TEMPLATE.format(
            size=self._font_size,
            text=theme.color("doc_text"),
            link=theme.color("doc_link"),
            h2=theme.color("doc_h2"),
            h3=theme.color("doc_h3"),
            strong=theme.color("doc_strong"),
            em=theme.color("doc_em"),
            code_bg=theme.color("doc_code_bg"),
            code_text=theme.color("doc_code_text"),
            pre_bg=theme.color("doc_pre_bg"),
            border=theme.color("doc_border"),
            th_bg=theme.color("doc_th_bg"),
            th_text=theme.color("doc_th_text"),
            row_alt=theme.color("doc_row_alt"),
            quote_bg=theme.color("doc_quote_bg"),
            quote_text=theme.color("doc_quote_text"),
        )

    def apply_theme(self) -> None:
        """Re-apply the document surface colors and re-render the content."""
        self.setStyleSheet(
            "QTextEdit { background: %s; color: %s; padding: 12px; }"
            % (theme.color("doc_bg"), theme.color("doc_text"))
        )
        if not self._programmatic:
            try:
                self._render()
            except Exception:  # noqa: BLE001 — mai far crashare il tema
                pass

    # ── rendering ──────────────────────────────────────────────────────

    def _render(self) -> None:
        """Rebuild the document (from the render source or plain buffer).

        La dimensione del font NON passa dal ``body { font-size }`` del CSS:
        il motore rich-text di Qt lo ignora nei tag <style>, quindi il testo
        markdown non si sarebbe mai ridimensionato. Si applica invece
        ``document().setDefaultFont`` dopo il build del documento (i tag
        em/percentuali della CSS scalano rispetto al default font).
        """
        self._programmatic = True
        try:
            if self._raw_html:
                self.setHtml(self.css() + self._render_source)
            elif self._as_markdown and not self._plain_mode:
                html_body = _md_lib.markdown(
                    self._render_source, extensions=_MD_EXTENSIONS
                )
                self.setHtml(self.css() + html_body)
            else:
                self.setPlainText(self._buffer)
        finally:
            self._programmatic = False
        self.document().setDefaultFont(QFont("Segoe UI", self._font_size))
        # il contenuto corrente è sempre il testo renderizzato (i round-trip
        # HTML possono normalizzare gli spazi: confrontare col sorgente grezzo
        # darebbe falsi positivi di modifica)
        self._rendered_text = self.toPlainText()
        self._buffer = self._rendered_text
        self._fit_images_to_width()

    def _fit_images_to_width(self) -> None:
        """Ridimensiona le figure perché stiano nella larghezza visibile.

        Il motore rich-text di Qt **ignora** ``max-width`` nei tag ``<style>`` e
        rende le immagini alla dimensione nativa: le figure embedded (rese a 3×)
        sforavano il pannello. Si imposta larghezza (e altezza proporzionale) del
        ``QTextImageFormat`` al minimo tra nativa e spazio disponibile; allargando
        la finestra la figura torna alla dimensione nativa.
        """
        doc = self.document()
        avail = self.viewport().width() - 24
        if avail < 120:
            return
        self._programmatic = True
        changed = False
        try:
            cur = QTextCursor(doc)
            block = doc.begin()
            while block.isValid():
                it = block.begin()
                while not it.atEnd():
                    frag = it.fragment()
                    it += 1
                    if not frag.isValid():
                        continue
                    fmt = frag.charFormat()
                    if not fmt.isImageFormat():
                        continue
                    img = fmt.toImageFormat()
                    res = doc.resource(
                        QTextDocument.ResourceType.ImageResource, QUrl(img.name()))
                    if not isinstance(res, QImage) or res.isNull():
                        continue
                    nw, nh = res.width(), res.height()
                    if nw <= 0 or nh <= 0:
                        continue
                    w = min(nw, avail)
                    h = nh * w / nw
                    if abs(w - img.width()) < 1 and abs(h - img.height()) < 1:
                        continue
                    new = QTextImageFormat(img)
                    new.setWidth(w)
                    new.setHeight(h)
                    cur.setPosition(frag.position())
                    cur.setPosition(
                        frag.position() + frag.length(),
                        QTextCursor.MoveMode.KeepAnchor)
                    cur.setCharFormat(new)
                    changed = True
                block = block.next()
            # Cambiare i formati immagine durante l'iterazione lascia il layout
            # incoerente: gli ultimi blocchi restano con altezza 0 (non
            # impaginati, irraggiungibili). Un relayout completo li sistema.
            if changed:
                doc.markContentsDirty(0, doc.characterCount())
        finally:
            self._programmatic = False

    def resizeEvent(self, event):  # noqa: N802 — API Qt
        """Ri-adatta le figure alla nuova larghezza (solo in modalità markdown)."""
        super().resizeEvent(event)
        if self._programmatic or self._plain_mode:
            return
        if self._as_markdown or self._raw_html:
            self._fit_images_to_width()

    def is_modified(self) -> bool:
        """True if the user changed the content beyond the last render."""
        return self._rendered_text != self._buffer

    def _on_text_changed(self) -> None:
        """A user edit flips the window to plain text and updates the buffer."""
        if self._programmatic:
            return
        self._plain_mode = True
        self._raw_html = False
        self._buffer = self.toPlainText()
        changed = self.is_modified()
        self.modified.emit(changed)
        if changed and self._edit_base is not None:
            self.edited.emit(self._edit_base, self._buffer)

    def show_text(
        self,
        text: str,
        as_markdown: bool = True,
        edited: str | None = None,
    ) -> None:
        """Display text, optionally rendering as Markdown → HTML.

        Idempotent: if the same content is shown again (tab switch, repeat
        display), the window is left untouched so edits, cursor and scroll
        position survive.  ``edited`` (the user's stored version of ``text``)
        is shown instead, in plain mode, while ``text`` stays the edit base.
        """
        if text == self._shown_buffer and as_markdown == self._shown_md:
            return
        self._shown_buffer = text
        self._shown_md = as_markdown
        self._edit_base = text
        if edited is not None and edited != text:
            self._render_source = edited
            self._buffer = edited
            self._as_markdown = as_markdown
            self._plain_mode = True
            self._raw_html = False
        else:
            self._render_source = text
            self._buffer = text
            self._as_markdown = as_markdown
            self._plain_mode = False
            self._raw_html = False
        self._render()

    def show_html(self, html_body: str) -> None:
        """Display raw HTML with CSS styling."""
        self._render_source = html_body
        self._buffer = html_body
        self._as_markdown = False
        self._plain_mode = False
        self._raw_html = True
        self._shown_buffer = html_body
        self._shown_md = False
        self._edit_base = html_body
        self._render()

    # ── font zoom ──────────────────────────────────────────────────────

    def font_size(self) -> int:
        return self._font_size

    def set_font_size(self, px: int) -> None:
        """Set the base size (from Settings) and apply it."""
        self._base_font_size = px
        self._apply_font_size(px)

    def zoom_in(self) -> None:
        self._apply_font_size(self._font_size + 1)

    def zoom_out(self) -> None:
        self._apply_font_size(self._font_size - 1)

    def reset_zoom(self) -> None:
        self._apply_font_size(self._base_font_size)

    def _apply_font_size(self, px: int) -> None:
        """Apply a clamped size; plain text resizes live (cursor kept)."""
        px = _clamp_font_size(px)
        if px == self._font_size:
            return
        self._font_size = px
        if self._plain_mode and not self._raw_html:
            self.document().setDefaultFont(QFont("Segoe UI", px))
        else:
            self._render()  # markdown / raw HTML: re-render with new CSS size
        self.font_size_changed.emit(px)


class TextToolbar(QWidget):
    """Mini toolbar delle azioni **locali** di una finestra di testo.

    ``A− size A+ ↺`` (font) + ``📋`` copia + ``💾`` esporta + ``🔁`` ri-estrai
    (Originale) / ritraduci (lingua) + punto "modifiche non salvate". Il "passo
    successivo" (traduci) vive nel bottone flottante, non qui.
    """

    copy_requested = pyqtSignal()
    export_requested = pyqtSignal()
    reextract_requested = pyqtSignal()      # solo Originale
    retranslate_requested = pyqtSignal()    # solo lingua

    # Dimensioni uniformi per tutti i bottoni della mini toolbar: il glifo
    # emoji avrebbe altezza/larghezza diverse dai caratteri di testo.
    _BTN_FIXED = (36, 26)

    def __init__(self, panel: TextPanel, kind: str = "origin", parent=None):
        super().__init__(parent)
        self._panel = panel
        self._kind = kind

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)

        self.btn_decrease = QPushButton("A−")
        self.btn_decrease.setToolTip(T("editor.decrease"))
        self.btn_decrease.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_decrease.clicked.connect(panel.zoom_out)

        self.lbl_size = QLabel("")
        self.lbl_size.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_increase = QPushButton("A+")
        self.btn_increase.setToolTip(T("editor.increase"))
        self.btn_increase.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_increase.clicked.connect(panel.zoom_in)

        self.btn_reset = QPushButton("↺")
        self.btn_reset.setToolTip(T("editor.reset"))
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.clicked.connect(panel.reset_zoom)

        self.btn_export = QPushButton("💾")
        self.btn_export.setToolTip(T("editor.export"))
        self.btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_export.clicked.connect(self.export_requested.emit)

        # Azione locale di ri-elaborazione: ri-estrai (Originale) / ritraduci
        # (lingua). Dipende dal tipo di finestra.
        self.btn_action = QPushButton("🔁")
        self.btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
        if kind == "origin":
            self.btn_action.setToolTip(T("actions.origin.reextract"))
            self.btn_action.clicked.connect(self.reextract_requested.emit)
        else:
            self.btn_action.setToolTip(T("actions.translated.retranslate"))
            self.btn_action.clicked.connect(self.retranslate_requested.emit)

        # Copia il testo della finestra negli appunti.
        self.btn_copy = QPushButton("📋")
        self.btn_copy.setToolTip(T("toolbar.copy.tip"))
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.clicked.connect(self.copy_requested.emit)

        # Punto di stato: modifiche non ancora salvate su disco.
        self.lbl_dirty = QLabel("")
        self.lbl_dirty.setToolTip(T("editor.unsaved"))
        self.lbl_dirty.setAlignment(Qt.AlignmentFlag.AlignCenter)

        for b in (
            self.btn_decrease,
            self.btn_increase,
            self.btn_reset,
            self.btn_copy,
            self.btn_export,
            self.btn_action,
        ):
            b.setFixedSize(*self._BTN_FIXED)

        lay.addStretch()
        lay.addWidget(self.btn_decrease)
        lay.addWidget(self.lbl_size)
        lay.addWidget(self.btn_increase)
        lay.addWidget(self.btn_reset)
        lay.addSpacing(6)
        lay.addWidget(self.btn_copy)
        lay.addWidget(self.btn_export)
        lay.addWidget(self.btn_action)
        lay.addSpacing(4)
        lay.addWidget(self.lbl_dirty)
        lay.addStretch()

        self.apply_theme()
        panel.font_size_changed.connect(self._sync)
        self._sync(panel.font_size())

    def apply_theme(self) -> None:
        """Re-apply the toolbar colors from the active theme tokens."""
        q = theme.color
        btn_style = (
            "QPushButton { background: %s; color: %s;"
            " border: 1px solid %s; border-radius: 4px; padding: 0;"
            " font-size: 13px; }"
            "QPushButton:hover { background: %s; }"
            "QPushButton:pressed { background: %s; }"
            "QPushButton:disabled { color: %s; }"
            % (
                q("bg_input"), q("text"), q("border2"),
                q("bg_hover"), q("bg_pressed"), q("disabled_text"),
            )
        )
        for b in (
            self.btn_decrease, self.btn_increase, self.btn_reset,
            self.btn_copy, self.btn_export, self.btn_action,
        ):
            b.setStyleSheet(btn_style)
        self.lbl_size.setStyleSheet(
            "color: %s; font-size: 12px; min-width: 44px;" % q("text4")
        )
        self.lbl_dirty.setStyleSheet(
            "color: %s; font-size: 14px; min-width: 14px;" % q("warn")
        )

    def set_dirty(self, dirty: bool) -> None:
        """Show/hide the "unsaved edits" dot."""
        self.lbl_dirty.setText("●" if dirty else "")

    def _sync(self, px: int) -> None:
        self.lbl_size.setText(f"{px} pt")
        self.btn_decrease.setEnabled(px > _MIN_FONT_SIZE)
        self.btn_increase.setEnabled(px < _MAX_FONT_SIZE)

    def retranslate(self) -> None:
        self.btn_decrease.setToolTip(T("editor.decrease"))
        self.btn_increase.setToolTip(T("editor.increase"))
        self.btn_reset.setToolTip(T("editor.reset"))
        self.btn_copy.setToolTip(T("toolbar.copy.tip"))
        self.btn_export.setToolTip(T("editor.export"))
        self.btn_action.setToolTip(
            T("actions.origin.reextract") if self._kind == "origin"
            else T("actions.translated.retranslate")
        )
        self.lbl_dirty.setToolTip(T("editor.unsaved"))



class TranslateThread(QThread):
    """Background thread for translation to keep UI responsive."""

    result_ready = pyqtSignal(int, str, str)  # generation_id, kind, translated_text
    error_ready = pyqtSignal(int, str)        # generation_id, error message

    def __init__(
        self,
        text: str,
        generation: int,
        kind: str = "origin",
        source: str = "en",
        target: str = "it",
        engine: str = "google",
    ):
        super().__init__()
        self._text = text
        self._generation = generation
        self._kind = kind
        self._source = source
        self._target = target
        self._engine = engine

    def run(self):
        try:
            translated = translate_text(
                self._text,
                source=self._source,
                target=self._target,
                engine=self._engine,
            )
        except Exception as exc:
            self.error_ready.emit(self._generation, str(exc))
            return
        self.result_ready.emit(self._generation, self._kind, translated)


class ExtractThread(QThread):
    """Background thread for text extraction + layout engine.

    Both Tesseract OCR (scanned PDFs) and the adaptive layout engine's
    ``profile_page`` are CPU/IO bound and slow (seconds per page), so the
    whole pipeline runs here and the GUI thread only displays the result.
    """

    result_ready = pyqtSignal(int, int, str, str, str, float, str)
    # generation, page_num, text, label, raw, elapsed, engine

    def __init__(
        self,
        path: str,
        page_num: int,
        generation: int,
        ocr_language: str = "eng",
        exclude: tuple = (),
        include: tuple = (),
        raw: str | None = None,
        figures_dir=None,
        zones_key: str = "()",
    ):
        super().__init__()
        self._path = path
        self._page_num = page_num
        self._generation = generation
        self._ocr_language = ocr_language
        self._exclude = exclude
        self._include = include
        self._raw = raw
        self._figures_dir = figures_dir
        # Chiave delle zone con cui questa estrazione è stata richiesta: il
        # risultato va salvato sotto QUESTA chiave, non sotto quella corrente
        # (l'utente può aver resettato/cambiato le zone nel frattempo).
        self._zones_key = zones_key
        # Pipeline che ha prodotto il testo finale ("ir"|"current") e flag di
        # fallback: valorizzati in ``run`` e osservabili dall'esterno.
        self.engine = "current"
        self.fallback = False

    def run(self):
        t0 = time.perf_counter()
        raw = self._raw  # dalla cache, se c'è (None altrimenti)
        text, label = "", ""
        # Pipeline che ha **davvero** prodotto il testo ("ir" o "current").
        # Non è mai silenziosa: va registrata nel record/cache, così l'harness
        # può osservarla senza ri-derivarla (attribuzione corretta) e un
        # eventuale fallback resta visibile (R12/determinismo).
        engine = "current"
        self.fallback = False
        # Pipeline IR (content map), solo senza zone manuali (che richiedono il
        # motore "current"). Guardia anti-body=0: se IR non produce testo, ricade
        # sulla pipeline attuale (es. pagine quasi vuote). Una **sola** passata:
        # IR restituisce anche il "raw" da mettere in cache.
        if _pipeline_mode() == "ir" and not self._include and not self._exclude:
            try:
                sel_text, sel_engine, ir_raw = _select_page_output(
                    self._path, self._page_num, self._figures_dir)
                if raw is None:
                    raw = ir_raw  # riusa la stessa passata anche in fallback
                if sel_text and _norm_text(sel_text).strip():
                    text, label = sel_text, "auto"
                    engine = sel_engine  # "ir" oppure "current" (chooser)
            except Exception:
                # Fallback da IR a `current` per eccezione: **segnalato** (non
                # silenzioso), così l'attribuzione e il determinismo restano
                # verificabili dall'harness.
                text, self.fallback = "", True
        if not text:
            if raw is None:
                raw = _extract_pymupdf4llm(
                    self._path, self._page_num, ocr_language=self._ocr_language
                )
            text, label = _apply_engine_standalone(
                self._path, self._page_num, raw,
                exclude=self._exclude, include=self._include,
                figures_dir=self._figures_dir,
            )
            engine = "current"
        self.engine = engine
        elapsed = time.perf_counter() - t0
        self.result_ready.emit(
            self._generation, self._page_num, text, label, raw, elapsed, engine
        )


class LiquidOverlay(QWidget):
    """Overlay con riempimento "liquido" (stile servizio).

    Adattato dal cloner all'area **testo** del reader-lite: mostra una didascalia
    centrata e un gradiente che sale dal basso, con progresso **stimato** (nessun
    progresso reale dal motore): sale asintoticamente verso ~90% in una durata
    tipica e chiude a 100% al completamento. Usato per l'overlay di
    estrazione/traduzione e per l'anteprima "liquida" della finestra batch.
    """

    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._caption = ""
        self._caption_box: QRect | None = None
        self._progress = 0.0
        self._target = 90.0
        self._duration = _OVERLAY_EST_MS
        self._elapsed = 0
        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick)
        self._cancelable = False
        self._btn_cancel = QPushButton(T("settings.cancel"), self)
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._on_cancel_clicked)
        self._btn_cancel.hide()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.apply_theme()
        self.hide()

    def apply_theme(self) -> None:
        """Applica i colori del tema attivo all'overlay e al pulsante Annulla."""
        q = theme.color
        self._btn_cancel.setStyleSheet(
            "QPushButton { background: %s; color: %s;"
            " border: 1px solid %s; border-radius: 8px;"
            " padding: 7px 18px; font-size: 13px; }"
            "QPushButton:hover { background: %s; }"
            "QPushButton:disabled { color: %s; border-color: %s; }"
            % (
                q("wiz_panel"), q("wiz_title"), q("wiz_accent"),
                q("wiz_sel_bg"), q("wiz_muted"), q("wiz_line"),
            )
        )
        self.update()

    # ── ciclo di vita ────────────────────────────────────────────────
    def start(
        self,
        caption: str = "",
        pixmap: QPixmap | None = None,
        duration_ms: int = _OVERLAY_EST_MS,
        cancelable: bool = False,
    ) -> None:
        self._pixmap = pixmap
        self._caption = caption or ""
        self._progress = 0.0
        self._target = 90.0
        self._elapsed = 0
        self._duration = max(1000, int(duration_ms))
        self._cancelable = bool(cancelable)
        self._btn_cancel.setText(T("settings.cancel"))
        self._btn_cancel.setVisible(self._cancelable)
        self._btn_cancel.setEnabled(True)
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self._position_cancel()
        self.update()
        self._timer.start()

    def set_caption(self, caption: str) -> None:
        self._caption = caption or ""
        self.update()

    def _on_cancel_clicked(self) -> None:
        self._btn_cancel.setEnabled(False)
        self._caption = T("export.progress.cancelling")
        self.update()
        self.cancel_requested.emit()

    def set_target(self, target: float) -> None:
        self._target = max(0.0, min(100.0, float(target)))

    def finish(self, ok: bool = True) -> None:
        self._timer.stop()
        self._progress = 100.0 if ok else 0.0
        self._btn_cancel.hide()
        self.update()
        QTimer.singleShot(200, self._hide_when_idle)

    def stop(self) -> None:
        self._timer.stop()
        self._pixmap = None
        self._btn_cancel.hide()
        self.hide()

    def progress(self) -> float:
        return self._progress

    def _position_cancel(self) -> None:
        if not self._btn_cancel.isVisible():
            return
        self._btn_cancel.adjustSize()
        x = (self.width() - self._btn_cancel.width()) // 2
        box = self._caption_box
        if box is not None:
            y = box.bottom() + 10
        else:
            y = (self.height() + 56) // 2
        y = min(y, self.height() - self._btn_cancel.height() - 8)
        self._btn_cancel.move(max(0, x), max(0, y))

    def resizeEvent(self, event):  # noqa: N802 — override Qt
        super().resizeEvent(event)
        self._position_cancel()

    # ── animazione ───────────────────────────────────────────────────
    def _tick(self) -> None:
        self._elapsed += self._timer.interval()
        tau = self._duration / 2.5
        self._progress = self._target * (1.0 - math.exp(-self._elapsed / tau))
        self.update()

    def _hide_when_idle(self) -> None:
        if not self._timer.isActive():
            self.hide()

    # ── rendering ────────────────────────────────────────────────────
    def paintEvent(self, event):  # noqa: N802 — override Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        painter.fillRect(rect, QColor.fromString(theme.color("liquid_bg")))

        if self._pixmap is not None and not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (rect.width() - scaled.width()) // 2
            y = (rect.height() - scaled.height()) // 2
            painter.setOpacity(0.35)
            painter.drawPixmap(x, y, scaled)
            painter.setOpacity(1.0)

        height = int(rect.height() * self._progress / 100.0)
        if height > 0:
            top = rect.height() - height
            base = QColor.fromString(theme.color("liquid_bar"))
            top_col = QColor(base)
            top_col.setAlpha(77)
            bot_col = QColor(base)
            bot_col.setAlpha(148)
            gradient = QLinearGradient(0, top, 0, rect.height())
            gradient.setColorAt(0.0, top_col)
            gradient.setColorAt(1.0, bot_col)
            painter.fillRect(QRect(0, top, rect.width(), height), gradient)
            painter.setPen(QColor.fromString(theme.color("liquid_bar")))
            painter.drawLine(0, top, rect.width(), top)

        if self._caption:
            font = painter.font()
            font.setPointSize(13)
            font.setBold(True)
            painter.setFont(font)
            flags = int(Qt.AlignmentFlag.AlignCenter) | int(
                Qt.TextFlag.TextWordWrap
            )
            metrics = painter.fontMetrics()
            box = metrics.boundingRect(
                QRect(8, 0, max(40, rect.width() - 16), rect.height()),
                flags,
                self._caption,
            )
            box.adjust(-12, -8, 12, 8)
            box.moveCenter(rect.center())
            self._caption_box = QRect(box)
            painter.fillRect(
                box, QColor.fromString(theme.color("liquid_caption_bg"))
            )
            painter.setPen(QColor.fromString(theme.color("liquid_text")))
            painter.drawText(box, flags, self._caption)
            self._position_cancel()
        else:
            self._caption_box = None


class _SheenButton(QPushButton):
    """QPushButton con un riflesso luminoso ("scintilla") che lo attraversa.

    Qt non supporta le animazioni dei fogli di stile, quindi il riflesso è
    disegnato in ``paintEvent`` e la sua posizione è animata da una
    ``QPropertyAnimation`` sulla property ``sheenPos`` (0→1). Il colore arriva
    dal token di tema ``fab_spark``: bianco sul tema scuro, blu su quello chiaro.
    """

    # Durata del ciclo del riflesso (ms), coerente col mockup (2.4 s).
    SHEEN_MS = 2400
    # Raggio degli angoli: allineato al ``border-radius`` del QSS del FAB.
    SHEEN_RADIUS = 15.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sheen_pos = 0.0
        self._sheen_enabled = False
        self._sheen_color = QColor(theme.color("fab_spark"))
        self._sheen_anim = QPropertyAnimation(self, b"sheenPos", self)
        self._sheen_anim.setDuration(self.SHEEN_MS)
        self._sheen_anim.setLoopCount(-1)
        self._sheen_anim.setStartValue(0.0)
        self._sheen_anim.setKeyValueAt(0.55, 1.0)
        self._sheen_anim.setEndValue(1.0)
        self._sheen_anim.setEasingCurve(QEasingCurve.Type.Linear)

    # ── property animata ────────────────────────────────────────────────
    def _get_sheen_pos(self) -> float:
        return self._sheen_pos

    def _set_sheen_pos(self, value: float) -> None:
        self._sheen_pos = float(value)
        self.update()

    sheenPos = pyqtProperty(float, _get_sheen_pos, _set_sheen_pos)

    # ── API ─────────────────────────────────────────────────────────────
    def set_sheen_color(self, color) -> None:
        """Aggiorna il colore del riflesso (dal tema attivo)."""
        self._sheen_color = QColor(color)
        self.update()

    def start_sheen(self) -> None:
        """Avvia il riflesso in loop (pulsante visibile)."""
        self._sheen_enabled = True
        self._sheen_anim.start()

    def stop_sheen(self) -> None:
        """Ferma il riflesso e lo riporta fuori campo."""
        self._sheen_enabled = False
        self._sheen_anim.stop()
        self._sheen_pos = 0.0
        self.update()

    def is_sheen_running(self) -> bool:
        return self._sheen_enabled

    # ── disegno ─────────────────────────────────────────────────────────
    def paintEvent(self, event):  # noqa: N802 (API Qt)
        super().paintEvent(event)
        if not self._sheen_enabled:
            return
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0),
            self.SHEEN_RADIUS, self.SHEEN_RADIUS,
        )
        painter.setClipPath(path)
        band = max(24.0, w * 0.5)
        x = -band + (w + 2.0 * band) * self._sheen_pos
        grad = QLinearGradient(x, 0.0, x + band, float(h))
        edge = QColor(self._sheen_color)
        edge.setAlpha(0)
        grad.setColorAt(0.0, edge)
        grad.setColorAt(0.5, QColor(self._sheen_color))
        grad.setColorAt(1.0, edge)
        painter.fillRect(self.rect(), grad)
        painter.end()


@dataclass
class Capture:
    """An object in the gallery: an image, a table or a piece of text.

    ``kind`` is derived from the interpretation ``method`` (see
    :func:`_capture_kind`); ``text`` holds the interpreted text/markdown for
    ``table``/``text`` objects; ``source`` distinguishes automatic figures
    (``figure``) from manual captures (``capture``).
    """

    uri: str
    kind: str = "image"          # image | table | text
    text: str = ""               # interpreted text/markdown
    method: str = "none"         # none | native | ocr | table
    source: str = "capture"      # capture | figure
    page: int = -1


def _capture_kind(method: str) -> str:
    """Map an interpretation method to a gallery object kind."""
    if method == "table":
        return "table"
    if method in ("native", "ocr"):
        return "text"
    return "image"


class ObjectsToolbar(QWidget):
    """Mini toolbar of the 🗂️ Objects tab.

    Type filter (Tutti / Immagini / Tabelle / Testi), object count, and bulk
    actions (export all / remove all).
    """

    filter_changed = pyqtSignal(str)      # all | image | table | text
    export_all_requested = pyqtSignal()
    remove_all_requested = pyqtSignal()

    _FILTERS = ("all", "image", "table", "text")

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 3, 6, 3)
        lay.setSpacing(4)

        self._group = QButtonGroup(self)
        self._buttons: dict[str, QPushButton] = {}
        for key in self._FILTERS:
            b = QPushButton(T(f"objects.filter.{key}"))
            b.setCheckable(True)
            b.setFlat(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.filter_changed.emit(k))
            self._group.addButton(b)
            self._buttons[key] = b
            lay.addWidget(b)
        self._buttons["all"].setChecked(True)

        self.lbl_count = QLabel("")
        lay.addSpacing(6)
        lay.addWidget(self.lbl_count)
        lay.addStretch(1)

        self.btn_export_all = QPushButton(T("objects.export_all"))
        self.btn_export_all.clicked.connect(self.export_all_requested.emit)
        self.btn_remove_all = QPushButton(T("objects.remove_all"))
        self.btn_remove_all.clicked.connect(self.remove_all_requested.emit)
        for b in (self.btn_export_all, self.btn_remove_all):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            lay.addWidget(b)

        self.apply_theme()

    def current_filter(self) -> str:
        for key, b in self._buttons.items():
            if b.isChecked():
                return key
        return "all"

    def set_count(self, n: int) -> None:
        self.lbl_count.setText(T("objects.count", n=n))

    def set_bulk_enabled(self, enabled: bool) -> None:
        self.btn_export_all.setEnabled(enabled)
        self.btn_remove_all.setEnabled(enabled)

    def apply_theme(self) -> None:
        q = theme.color
        chip = (
            "QPushButton { background: %s; color: %s; border: 1px solid %s;"
            " border-radius: 10px; padding: 3px 12px; font-size: 12px; }"
            "QPushButton:hover { background: %s; color: %s; }"
            "QPushButton:checked { background: %s; color: %s;"
            " border-color: %s; font-weight: bold; }"
            % (
                q("bg_input"), q("text4"), q("border2"),
                q("bg_hover"), q("text2"),
                q("accent"), q("accent_text"), q("accent"),
            )
        )
        for b in self._buttons.values():
            b.setStyleSheet(chip)
        self.lbl_count.setStyleSheet(
            "color: %s; font-size: 12px;" % q("text4"))
        bulk = (
            "QPushButton { background: %s; color: %s; border: none;"
            " border-radius: 4px; padding: 4px 10px; font-size: 12px; }"
            "QPushButton:hover { background: %s; }"
            "QPushButton:disabled { color: %s; }"
            % (q("bg_input"), q("text2"), q("bg_hover"), q("disabled_text"))
        )
        self.btn_export_all.setStyleSheet(bulk)
        self.btn_remove_all.setStyleSheet(bulk)
        self.setStyleSheet(
            "ObjectsToolbar { background: %s; border-bottom: 1px solid %s; }"
            % (q("bg_bar"), q("border2"))
        )

    def retranslate(self) -> None:
        for key in self._FILTERS:
            self._buttons[key].setText(T(f"objects.filter.{key}"))
        self.btn_export_all.setText(T("objects.export_all"))
        self.btn_remove_all.setText(T("objects.remove_all"))
        self.set_count(0)  # refreshed on the next rebuild


class TranslatablePanel(QWidget):
    """Wraps TextPanel with tabs: original, Italian translation, and images."""

    # Emitted when the user removes a captured image from the gallery.
    image_removed = pyqtSignal(str)  # file:// URI
    # Transient message for the main window status bar (message, duration ms).
    toast = pyqtSignal(str, int)
    # Azioni post-elaborazione: mini toolbar (locali) + CTA del FAB.
    copy_requested = pyqtSignal(str)  # "origin" | "translated"
    translate_page_requested = pyqtSignal()
    reextract_page_requested = pyqtSignal()
    retranslate_page_requested = pyqtSignal()
    translate_next_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Tab bar ────────────────────────────────────────────────────
        self._tab_bar = QWidget()
        self._tab_bar.setFixedHeight(36)
        self._tab_bar.setObjectName("tabBar")

        tab_layout = QHBoxLayout(self._tab_bar)
        tab_layout.setContentsMargins(4, 2, 4, 2)
        tab_layout.setSpacing(2)

        self._btn_original = QPushButton(T("tab.original"))
        # La tab di traduzione mostra bandiera + nome della lingua di
        # destinazione scelta (endonimo): es. "🇫🇷 Français", "🇩🇪 Deutsch".
        self._target_lang: str = get_target_lang()
        self._source_lang: str = get_source_lang()
        self._engine: str = get_translation_engine()
        self._btn_translated = QPushButton(flag_endonym(self._target_lang))
        self._btn_images = QPushButton(T("tab.objects"))

        tab_style = self._tab_style()
        for btn in (self._btn_original, self._btn_translated, self._btn_images):
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setStyleSheet(tab_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            tab_layout.addWidget(btn)

        # Spinner label for translation in progress
        self._lbl_spinner = QLabel("")
        tab_layout.addWidget(self._lbl_spinner)

        tab_layout.addStretch()

        # ── Engine radios (live engine switch), far right of the tab bar ──
        self._radio_google = QRadioButton(T("engine.short.google"))
        self._radio_microsoft = QRadioButton(T("engine.short.microsoft"))
        radio_style = self._radio_style()
        for rb in (self._radio_google, self._radio_microsoft):
            rb.setStyleSheet(radio_style)
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            tab_layout.addWidget(rb)
        self._engine_group = QButtonGroup(self)
        self._engine_group.addButton(self._radio_google)
        self._engine_group.addButton(self._radio_microsoft)
        self._radio_google.setChecked(self._engine == "google")
        self._radio_microsoft.setChecked(self._engine == "microsoft")
        self._radio_google.toggled.connect(self._on_engine_radio)
        self._radio_microsoft.toggled.connect(self._on_engine_radio)

        # ── Text windows (Original / Translated) ───────────────────────
        # Two independent editable windows, each with its own mini toolbar
        # (A− / A+): stacked, the active tab's window is shown.
        def _make_window(kind: str) -> tuple[QWidget, TextPanel, TextToolbar]:
            win = QWidget()
            v = QVBoxLayout(win)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            panel = TextPanel()
            panel.set_font_size(get_setting("font_size", 12))
            toolbar = TextToolbar(panel, kind=kind)
            v.addWidget(toolbar)
            v.addWidget(panel)
            return win, panel, toolbar

        (
            self._origin_window,
            self.origin_panel,
            self.origin_toolbar,
        ) = _make_window("origin")
        (
            self._translated_window,
            self.translated_panel,
            self.translated_toolbar,
        ) = _make_window("translated")

        # ── Overlay "liquido" (estrazione/traduzione) + indicatore di tab ──
        # L'overlay copre l'area testo mentre il lavoro è in corso; l'indicatore
        # pulsa sotto la tab attiva. Sostituisce il vecchio toast testuale.
        self._overlay_origin = LiquidOverlay(self._origin_window)
        self._overlay_translated = LiquidOverlay(self._translated_window)
        self._origin_window.installEventFilter(self)
        self._translated_window.installEventFilter(self)

        # Indicatore animato sotto la tab attiva.
        self._tab_indicator = QWidget(self._tab_bar)
        self._tab_indicator.setObjectName("tabIndicator")
        self._tab_indicator.setFixedHeight(3)
        self._tab_indicator.hide()
        self._tab_effect = QGraphicsOpacityEffect(self._tab_indicator)
        self._tab_effect.setOpacity(1.0)
        self._tab_indicator.setGraphicsEffect(self._tab_effect)
        self._tab_anim = QPropertyAnimation(self._tab_indicator, b"geometry", self)
        self._tab_anim.setDuration(220)
        self._tab_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._tab_pulse = QPropertyAnimation(self._tab_effect, b"opacity", self)
        self._tab_pulse.setDuration(700)
        self._tab_pulse.setLoopCount(-1)
        self._tab_pulse.setStartValue(1.0)
        self._tab_pulse.setKeyValueAt(0.5, 0.25)
        self._tab_pulse.setEndValue(1.0)
        self._tab_pulse.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._tab_bar.installEventFilter(self)

        # ── FAB azioni post-elaborazione (origine + gemello traduzione) ──
        self._fab_origin = self._make_fab("origin", self._origin_window)
        self._fab_translated = self._make_fab("translated", self._translated_window)
        self._fab_origin.hide()
        self._fab_translated.hide()

        # ── Oggetti: mini toolbar (filtro tipo + azioni bulk) + galleria ──
        self._objects_filter: str = "all"
        self.objects_toolbar = ObjectsToolbar()
        self.objects_toolbar.filter_changed.connect(self._on_objects_filter)
        self.objects_toolbar.export_all_requested.connect(self._export_all_objects)
        self.objects_toolbar.remove_all_requested.connect(self._remove_all_objects)

        self.images_panel = QScrollArea()
        self.images_panel.setWidgetResizable(True)

        self._objects_window = QWidget()
        _ov = QVBoxLayout(self._objects_window)
        _ov.setContentsMargins(0, 0, 0, 0)
        _ov.setSpacing(0)
        _ov.addWidget(self.objects_toolbar)
        _ov.addWidget(self.images_panel)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._origin_window)      # index 0
        self._stack.addWidget(self._translated_window)  # index 1
        self._stack.addWidget(self._objects_window)     # index 2

        layout.addWidget(self._tab_bar)
        layout.addWidget(self._stack)

        # ── State ──────────────────────────────────────────────────────
        self._page_text: str = ""          # the single text shown (auto or manual)
        self._page_body: str = ""          # page text without the extraction header
        self._header_prefix: str = ""      # header template before the engine token
        self._header_tail: str = ""        # header template after the engine token
        self._translated_text: str = ""
        self._render_md: bool = True
        # Cache per (pagina, destinazione): cambiare la lingua di destinazione
        # fa cache-miss naturale, senza invalidare nulla.
        # Cache per (pagina, engine, destinazione): cambiare lingua o motore
        # fa cache-miss naturale, senza invalidare nulla.
        self._page_translation_cache: dict[tuple[int, str, str], str] = {}
        self._current_page: int = -1
        self._images: list[str] = []  # file:// URIs of manually captured regions
        # Oggetti catturati (cattura / cattura+interpreta) per URI: tipo,
        # testo interpretato, metodo, provenienza. Solo memoria (non persistito).
        self._captures: dict[str, Capture] = {}
        self._thread: TranslateThread | None = None
        # Thread sostituiti mentre erano ancora attivi: tenuti vivi finché non
        # terminano (i loro risultati sono scartati dal guard di generazione).
        self._retired_threads: list[TranslateThread] = []
        self._generation: int = 0
        self._cache_file: Path | None = None  # on-disk translation cache file
        self._doc_fingerprint: str = ""  # invalidates the cache if the PDF changes
        # Modifiche utente per finestra (origin/translated), chiavate per
        # contenuto base (senza header): sopravvivono alla navigazione e alla
        # riapertura del documento.
        self._edit_cache: dict[str, dict[str, str]] = {
            "origin": {}, "translated": {},
        }
        self._edits_cache_file: Path | None = None  # on-disk edits cache file
        self._doc_stem: str = ""  # PDF name (without extension), for exports
        self._save_edits: bool = bool(get_setting("save_edits", True))
        # Indicatore "modifiche non salvate" per finestra (toolbar dot).
        self._dirty: dict[str, bool] = {"origin": False, "translated": False}
        self._edits_save_timer = QTimer(self)
        self._edits_save_timer.setSingleShot(True)
        self._edits_save_timer.setInterval(1500)
        self._edits_save_timer.timeout.connect(self._save_edits_cache)
        # Deferral ibrido: traduzione ri-lanciata solo dopo una pausa di 600 ms.
        self._pending_translation: bool = False
        self._translate_timer = QTimer(self)
        self._translate_timer.setSingleShot(True)
        self._translate_timer.setInterval(600)
        self._translate_timer.timeout.connect(self._flush_translation)

        # ── Connections ────────────────────────────────────────────────
        self._btn_original.clicked.connect(self._on_show_original)
        self._btn_translated.clicked.connect(self._on_show_translated)
        self._btn_images.clicked.connect(self._on_show_images)
        self.origin_panel.edited.connect(self._on_text_edited)
        self.translated_panel.edited.connect(self._on_text_edited)
        self.origin_panel.modified.connect(
            lambda m: self._on_window_modified("origin", m)
        )
        self.translated_panel.modified.connect(
            lambda m: self._on_window_modified("translated", m)
        )
        self.origin_toolbar.export_requested.connect(
            lambda: self._export_window(self.origin_panel)
        )
        self.translated_toolbar.export_requested.connect(
            lambda: self._export_window(self.translated_panel)
        )
        self.origin_toolbar.copy_requested.connect(
            lambda: self.copy_requested.emit("origin"))
        self.translated_toolbar.copy_requested.connect(
            lambda: self.copy_requested.emit("translated"))
        self.origin_toolbar.reextract_requested.connect(
            self.reextract_page_requested.emit)
        self.translated_toolbar.retranslate_requested.connect(
            self.retranslate_page_requested.emit)

        # Applica i colori del tema prima di mostrare qualsiasi cosa.
        self.apply_theme()
        # Riapri sull'ultima tab usata (se abilitato) — dopo che tutto lo
        # stato e i pannelli esistono.
        self._restore_initial_tab()
        self._rebuild_images_panel()

    # ── tema ────────────────────────────────────────────────────────────

    def _tab_style(self) -> str:
        """QSS dei bottoni-tab, dai token del tema attivo."""
        q = theme.color
        return (
            "QPushButton { background: %s; color: %s;"
            " border: 1px solid %s; border-bottom: none;"
            " border-radius: 6px 6px 0 0; padding: 4px 16px; font-size: 13px; }"
            "QPushButton:hover { background: %s; color: %s; }"
            "QPushButton:checked { background: %s; color: %s;"
            " border-color: %s; font-weight: bold; }"
            % (
                q("bg_input"), q("text4"), q("border2"),
                q("bg_hover"), q("text2"),
                q("doc_bg"), q("doc_text"), q("doc_border"),
            )
        )

    def _radio_style(self) -> str:
        """QSS dei radio del motore, dai token del tema attivo."""
        q = theme.color
        return (
            "QRadioButton { color: %s; font-size: 12px;"
            " background: transparent; spacing: 5px; }"
            "QRadioButton:hover { color: %s; }"
            "QRadioButton::indicator { width: 13px; height: 13px;"
            " border: 1px solid %s; border-radius: 7px; background: %s; }"
            "QRadioButton::indicator:checked { background: %s;"
            " border-color: %s; }"
            % (
                q("text3"), q("text"),
                q("radio_border"), q("radio_bg"),
                q("accent"), q("accent"),
            )
        )

    def apply_theme(self) -> None:
        """Re-apply the active theme to the panel chrome and gallery."""
        q = theme.color
        self._tab_bar.setStyleSheet(
            "QWidget#tabBar { background: %s; border-bottom: 1px solid %s; }"
            % (q("bg_bar"), q("border2"))
        )
        tab_style = self._tab_style()
        for btn in (self._btn_original, self._btn_translated, self._btn_images):
            btn.setStyleSheet(tab_style)
        self._lbl_spinner.setStyleSheet(
            "color: %s; font-size: 13px; padding: 4px 8px;" % q("text4")
        )
        radio_style = self._radio_style()
        for rb in (self._radio_google, self._radio_microsoft):
            rb.setStyleSheet(radio_style)
        self.images_panel.setStyleSheet(
            "QScrollArea { background: %s; border: none; }" % q("doc_pre_bg")
        )
        if hasattr(self, "objects_toolbar"):
            self.objects_toolbar.apply_theme()
        if hasattr(self, "_tab_indicator"):
            self._tab_indicator.setStyleSheet("background: %s;" % q("accent"))
        if hasattr(self, "_overlay_origin"):
            self._overlay_origin.apply_theme()
            self._overlay_translated.apply_theme()
        if hasattr(self, "_fab_origin"):
            self._theme_fab(self._fab_origin)
            self._theme_fab(self._fab_translated)
        self.origin_panel.apply_theme()
        self.translated_panel.apply_theme()
        self.origin_toolbar.apply_theme()
        self.translated_toolbar.apply_theme()
        self._rebuild_images_panel()

    def _restore_initial_tab(self):
        """Open the panel on the remembered tab (if enabled), else Originale."""
        if get_setting("remember_tab", True):
            last = get_setting("last_tab", "original")
            if last == "translated":
                self._set_active_tab(self._btn_translated)
            elif last == "images":
                self._set_active_tab(self._btn_images)
            else:
                self._set_active_tab(self._btn_original)
        else:
            self._set_active_tab(self._btn_original)

    def retranslate(self):
        """Re-apply tab labels and toolbar tooltips after a language switch."""
        self._btn_original.setText(T("tab.original"))
        self._btn_translated.setText(flag_endonym(self._target_lang))
        self._btn_images.setText(T("tab.objects"))
        self.objects_toolbar.retranslate()
        self._radio_google.setText(T("engine.short.google"))
        self._radio_microsoft.setText(T("engine.short.microsoft"))
        self._fab_origin.setText(T("actions.origin.translate"))
        self._fab_origin.setToolTip(T("actions.origin.cta.tip"))
        self._fab_translated.setText(T("actions.translated.next"))
        self._fab_translated.setToolTip(T("actions.translated.cta.tip"))
        self.origin_toolbar.retranslate()
        self.translated_toolbar.retranslate()

    def set_font_size(self, px: int) -> None:
        """Apply the Settings font size as the base for both windows."""
        self.origin_panel.set_font_size(px)
        self.translated_panel.set_font_size(px)

    def set_translation_languages(
        self, src: str, dst: str, engine: str | None = None
    ) -> None:
        """Apply new source/target languages (and optionally engine)."""
        src = src or "auto"
        dst = dst or "it"
        engine = engine or self._engine
        changed = (
            src != self._source_lang
            or dst != self._target_lang
            or engine != self._engine
        )
        self._source_lang = src
        self._target_lang = dst
        self._engine = engine
        self._btn_translated.setText(flag_endonym(self._target_lang))
        # Keep the tab-bar engine radios in sync (Settings change → radios).
        for rb, code in (
            (self._radio_google, "google"),
            (self._radio_microsoft, "microsoft"),
        ):
            rb.blockSignals(True)
            rb.setChecked(engine == code)
            rb.blockSignals(False)
        if changed and self._btn_translated.isChecked():
            self._schedule_translation()

    def _on_engine_radio(self, checked: bool):
        """Live engine switch from the tab-bar radios.

        Persists the choice, shows a 2-second toast, and re-translates the
        current page when the translated tab is active (cache is keyed by
        engine, so switching engines naturally misses and re-translates).
        """
        if not checked:
            return  # toggled fires for both radios; act on the checked one
        code = "google" if self.sender() is self._radio_google else "microsoft"
        if code == self._engine:
            return
        self._engine = code
        set_translation_engine(code)
        save_config()
        # The origin window also shows the engine in its header: recompose it
        # so both windows agree after a live switch (same root cause as the
        # stale translated header).
        if self._header_prefix and not self._btn_translated.isChecked():
            self._page_text = self._recomposed_header() + self._page_body
            self._show_panel(self.origin_panel, self._page_text, self._render_md)
        self.toast.emit(
            T("toast.engine_changed", engine=T(f"engine.option.{code}")), 2000
        )
        if self._btn_translated.isChecked():
            self._maybe_show_translation()

    # ── translation header / translating toast ────────────────────────

    def _recomposed_header(self) -> str:
        """Header for the translated window, recomposed with the current engine.

        The header template (ms/chars/label/OCR + engine) is captured at
        ``show_text`` time; only the engine token is refreshed, so a live
        engine switch (radio buttons / Settings) is reflected above the
        translated text instead of keeping the stale name.
        """
        if not self._header_prefix:
            return ""
        return (
            f"{self._header_prefix} {T(f'engine.option.{self._engine}')}"
            f" ──{self._header_tail}"
        )

    # ── overlay animati (estrazione / traduzione) + indicatore tab ─────

    def _caption_for_translation(self) -> str:
        return T(
            "status.translating_engine",
            engine=T(f"engine.option.{self._engine}"),
        )

    def _show_translating_toast(self):
        """Show the liquid overlay over the translated text."""
        self._overlay_origin.stop()
        self._overlay_translated.setGeometry(self._translated_window.rect())
        self._overlay_translated.start(self._caption_for_translation())
        self.set_tab_busy(True)

    def _hide_translating_toast(self):
        self._overlay_translated.finish(True)
        self.set_tab_busy(False)

    def show_extraction_overlay(self, caption: str) -> None:
        """Show the liquid overlay over the origin text during extraction."""
        self._overlay_translated.stop()
        self._overlay_origin.setGeometry(self._origin_window.rect())
        self._overlay_origin.start(caption)
        self.set_tab_busy(True)

    def hide_extraction_overlay(self) -> None:
        self._overlay_origin.finish(True)
        self.set_tab_busy(False)

    def _position_overlays(self) -> None:
        if self._overlay_origin.isVisible():
            self._overlay_origin.setGeometry(self._origin_window.rect())
        if self._overlay_translated.isVisible():
            self._overlay_translated.setGeometry(self._translated_window.rect())

    def _tab_geometry(self, btn) -> QRect:
        h = self._tab_bar.height()
        return QRect(btn.x(), h - 3, btn.width(), 3)

    def _active_tab_button(self):
        for btn in (self._btn_original, self._btn_translated, self._btn_images):
            if btn.isChecked():
                return btn
        return self._btn_original

    def _move_tab_indicator(self, btn, animate: bool = True) -> None:
        """Slide (or jump) the indicator under the given tab button."""
        if btn is None or btn is self._btn_images:
            self._tab_indicator.hide()
            return
        self._tab_indicator.show()
        self._tab_indicator.raise_()
        target = self._tab_geometry(btn)
        if animate and self._tab_indicator.width() > 0:
            self._tab_anim.stop()
            self._tab_anim.setStartValue(self._tab_indicator.geometry())
            self._tab_anim.setEndValue(target)
            self._tab_anim.start()
        else:
            self._tab_indicator.setGeometry(target)

    def set_tab_busy(self, busy: bool) -> None:
        """Pulse the active-tab indicator while a job is running."""
        if busy:
            self._move_tab_indicator(self._active_tab_button(), animate=False)
            self._tab_pulse.start()
        else:
            self._tab_pulse.stop()
            self._tab_effect.setOpacity(1.0)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize:
            if obj in (self._origin_window, self._translated_window):
                self._position_overlays()
                self._reposition_fabs()
            elif obj is self._tab_bar:
                self._move_tab_indicator(self._active_tab_button(), animate=False)
        return super().eventFilter(obj, event)

    # ── FAB azioni post-elaborazione ───────────────────────────────────

    def _make_fab(self, kind: str, parent: QWidget) -> "_SheenButton":
        """Create the post-processing **CTA** button over the given window.

        Opzione B: il flottante è una **singola** azione "passo successivo" —
        *Traduci pagina* (Originale) / *Traduci la successiva* (lingua). Le
        azioni locali (copia/esporta/ri-estrai) stanno nella mini toolbar.
        """
        if kind == "origin":
            label, tip = T("actions.origin.translate"), T("actions.origin.cta.tip")
        else:
            label = T("actions.translated.next")
            tip = T("actions.translated.cta.tip")
        fab = _SheenButton(label, parent)
        fab.setCursor(Qt.CursorShape.PointingHandCursor)
        fab.setToolTip(tip)
        fab.clicked.connect(lambda _=False, k=kind: self._on_fab_click(k))
        # Badge: pallino persistente finché la CTA non viene premuta.
        badge = QLabel(fab)
        badge.setFixedSize(8, 8)
        badge.setObjectName("fabBadge")
        fab._badge = badge
        fab._glow_anim = None
        self._theme_fab(fab)
        return fab

    def _on_fab_click(self, kind: str) -> None:
        """Primary next step: translate this page / translate the next page."""
        fab = self._fab_origin if kind == "origin" else self._fab_translated
        fab._badge.hide()
        if kind == "origin":
            self.translate_page_requested.emit()
        else:
            self.translate_next_requested.emit()

    def _theme_fab(self, fab: "_SheenButton") -> None:
        q = theme.color
        fab.setStyleSheet(
            "QPushButton { background: %s; color: %s; border: 2px solid %s;"
            " border-radius: 15px; padding: 7px 14px; font-size: 13px;"
            " font-weight: bold; }"
            "QPushButton:hover { background: %s; border-color: %s; }"
            % (
                q("fab_bg"), q("fab_text"), q("fab_border"),
                q("fab_hover_bg"), q("fab_hover_border"),
            )
        )
        fab.set_sheen_color(theme.color("fab_spark"))
        fab._badge.setStyleSheet(
            "background: %s; border-radius: 4px;" % q("badge")
        )

    def _reposition_fabs(self) -> None:
        for fab, win in (
            (self._fab_origin, self._origin_window),
            (self._fab_translated, self._translated_window),
        ):
            fab.adjustSize()
            x = max(8, win.width() - fab.width() - 16)
            y = max(8, win.height() - fab.height() - 16)
            fab.move(x, y)
            fab._badge.move(fab.width() - 9, 3)

    def _update_fab_visibility(self) -> None:
        """Show the FAB of the active tab once its content is ready."""
        origin_ready = (
            self._btn_original.isChecked()
            and self._current_page >= 0
            and bool(self._page_text)
        )
        translated_ready = (
            self._btn_translated.isChecked()
            and self._current_page >= 0
            and bool(self._translated_text)
        )
        self._set_fab_visible(self._fab_origin, origin_ready)
        self._set_fab_visible(self._fab_translated, translated_ready)

    def _set_fab_visible(self, fab: "_SheenButton", visible: bool) -> None:
        if visible and not fab.isVisible():
            self._reposition_fabs()
            fab.show()
            fab.raise_()
            fab._badge.show()
            self._start_fab_glow(fab)
            fab.start_sheen()
        elif not visible and fab.isVisible():
            fab.hide()
            self._stop_fab_glow(fab)
            fab.stop_sheen()

    def _start_fab_glow(self, fab: "_SheenButton") -> None:
        eff = fab.graphicsEffect()
        if not isinstance(eff, QGraphicsDropShadowEffect):
            eff = QGraphicsDropShadowEffect(fab)
            fab.setGraphicsEffect(eff)
        eff.setColor(QColor(theme.color("fab_glow")))
        eff.setOffset(0, 0)
        anim = QPropertyAnimation(eff, b"blurRadius", fab)
        anim.setDuration(1700)
        anim.setStartValue(18.0)
        anim.setEndValue(52.0)
        anim.setLoopCount(-1)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        fab._glow_anim = anim
        anim.start()

    def _stop_fab_glow(self, fab: "_SheenButton") -> None:
        anim = getattr(fab, "_glow_anim", None)
        if anim is not None:
            anim.stop()
            fab._glow_anim = None
        eff = fab.graphicsEffect()
        if isinstance(eff, QGraphicsDropShadowEffect):
            eff.setBlurRadius(0.0)

    def show_text(
        self,
        text: str,
        as_markdown: bool = True,
        page_num: int = -1,
        images: list[str] | None = None,
    ):
        """Display the page text (already the auto-or-manual result).

        ``page_num`` keys the per-page translation cache; ``images`` are the
        file:// URIs of the regions captured for the page.
        """
        if page_num != self._current_page:
            # Nuova pagina: i contenuti precedenti non valgono più.
            self._translated_text = ""
            self._set_fab_visible(self._fab_translated, False)
            self._set_fab_visible(self._fab_origin, False)
        self._render_md = as_markdown
        self._page_text = text
        self._current_page = page_num
        self._images = list(images or [])
        # The header line (``── … Trad: {engine} ──``) must never be sent to
        # the translator: it is baked at display time and would carry a stale
        # engine name. Translate only the body and recompose the header with
        # the *current* engine when the translated result is shown.
        body = _strip_header(text)
        self._page_body = body
        header = text[: len(text) - len(body)]
        if header:
            # The header template ends with the engine name right before the
            # closing "──": drop that baked name from the prefix, so the
            # recomposed header can swap in the *current* engine later.
            prefix, tail = header.rsplit("──", 1)
            old_engine = T(f"engine.option.{self._engine}")
            stripped = prefix.rstrip()  # the template ends "…{engine} ──"
            if stripped.endswith(old_engine):
                prefix = stripped[: -len(old_engine)].rstrip()
            self._header_prefix = prefix
            self._header_tail = tail
        else:
            self._header_prefix = ""
            self._header_tail = ""
        self._rebuild_images_panel()

        if self._btn_images.isChecked():
            return  # images tab active — gallery already rebuilt above

        if self._btn_translated.isChecked():
            self._maybe_show_translation()
            return

        # Original tab is active (default)
        self._set_active_tab(self._btn_original)
        self._show_panel(self.origin_panel, self._page_text, as_markdown)
        self._lbl_spinner.setText("")
        self._update_fab_visibility()

    def _set_active_tab(self, active):
        for btn in (self._btn_original, self._btn_translated, self._btn_images):
            btn.setChecked(btn is active)
        self._move_tab_indicator(active)
        if active is self._btn_images:
            self._stack.setCurrentWidget(self._objects_window)
            self._rebuild_images_panel()
        elif active is self._btn_translated:
            self._stack.setCurrentWidget(self._translated_window)
        else:
            self._stack.setCurrentWidget(self._origin_window)
        self._update_fab_visibility()
        if get_setting("remember_tab", True):
            if active is self._btn_translated:
                tab_id = "translated"
            elif active is self._btn_images:
                tab_id = "images"
            else:
                tab_id = "original"
            if get_setting("last_tab", "") != tab_id:
                set_setting("last_tab", tab_id)
                save_config()

    def _show_panel(
        self, panel: TextPanel, text: str, as_markdown: bool
    ) -> None:
        """Show text in a window, re-applying the user's saved edits if any.

        Edits are keyed by the content without the extraction header, so they
        survive navigation, reopen and header re-renders (language/engine
        switches); the current header is recomposed on display.
        """
        window = "origin" if panel is self.origin_panel else "translated"
        body = _strip_header(text)
        saved = None
        if self._save_edits:
            saved = self._edit_cache.get(window, {}).get(_text_key(body))
        edited = None
        if saved is not None and saved != body:
            # ri-attacca l'header corrente al testo modificato salvato
            edited = text[: len(text) - len(body)] + saved
        panel.show_text(text, as_markdown=as_markdown, edited=edited)
        # indicatore: la finestra mostra una modifica ancora in attesa di
        # essere scritta su disco (timer di salvataggio attivo)
        pending = self._edits_save_timer.isActive()
        self._set_dirty(window, pending and edited is not None and edited != text)

    def _on_text_edited(self, base: str, edited: str) -> None:
        """Store a user edit (header-stripped) and schedule a disk save."""
        if not self._save_edits:
            return
        window = "origin" if self.sender() is self.origin_panel else "translated"
        body = _strip_header(base)
        if not body:
            return
        self._edit_cache.setdefault(window, {})[_text_key(body)] = _strip_header(edited)
        self._edits_save_timer.start()

    def _on_window_modified(self, window: str, modified: bool) -> None:
        """Track the unsaved-edits dot from the user's typing."""
        if not self._save_edits:
            self._set_dirty(window, False)
            return
        self._set_dirty(window, modified)

    def _set_dirty(self, window: str, dirty: bool) -> None:
        """Update the toolbar dot, avoiding redundant repaints."""
        if self._dirty.get(window) == dirty:
            return
        self._dirty[window] = dirty
        toolbar = self.origin_toolbar if window == "origin" else self.translated_toolbar
        toolbar.set_dirty(dirty)

    def _export_window(self, panel: TextPanel) -> None:
        """Export the window content (.md or .txt), edits included.

        The extraction header line is excluded: it is display metadata, not
        document content.
        """
        content = _strip_header(panel.toPlainText())
        window = "original" if panel is self.origin_panel else "translated"
        page = max(self._current_page + 1, 1)
        stem = self._doc_stem or "documento"
        dest, _ = QFileDialog.getSaveFileName(
            self,
            T("editor.export_dialog"),
            f"{stem}_pag{page}_{window}",
            T("editor.export_filter"),
        )
        if not dest:
            return
        if not dest.lower().endswith((".md", ".txt")):
            dest += ".md"
        try:
            Path(dest).write_text(content, encoding="utf-8")
            self._lbl_spinner.setText(T("status.exported"))
        except Exception:
            self._lbl_spinner.setText(T("editor.export_error"))

    def show_original(self):
        """Switch to the "Originale" text tab (keeps current content)."""
        self._set_active_tab(self._btn_original)
        self._lbl_spinner.setText("")

    def _on_show_original(self):
        self.show_original()
        self._show_panel(self.origin_panel, self._page_text, self._render_md)

    def _on_show_translated(self):
        self._set_active_tab(self._btn_translated)
        self._maybe_show_translation()

    def _on_show_images(self):
        self._set_active_tab(self._btn_images)
        self._lbl_spinner.setText("")

    def _maybe_show_translation(self):
        """Show the cached translation for the page, or schedule a new one."""
        key = (self._current_page, self._engine, self._target_lang)
        cached = self._page_translation_cache.get(key)
        if self._current_page >= 0 and cached is not None:
            self._translated_text = cached
            self._show_panel(self.translated_panel, cached, self._render_md)
            self._lbl_spinner.setText("")
            self._hide_translating_toast()
            self._update_fab_visibility()
        else:
            self._schedule_translation()

    def _schedule_translation(self):
        """Debounce: re-translate only after the user pauses drawing."""
        self._lbl_spinner.setText("")
        self._set_fab_visible(self._fab_translated, False)
        self._show_translating_toast()
        self._pending_translation = True
        self._translate_timer.start()

    def _flush_translation(self):
        self._pending_translation = False
        if not self._btn_translated.isChecked():
            return
        key = (self._current_page, self._engine, self._target_lang)
        if self._current_page >= 0 and key in self._page_translation_cache:
            # Debounce obsoleto: la pagina corrente è già tradotta per questa
            # destinazione (es. sbirciata avanti e ritorno) → mostra la cache
            # invece di ritradurre inutilmente.
            self._show_panel(
                self.translated_panel,
                self._page_translation_cache[key],
                self._render_md,
            )
            self._lbl_spinner.setText("")
            self._hide_translating_toast()
            self._update_fab_visibility()
            return
        self._start_translation()

    def show_images(self, images: list[str], activate: bool = True):
        """Set the current page's captured regions and (by default) activate
        the gallery tab. Pass ``activate=False`` to update the gallery without
        switching away from the text window (used by zone exclusion)."""
        self._images = list(images or [])
        self._rebuild_images_panel()
        if activate:
            self._set_active_tab(self._btn_images)

    def set_capture(
        self, uri: str, kind: str, text: str = "", method: str = "none",
        page: int = -1, source: str = "capture", rebuild: bool = True,
    ) -> None:
        """Register a captured object (image/table/text) and refresh gallery."""
        self._captures[uri] = Capture(
            uri=uri, kind=kind, text=text, method=method, source=source,
            page=page,
        )
        if rebuild:
            self._rebuild_images_panel()

    def _copy_capture_text(self, uri: str) -> None:
        cap = self._captures.get(uri)
        text = cap.text if cap else ""
        if text:
            QApplication.clipboard().setText(text)
            self._lbl_spinner.setText(T("status.copied"))

    def _save_capture_text(self, uri: str) -> None:
        cap = self._captures.get(uri)
        text = cap.text if cap else ""
        if not text:
            return
        stem = self._doc_stem or "cattura"
        default = f"{stem}_cattura.md"
        dest, _ = QFileDialog.getSaveFileName(
            self, T("capture.save_dialog"), default, T("export.filter"))
        if not dest:
            return
        if not dest.lower().endswith(".md"):
            dest += ".md"
        try:
            Path(dest).write_text(text, encoding="utf-8")
            self._lbl_spinner.setText(T("status.saved"))
        except Exception:  # noqa: BLE001
            pass

    def clear_pages(self, message: str = ""):
        """Svuota i pannelli (dopo il reset cache) e mostra un avviso.

        Non riestrae: azzera contenuti/immagini/traduzioni così non restano
        testi vecchi a schermo e mostra ``message`` nel pannello Originale
        (invito a riestrarre).
        """
        self._page_text = ""
        self._page_body = ""
        self._header_prefix = ""
        self._header_tail = ""
        self._translated_text = ""
        self._images = []
        self._captures.clear()
        self._page_translation_cache.clear()
        self._save_disk_cache()
        self._rebuild_images_panel()
        self.origin_panel.show_text(message or "", as_markdown=False)
        self.translated_panel.show_text("", as_markdown=False)
        self._set_active_tab(self._btn_original)
        self._update_fab_visibility()

    # ── Images gallery ─────────────────────────────────────────────────

    # ── Oggetti (immagini / tabelle / testi) ────────────────────────────

    def _object_kind(self, uri: str) -> str:
        """Kind of a gallery URI: from its capture, else ``image`` (figure)."""
        cap = self._captures.get(uri)
        return cap.kind if cap else "image"

    def _object_groups(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {"image": [], "table": [], "text": []}
        for uri in self._images:
            groups.setdefault(self._object_kind(uri), []).append(uri)
        return groups

    def _on_objects_filter(self, key: str) -> None:
        self._objects_filter = key or "all"
        self._rebuild_images_panel()

    def _section_header(self, kind: str, n: int) -> QWidget:
        lbl = QLabel(f"{T('objects.kind.' + kind)} · {n}")
        lbl.setStyleSheet(
            "color: %s; font-size: 12px; font-weight: bold; padding: 6px 2px;"
            % theme.color("doc_text")
        )
        return lbl

    def _rebuild_images_panel(self):
        """Rebuild the objects gallery (grouped/filtered by kind)."""
        container = QWidget()
        container.setObjectName("galleryContainer")
        # fondo esplicito: la galleria resta chiara su qualunque tema di
        # sistema (il viewport dello QScrollArea potrebbe ereditare il palette)
        container.setStyleSheet(
            "#galleryContainer { background: %s; }" % theme.color("doc_pre_bg")
        )
        lay = QVBoxLayout(container)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        groups = self._object_groups()
        total = sum(len(v) for v in groups.values())
        self.objects_toolbar.set_count(total)
        self.objects_toolbar.set_bulk_enabled(total > 0)

        kinds = (("image", "table", "text")
                 if self._objects_filter == "all"
                 else (self._objects_filter,))
        shown = 0
        for kind in kinds:
            uris = groups.get(kind) or []
            if not uris:
                continue
            if self._objects_filter == "all":
                lay.addWidget(self._section_header(kind, len(uris)))
            for uri in uris:
                lay.addWidget(self._make_image_card(uri))
                shown += 1

        if shown == 0:
            hint = QLabel(T("objects.empty"))
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setWordWrap(True)
            hint.setStyleSheet(
                "color: %s; font-size: 14px; padding: 24px;"
                % theme.color("doc_em")
            )
            lay.addWidget(hint)
        lay.addStretch()

        old = self.images_panel.takeWidget()
        if old is not None:
            old.deleteLater()
        self.images_panel.setWidget(container)

    def _make_image_card(self, uri: str) -> QWidget:
        # figure automatiche: data URI base64; catture manuali: file://
        is_data = uri.startswith("data:")
        path = "" if is_data else str(QUrl(uri).toLocalFile())
        cap = self._captures.get(uri)
        kind = cap.kind if cap else "image"
        text = cap.text if cap else ""
        card = QWidget()
        card.setObjectName("imgCard")
        card.setStyleSheet(
            "QWidget#imgCard { background: %s; border: 1px solid %s;"
            " border-radius: 6px; }"
            % (theme.color("doc_bg"), theme.color("doc_border"))
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        pix = QPixmap.fromImage(_uri_qimage(uri))
        thumb = QLabel()
        thumb.setPixmap(
            pix.scaledToWidth(340, Qt.TransformationMode.SmoothTransformation)
        )
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        thumb.setToolTip(T("gallery.zoom_tip"))
        thumb.mousePressEvent = lambda _e, u=uri: self._show_image_full(u)
        v.addWidget(thumb)

        label = f"{T('objects.kind.' + kind)}  ·  {pix.width()}×{pix.height()} px"
        if not is_data:
            label = f"{Path(path).name}  ·  {label}"
        info = QLabel(label)
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet(
            "border: none; color: %s; font-size: 12px;" % theme.color("doc_em")
        )
        v.addWidget(info)

        btn_style = (
            "QPushButton { background: %s; color: %s; border: none;"
            " border-radius: 4px; padding: 6px 12px; font-size: 12px; }"
            "QPushButton:hover { background: %s; }"
            % (
                theme.color("accent"), theme.color("accent_text"),
                theme.color("accent_hover"),
            )
        )

        # Oggetti con testo (tabella/testo): box markdown + azioni dedicate.
        if text and kind in ("table", "text"):
            box = QTextEdit()
            box.setReadOnly(True)
            box.setPlainText(text)
            box.setFixedHeight(120)
            box.setStyleSheet(
                "QTextEdit { background: %s; color: %s; border: 1px solid %s;"
                " font-family: 'Consolas','monospace'; font-size: 12px; }"
                % (theme.color("doc_bg"), theme.color("doc_text"),
                   theme.color("doc_border"))
            )
            v.addWidget(box)
            trow = QHBoxLayout()
            copy_label = (T("objects.copy_md") if kind == "table"
                          else T("capture.copy_text"))
            btn_copy_text = QPushButton(copy_label)
            btn_copy_text.clicked.connect(
                lambda _=False, u=uri: self._copy_capture_text(u))
            btn_save_md = QPushButton(T("objects.save_md"))
            btn_save_md.clicked.connect(
                lambda _=False, u=uri: self._save_capture_text(u))
            for b in (btn_copy_text, btn_save_md):
                b.setStyleSheet(btn_style)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                trow.addWidget(b)
            trow.addStretch(1)
            v.addLayout(trow)

        # Azioni per tipo: immagine → salva/copia immagine; tabella/testo →
        # copia/salva testo (sopra). Rimuovi è comune.
        row = QHBoxLayout()
        if kind == "image":
            btn_save = QPushButton(T("gallery.save"))
            btn_save.clicked.connect(lambda _=False, u=uri: self._save_image(u))
            btn_copy = QPushButton(T("gallery.copy"))
            btn_copy.clicked.connect(lambda _=False, u=uri: self._copy_image(u))
            for b in (btn_save, btn_copy):
                b.setStyleSheet(btn_style)
                b.setCursor(Qt.CursorShape.PointingHandCursor)
                row.addWidget(b)
        btn_remove = QPushButton(T("gallery.remove"))
        btn_remove.clicked.connect(lambda _=False, u=uri: self._remove_image(u))
        btn_remove.setStyleSheet(
            "QPushButton { background: %s; color: %s; border: none;"
            " border-radius: 4px; padding: 6px 12px; font-size: 12px; }"
            "QPushButton:hover { background: %s; }"
            % (
                theme.color("err"), theme.color("accent_text"),
                theme.color("err_hover"),
            )
        )
        btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(btn_remove)
        v.addLayout(row)
        return card

    def _export_all_objects(self) -> None:
        """Export every object to a chosen folder (images PNG, text .md)."""
        if not self._images:
            return
        folder = QFileDialog.getExistingDirectory(
            self, T("objects.export_dialog"))
        if not folder:
            return
        base = Path(folder)
        stem = self._doc_stem or "oggetti"
        for i, uri in enumerate(self._images):
            cap = self._captures.get(uri)
            kind = cap.kind if cap else "image"
            try:
                if kind == "image":
                    img = _uri_qimage(uri)
                    if not img.isNull():
                        img.save(str(base / f"{stem}_oggetto_{i + 1:03d}.png"))
                else:
                    text = cap.text if cap else ""
                    (base / f"{stem}_oggetto_{i + 1:03d}.md").write_text(
                        text, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
        self._lbl_spinner.setText(T("objects.exported"))

    def _remove_all_objects(self) -> None:
        """Remove every object of the current page (delegates to the window)."""
        for uri in list(self._images):
            self._remove_image(uri)

    def _show_image_full(self, uri: str):
        is_data = uri.startswith("data:")
        path = "" if is_data else str(QUrl(uri).toLocalFile())
        dlg = QDialog(self)
        dlg.setWindowTitle("" if is_data else Path(path).name)
        dlg.resize(900, 720)
        # finestra top-level: colori del tema attivo
        dlg.setStyleSheet(
            "QDialog { background: %s; color: %s; }"
            " QScrollArea { background: %s; border: none; }"
            % (
                theme.color("bg"), theme.color("text2"),
                theme.color("bg"),
            )
        )
        lay = QVBoxLayout(dlg)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        lbl = QLabel()
        lbl.setPixmap(QPixmap.fromImage(_uri_qimage(uri)))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(lbl)
        lay.addWidget(scroll)
        dlg.exec()

    def _copy_image(self, uri: str):
        img = _uri_qimage(uri)
        if not img.isNull():
            QApplication.clipboard().setImage(img)
            self._lbl_spinner.setText(T("status.copied"))

    def _save_image(self, uri: str):
        is_data = uri.startswith("data:")
        src = "" if is_data else str(QUrl(uri).toLocalFile())
        default = "figura.jpg" if is_data else Path(src).name
        dest, _ = QFileDialog.getSaveFileName(
            self,
            T("gallery.save_dialog"),
            default,
            T("gallery.save_filter"),
        )
        if not dest:
            return
        if is_data:
            data = _uri_bytes(uri)
            if data:
                try:
                    Path(dest).write_bytes(data)
                    self._lbl_spinner.setText(T("status.saved"))
                except Exception:
                    pass
        else:
            shutil.copyfile(src, dest)
            self._lbl_spinner.setText(T("status.saved"))

    def _remove_image(self, uri: str):
        """Ask the main window to drop a captured image from the gallery."""
        self.image_removed.emit(uri)

    def _start_translation(self):
        """Fire a background translation for the current page text."""
        # Translate only the body: the extraction header is recomposed at
        # display time with the current engine (see ``_recomposed_header``),
        # so a live engine switch updates the name above the translated text.
        text = self._page_body
        self._lbl_spinner.setText("")
        self._set_fab_visible(self._fab_translated, False)
        self._show_translating_toast()
        self._generation += 1

        old = self._thread
        if old is not None:
            for sig in (old.result_ready, old.error_ready):
                try:
                    sig.disconnect()
                except TypeError:
                    pass  # already disconnected
            if old.isRunning():
                # Non distruggere un thread ancora attivo: lo si ritira e si
                # pulisce quando termina da solo.
                self._retired_threads.append(old)
                old.finished.connect(self._forget_retired_thread)

        thread = TranslateThread(
            text, generation=self._generation, kind="page",
            source=self._source_lang, target=self._target_lang,
            engine=self._engine,
        )
        thread.result_ready.connect(self._on_translation_done)
        thread.error_ready.connect(self._on_translation_error)
        self._thread = thread
        thread.start()

    def _forget_retired_thread(self):
        """Drop a retired thread from the keep-alive list once it finishes."""
        thread = self.sender()
        if thread in self._retired_threads:
            self._retired_threads.remove(thread)

    def _on_translation_done(self, generation: int, kind: str, translated: str):
        """Slot: background translation finished."""
        # Ignore stale results from superseded requests
        if generation != self._generation:
            return

        # Attach the header recomposed with the *current* engine, so the
        # engine name above the translated text always matches the active one
        # (e.g. after a live switch from Google to Microsoft).
        displayed = self._recomposed_header() + translated
        self._translated_text = displayed

        # Cache per (pagina, engine, destinazione)
        if self._current_page >= 0:
            self._page_translation_cache[
                (self._current_page, self._engine, self._target_lang)
            ] = displayed
            self._save_disk_cache()

        # Show if the translated tab is active
        if self._btn_translated.isChecked():
            self._show_panel(self.translated_panel, displayed, self._render_md)

        self._lbl_spinner.setText("✅")
        self._hide_translating_toast()
        self._update_fab_visibility()

    def _on_translation_error(self, generation: int, message: str):
        """Slot: engine failed on every chunk — surface it instead of the
        silent no-op that used to leave untranslated text on screen."""
        if generation != self._generation:
            return
        engine = T(f"engine.option.{self._engine}")
        self._lbl_spinner.setText(
            T("status.translation_error", engine=engine, reason=message)
        )
        self._hide_translating_toast()
        self._update_fab_visibility()

    def show_html(self, html_body: str):
        """Forward to the Original text window."""
        self.origin_panel.show_html(html_body)

    # ── persistent translation cache ───────────────────────────────────

    def set_document(self, path: Path | None):
        """Point the caches at this PDF and load saved translations + edits."""
        self._save_edits_cache()  # flush pending edits of the previous doc
        self._page_translation_cache.clear()
        self._captures.clear()
        self._cache_file = None
        self._edit_cache = {"origin": {}, "translated": {}}
        self._edits_cache_file = None
        self._doc_fingerprint = ""
        if path is None:
            self._doc_stem = ""
            return
        self._doc_stem = path.stem
        try:
            st = path.stat()
            self._doc_fingerprint = f"{st.st_size}-{st.st_mtime_ns}"
        except Exception:
            return
        cache_dir = _app_data_base() / "translation"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        self._cache_file = cache_dir / f"{path.stem}.json"
        self._load_disk_cache()
        edits_dir = _app_data_base() / "edits"
        try:
            edits_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        self._edits_cache_file = edits_dir / f"{path.stem}.json"
        if self._save_edits:
            self._load_edits_cache()

    def _load_disk_cache(self):
        """Load saved translations when they match the current PDF fingerprint."""
        if self._cache_file is None or not self._cache_file.exists():
            return
        try:
            data = json.loads(self._cache_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if data.get("fingerprint") != self._doc_fingerprint:
            return
        pages = data.get("pages") or {}
        for key, value in pages.items():
            try:
                page = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(value, str):
                # vecchio formato {page: testo}: la destinazione era sempre it
                self._page_translation_cache[(page, "google", "it")] = value
            elif isinstance(value, dict):
                if "origin" in value or "cleaned" in value:
                    # formato pre-v2 {origin, cleaned} → migra a destinazione it
                    migrated = value.get("cleaned") or value.get("origin")
                    if isinstance(migrated, str):
                        self._page_translation_cache[(page, "google", "it")] = migrated
                else:
                    # formato v2 {page: {target: testo}} → engine google;
                    # formato v3 {page: {"engine:target": testo}}
                    for tgt, txt in value.items():
                        if not isinstance(txt, str):
                            continue
                        if ":" in tgt:
                            engine, lang = tgt.split(":", 1)
                            if (
                                engine in TRANSLATION_ENGINES
                                and lang in TRANSLATION_LANGUAGES
                                and lang != "auto"
                            ):
                                self._page_translation_cache[(page, engine, lang)] = txt
                        elif tgt in TRANSLATION_LANGUAGES and tgt != "auto":
                            self._page_translation_cache[(page, "google", tgt)] = txt

    def _save_disk_cache(self):
        """Persist the in-memory translation cache to disk."""
        if self._cache_file is None:
            return
        pages: dict[str, dict[str, str]] = {}
        for (page, engine, tgt), txt in self._page_translation_cache.items():
            pages.setdefault(str(page), {})[f"{engine}:{tgt}"] = txt
        payload = {
            "fingerprint": self._doc_fingerprint,
            "pages": pages,
        }
        try:
            self._cache_file.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    # ── persistent user-edits cache ─────────────────────────────────────

    def _load_edits_cache(self):
        """Load saved user edits when they match the current PDF fingerprint."""
        if self._edits_cache_file is None or not self._edits_cache_file.exists():
            return
        try:
            data = json.loads(self._edits_cache_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if data.get("fingerprint") != self._doc_fingerprint:
            return
        for window in ("origin", "translated"):
            entries = data.get(window)
            if isinstance(entries, dict):
                for key, value in entries.items():
                    if isinstance(value, str):
                        self._edit_cache[window][key] = value

    def _save_edits_cache(self, force: bool = False):
        """Persist the user edits to disk.

        No-op while saving is disabled (``force`` overrides it, used by
        ``clear_saved_edits`` which must wipe the file on purpose).
        """
        if not self._save_edits and not force:
            return
        if self._edits_cache_file is None:
            return
        payload = {"fingerprint": self._doc_fingerprint, **self._edit_cache}
        try:
            self._edits_cache_file.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            return
        # tutto ciò che era in attesa è ora su disco: la baseline di
        # confronto delle finestre diventa il contenuto appena salvato
        self.origin_panel._rendered_text = self.origin_panel.toPlainText()
        self.translated_panel._rendered_text = self.translated_panel.toPlainText()
        self._set_dirty("origin", False)
        self._set_dirty("translated", False)

    def set_save_edits(self, enabled: bool) -> None:
        """Enable/disable the persistence (and application) of text edits.

        Disabling stops saving and drops the in-memory cache (the on-disk
        file is preserved); re-enabling reloads the saved edits.
        """
        enabled = bool(enabled)
        if enabled == self._save_edits:
            return
        self._save_edits = enabled
        self._edits_save_timer.stop()
        if enabled:
            self._load_edits_cache()
            # il contenuto mostrato non cambia: forza il re-render delle
            # finestre così le modifiche salvate tornano visibili subito
            self._force_reshow()
        else:
            # flush le modifiche pendenti fatte mentre era attivo, poi
            # abbandona la cache in memoria (il file su disco resta)
            self._save_edits_cache(force=True)
            self._edit_cache = {"origin": {}, "translated": {}}
            self._set_dirty("origin", False)
            self._set_dirty("translated", False)

    def _force_reshow(self) -> None:
        """Re-render the current page, bypassing the idempotent show.

        Used after the edit cache changes (re-enable saving / clear) so the
        current view reflects it immediately.
        """
        self.origin_panel._shown_buffer = None
        self.translated_panel._shown_buffer = None
        if self._current_page >= 0:
            if self._btn_translated.isChecked():
                self._maybe_show_translation()
            else:
                self._show_panel(
                    self.origin_panel, self._page_text, self._render_md
                )

    def clear_saved_edits(self) -> None:
        """Wipe the current document's saved edits (memory + disk)."""
        self._edits_save_timer.stop()
        self._edit_cache = {"origin": {}, "translated": {}}
        self._save_edits_cache(force=True)
        self._set_dirty("origin", False)
        self._set_dirty("translated", False)
        self._force_reshow()

    def invalidate_cache(self):
        """Clear per-page translation cache."""
        self._page_translation_cache.clear()

    def invalidate_page(self, page_num: int):
        """Drop the cached translations for one page (its content changed)."""
        for k in [k for k in self._page_translation_cache if k[0] == page_num]:
            del self._page_translation_cache[k]
        self._save_disk_cache()

    def shutdown(self):
        """Wait for any in-flight translation before the app closes."""
        self._translate_timer.stop()
        self._edits_save_timer.stop()
        self._save_edits_cache()
        for t in self._retired_threads:
            if t.isRunning():
                t.wait(3000)
        self._retired_threads.clear()
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(5000)
        self._save_disk_cache()


class TocPanel(QWidget):
    """Dockable multi-level table of contents navigator."""

    page_selected = pyqtSignal(int)  # 0-based page index

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.apply_theme()
        layout.addWidget(self.tree)

        self._items: list[QTreeWidgetItem] = []  # flat, in document order
        self._syncing: bool = False

        self.tree.itemClicked.connect(self._on_item_clicked)

    def apply_theme(self) -> None:
        """Re-apply the theme colors to the tree."""
        q = theme.color
        self.tree.setStyleSheet(
            "QTreeWidget { background: %s; color: %s; border: none;"
            " font-size: 13px; }"
            "QTreeWidget::item { padding: 2px 0; }"
            "QTreeWidget::item:selected { background: %s; color: %s; }"
            % (q("tree_bg"), q("tree_text"), q("tree_sel_bg"), q("tree_sel_text"))
        )

    def build_toc(self, doc) -> None:
        """Rebuild the tree from a pymupdf Document's bookmarks."""
        self.tree.clear()
        self._items = []

        stack: list[tuple[int, QTreeWidgetItem]] = []  # (level, item)
        try:
            bookmarks = doc.get_toc(simple=True)
        except Exception:
            bookmarks = []

        for level, title, page in bookmarks:
            page_idx = page - 1  # pymupdf is 1-based
            if page_idx < 0:
                continue
            title = (title or "").strip() or T("toc.no_title")
            item = QTreeWidgetItem(
                [T("toc.page_fmt", title=title, page=page_idx + 1)]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, page_idx)

            level = max(0, int(level))
            while stack and stack[-1][0] >= level:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            stack.append((level, item))
            self._items.append(item)

        self.tree.expandAll()

    def select_page(self, page_num: int) -> None:
        """Highlight the TOC entry that best matches the given 0-based page."""
        best: QTreeWidgetItem | None = None
        for item in self._items:
            p = item.data(0, Qt.ItemDataRole.UserRole)
            if p is None:
                continue
            if p <= page_num:
                best = item
            else:
                break

        if best is None:
            return
        self._syncing = True
        self.tree.setCurrentItem(best)
        self.tree.scrollToItem(best)
        self._syncing = False

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int):
        if self._syncing:
            return
        page_idx = item.data(0, Qt.ItemDataRole.UserRole)
        if page_idx is None:
            return
        self.page_selected.emit(int(page_idx))


# ═══════════════════════════════════════════════════════════════════════════════
#  settings dialog
# ═══════════════════════════════════════════════════════════════════════════════


class SettingsDialog(QDialog):
    """Menu di configurazione: lingua UI, lingue traduzione, preferenze.

    Ogni stringa visibile passa da T(), quindi il dialogo è mostrato nella
    lingua UI corrente. Cambiare la lingua UI dentro il dialogo fa un'anteprima
    dal vivo (ri-traduce solo le label del dialogo); Annulla ripristina la
    lingua originale senza toccare il MainWindow.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._orig_ui_lang = get_language()
        self._cfg = get_config()

        self.setWindowTitle(T("settings.title"))
        self.setMinimumWidth(480)
        self.setStyleSheet(theme.settings_qss())

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Contenuto scrollabile: con i gruppi Aspetto/Avanzate/Notifiche il
        # dialogo non entra più in una schermata (pattern del cloner).
        self._scroll = QScrollArea()
        self._scroll.setObjectName("settingsScroll")
        self._scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("settingsContent")
        inner = QVBoxLayout(content)
        inner.setSpacing(10)

        # ── Lingua ──────────────────────────────────────────────────────
        self._box_lang = QGroupBox(T("settings.group.lang"))
        lang_form = QFormLayout(self._box_lang)
        self._lbl_ui = QLabel(T("settings.lang.ui"))
        self._ui_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self._ui_combo.addItem(name, code)
        lang_form.addRow(self._lbl_ui, self._ui_combo)
        self._lbl_src = QLabel(T("settings.lang.source"))
        self._src_combo = QComboBox()
        for code, (flag, name) in TRANSLATION_LANGUAGES.items():
            self._src_combo.addItem(f"{flag} {name}", code)
        lang_form.addRow(self._lbl_src, self._src_combo)
        self._lbl_dst = QLabel(T("settings.lang.target"))
        self._dst_combo = QComboBox()
        for code, (flag, name) in TRANSLATION_LANGUAGES.items():
            if code == "auto":
                continue
            self._dst_combo.addItem(f"{flag} {name}", code)
        lang_form.addRow(self._lbl_dst, self._dst_combo)
        inner.addWidget(self._box_lang)

        # ── Traduzione ──────────────────────────────────────────────────
        self._box_translation = QGroupBox(T("settings.group.translation"))
        trans_form = QFormLayout(self._box_translation)
        self._lbl_engine = QLabel(T("settings.translation.engine"))
        self._engine_combo = QComboBox()
        for code in TRANSLATION_ENGINES:
            self._engine_combo.addItem(T(f"engine.option.{code}"), code)
        trans_form.addRow(self._lbl_engine, self._engine_combo)
        inner.addWidget(self._box_translation)

        # ── Testo ───────────────────────────────────────────────────────
        self._box_text = QGroupBox(T("settings.group.text"))
        text_form = QFormLayout(self._box_text)
        self._lbl_font = QLabel(T("settings.text.font"))
        self._font_spin = QSpinBox()
        self._font_spin.setRange(10, 16)
        text_form.addRow(self._lbl_font, self._font_spin)
        self._md_check = QCheckBox(T("settings.text.md"))
        text_form.addRow(self._md_check)
        self._header_check = QCheckBox(T("settings.text.header"))
        text_form.addRow(self._header_check)
        self._save_edits_check = QCheckBox(T("settings.edits.save"))
        text_form.addRow(self._save_edits_check)
        self._btn_clear_edits = QPushButton(T("settings.edits.clear"))
        self._btn_clear_edits.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear_edits.clicked.connect(self._on_clear_edits)
        text_form.addRow(self._btn_clear_edits)
        inner.addWidget(self._box_text)

        # ── Visualizzazione (qualità di rendering) ──────────────────────
        self._box_view = QGroupBox(T("settings.group.view"))
        view_form = QFormLayout(self._box_view)
        self._lbl_zoom = QLabel(T("settings.view.render_quality"))
        self._zoom_spin = QDoubleSpinBox()
        self._zoom_spin.setRange(0.5, 4.0)
        self._zoom_spin.setSingleStep(0.25)
        self._zoom_spin.setDecimals(2)
        view_form.addRow(self._lbl_zoom, self._zoom_spin)
        inner.addWidget(self._box_view)

        # ── Aspetto (tema) ──────────────────────────────────────────────
        self._box_appearance = QGroupBox(T("settings.group.appearance"))
        appear_form = QFormLayout(self._box_appearance)
        self._lbl_theme = QLabel(T("settings.appearance.theme"))
        theme_row = QHBoxLayout()
        self._theme_group = QButtonGroup(self)
        self._theme_buttons: dict[str, QRadioButton] = {}
        for code in ("dark", "light", "system"):
            rb = QRadioButton(T(f"settings.theme.{code}"))
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            self._theme_group.addButton(rb)
            self._theme_buttons[code] = rb
            theme_row.addWidget(rb)
        theme_row.addStretch(1)
        appear_form.addRow(self._lbl_theme, theme_row)
        inner.addWidget(self._box_appearance)

        # ── Avanzate (cache documento) ──────────────────────────────────
        self._box_advanced = QGroupBox(T("settings.group.advanced"))
        adv_form = QFormLayout(self._box_advanced)
        self._btn_cache_clear = QPushButton(T("settings.advanced.cache.clear"))
        self._btn_cache_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cache_clear.clicked.connect(self._on_cache_clear)
        adv_form.addRow(self._btn_cache_clear)
        self._btn_cache_info = QPushButton(T("settings.advanced.cache.info"))
        self._btn_cache_info.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cache_info.clicked.connect(self._on_cache_info)
        adv_form.addRow(self._btn_cache_info)
        inner.addWidget(self._box_advanced)

        # ── Notifiche ───────────────────────────────────────────────────
        self._box_notify = QGroupBox(T("settings.group.notifications"))
        notify_form = QFormLayout(self._box_notify)
        self._notify_check = QCheckBox(T("settings.notify.batch"))
        notify_form.addRow(self._notify_check)
        inner.addWidget(self._box_notify)

        # ── Comportamento ───────────────────────────────────────────────
        self._box_beh = QGroupBox(T("settings.group.behavior"))
        beh_form = QFormLayout(self._box_beh)
        self._resume_check = QCheckBox(T("settings.behavior.resume"))
        beh_form.addRow(self._resume_check)
        self._tab_check = QCheckBox(T("settings.behavior.tab"))
        beh_form.addRow(self._tab_check)
        inner.addWidget(self._box_beh)

        inner.addStretch(1)
        self._scroll.setWidget(content)
        root.addWidget(self._scroll, 1)

        # ── Pulsanti ────────────────────────────────────────────────────
        btns = QHBoxLayout()
        btns.addStretch(1)
        self._btn_ok = QPushButton(T("settings.ok"))
        self._btn_ok.clicked.connect(self.accept)
        self._btn_cancel = QPushButton(T("settings.cancel"))
        self._btn_cancel.clicked.connect(self.reject)
        for b in (self._btn_ok, self._btn_cancel):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            btns.addWidget(b)
        root.addLayout(btns)

        self._load_values()

        # Anteprima lingua dal vivo: ri-traduce SOLO le label del dialogo.
        self._ui_combo.currentIndexChanged.connect(self._on_ui_preview)

    def _on_clear_edits(self):
        """Ask confirmation, then wipe the current document's saved edits."""
        ret = QMessageBox.question(
            self,
            T("settings.title"),
            T("settings.edits.clear_confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        parent = self.parent()
        if parent is not None and hasattr(parent, "clear_saved_edits"):
            parent.clear_saved_edits()
        QMessageBox.information(self, T("settings.title"), T("settings.edits.clear_done"))

    def _on_cache_clear(self):
        """Delegate to the main window's document-cache reset."""
        parent = self.parent()
        if parent is not None and hasattr(parent, "_clear_document_cache"):
            parent._clear_document_cache()

    def _on_cache_info(self):
        """Delegate to the main window's cache diagnostics panel."""
        parent = self.parent()
        if parent is not None and hasattr(parent, "_show_cache_info"):
            parent._show_cache_info()

    def _theme_selected(self) -> str:
        """Return the selected theme mode (dark|light|system)."""
        for code, rb in self._theme_buttons.items():
            if rb.isChecked():
                return code
        return "dark"

    def _load_values(self):
        """Populate the widgets from the current config (bozza)."""
        cfg = self._cfg
        idx = self._ui_combo.findData(cfg.get("lang", "it"))
        self._ui_combo.setCurrentIndex(max(0, idx))
        idx = self._src_combo.findData(cfg.get("src_lang", "auto"))
        self._src_combo.setCurrentIndex(max(0, idx))
        idx = self._dst_combo.findData(cfg.get("dst_lang", "it"))
        self._dst_combo.setCurrentIndex(max(0, idx))
        idx = self._engine_combo.findData(cfg.get("engine", "google"))
        self._engine_combo.setCurrentIndex(max(0, idx))
        self._font_spin.setValue(int(cfg.get("font_size", 12)))
        self._md_check.setChecked(bool(cfg.get("render_md", True)))
        self._header_check.setChecked(bool(cfg.get("show_header", True)))
        self._save_edits_check.setChecked(bool(cfg.get("save_edits", True)))
        self._zoom_spin.setValue(float(cfg.get("zoom", 3.0)))
        self._resume_check.setChecked(bool(cfg.get("resume_last_page", True)))
        self._tab_check.setChecked(bool(cfg.get("remember_tab", True)))
        theme_mode = theme.normalize_mode(str(cfg.get("theme", "dark")))
        self._theme_buttons[theme_mode].setChecked(True)
        self._notify_check.setChecked(bool(cfg.get("notify_batch", True)))

    def _on_ui_preview(self, index: int):
        """Live preview: re-label the dialog when the UI language changes."""
        code = self._ui_combo.itemData(index)
        if code and code != get_language():
            set_language(code)
            self.retranslate()

    def retranslate(self):
        """Re-apply the dialog's own labels (names in combos are endonyms)."""
        self.setWindowTitle(T("settings.title"))
        self._box_lang.setTitle(T("settings.group.lang"))
        self._box_translation.setTitle(T("settings.group.translation"))
        self._box_text.setTitle(T("settings.group.text"))
        self._box_view.setTitle(T("settings.group.view"))
        self._box_beh.setTitle(T("settings.group.behavior"))
        self._box_appearance.setTitle(T("settings.group.appearance"))
        self._box_advanced.setTitle(T("settings.group.advanced"))
        self._box_notify.setTitle(T("settings.group.notifications"))
        self._lbl_ui.setText(T("settings.lang.ui"))
        self._lbl_src.setText(T("settings.lang.source"))
        self._lbl_dst.setText(T("settings.lang.target"))
        self._lbl_engine.setText(T("settings.translation.engine"))
        # Le etichette dei motori passano da T(): ricostruisci la combo
        # preservando la selezione corrente.
        cur = self._engine_combo.currentData()
        self._engine_combo.clear()
        for code in TRANSLATION_ENGINES:
            self._engine_combo.addItem(T(f"engine.option.{code}"), code)
        if cur is not None:
            idx = self._engine_combo.findData(cur)
            self._engine_combo.setCurrentIndex(max(0, idx))
        self._lbl_font.setText(T("settings.text.font"))
        self._md_check.setText(T("settings.text.md"))
        self._header_check.setText(T("settings.text.header"))
        self._save_edits_check.setText(T("settings.edits.save"))
        self._btn_clear_edits.setText(T("settings.edits.clear"))
        self._lbl_zoom.setText(T("settings.view.render_quality"))
        self._lbl_theme.setText(T("settings.appearance.theme"))
        for code, rb in self._theme_buttons.items():
            rb.setText(T(f"settings.theme.{code}"))
        self._btn_cache_clear.setText(T("settings.advanced.cache.clear"))
        self._btn_cache_info.setText(T("settings.advanced.cache.info"))
        self._notify_check.setText(T("settings.notify.batch"))
        self._resume_check.setText(T("settings.behavior.resume"))
        self._tab_check.setText(T("settings.behavior.tab"))
        self._btn_ok.setText(T("settings.ok"))
        self._btn_cancel.setText(T("settings.cancel"))

    def reject(self):
        """Cancel: restore the original UI language (main window untouched)."""
        set_language(self._orig_ui_lang)
        super().reject()

    def values(self) -> dict:
        """Return the dialog choices (applied by MainWindow on OK)."""
        return {
            "lang": self._ui_combo.currentData() or get_language(),
            "src_lang": self._src_combo.currentData() or "auto",
            "dst_lang": self._dst_combo.currentData() or "it",
            "engine": self._engine_combo.currentData() or "google",
            "zoom": float(self._zoom_spin.value()),
            "font_size": int(self._font_spin.value()),
            "render_md": bool(self._md_check.isChecked()),
            "show_header": bool(self._header_check.isChecked()),
            "save_edits": bool(self._save_edits_check.isChecked()),
            "resume_last_page": bool(self._resume_check.isChecked()),
            "remember_tab": bool(self._tab_check.isChecked()),
            "theme": self._theme_selected(),
            "notify_batch": bool(self._notify_check.isChecked()),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  batch export (wizard + thread + progress dialog)
# ═══════════════════════════════════════════════════════════════════════════════


def _batch_extract_page(path: str, page: int, ocr_lang: str, figures_dir):
    """Extract one page exactly like ``ExtractThread`` (IR first, fallback).

    Returns ``(text, label, raw, engine)`` so the caller can both export and
    warm the app's caches with the same result the on-demand path would give.
    """
    if _pipeline_mode() == "ir":
        try:
            sel_text, sel_engine, ir_raw = _select_page_output(
                path, page, figures_dir)
            if sel_text and _norm_text(sel_text).strip():
                return sel_text, "auto", ir_raw, sel_engine
        except Exception:  # noqa: BLE001 — fallback alla pipeline current
            pass
    raw = _extract_pymupdf4llm(path, page, ocr_language=ocr_lang)
    text, label = _apply_engine_standalone(
        path, page, raw, figures_dir=figures_dir)
    return text, label, raw, "current"


class CaptureInterpretThread(QThread):
    """Interpreta una regione catturata (nativo/tabelle/OCR) in background.

    Apre un **proprio** documento PyMuPDF per non contendere ``_mupdf_doc``
    usato dal thread GUI. Emette il testo (o vuoto) e il metodo usato.
    """

    result_ready = pyqtSignal(int, str, str, str)  # page, uri, text, method

    def __init__(self, path: str, page_num: int, clip, uri: str):
        super().__init__()
        self._path = path
        self._page_num = page_num
        self._clip = tuple(clip)
        self._uri = uri

    def run(self):
        text, method = "", "none"
        doc = None
        try:
            doc = pymupdf.open(self._path)
            page = doc[self._page_num]
            text, method = _interpret_region(page, pymupdf.Rect(self._clip))
        except Exception:  # noqa: BLE001 — l'immagine catturata resta valida
            text, method = "", "none"
        finally:
            if doc is not None:
                try:
                    doc.close()
                except Exception:  # noqa: BLE001
                    pass
        self.result_ready.emit(self._page_num, self._uri, text, method)


class BatchExportThread(QThread):
    """Background worker: extract + translate the selected pages for export.

    Pure with respect to the GUI: reads cache **snapshots** passed at start and
    emits per-page results; the main window merges them into its caches and
    writes the output. Reuses the OCR/IR locks already held by the engine, so
    determinism (R12) is preserved.
    """

    page_started = pyqtSignal(int, str)          # page, phase
    page_done = pyqtSignal(int)                  # page
    page_error = pyqtSignal(int, str)            # page, reason
    page_extracted = pyqtSignal(int, str, str, str, str)  # page,text,label,raw,engine
    page_translated = pyqtSignal(int, str)       # page, translated body
    progress = pyqtSignal(int, int)              # done, total
    completed = pyqtSignal()                     # always at the end

    def __init__(
        self,
        path: str,
        pages: list[int],
        content: str,
        translate_missing: bool,
        engine: str,
        src: str,
        target: str,
        ocr_lang: str,
        figures_dir,
        cached_original: dict,
        cached_translation: dict,
    ):
        super().__init__()
        self._path = path
        self._pages = list(pages)
        self._content = content
        self._translate_missing = bool(translate_missing)
        self._engine = engine
        self._src = src
        self._target = target
        self._ocr_lang = ocr_lang
        self._figures_dir = figures_dir
        self._cached_original = dict(cached_original)
        self._cached_translation = dict(cached_translation)
        self.results: dict[int, dict] = {}
        self.errors: dict[int, str] = {}
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def _ensure_original(self, page: int, entry: dict) -> str:
        if "original" in entry:
            return entry["original"]
        cached = self._cached_original.get(page)
        if cached is not None:
            return cached[0]
        self.page_started.emit(page, "extract")
        text, label, raw, eng = _batch_extract_page(
            self._path, page, self._ocr_lang, self._figures_dir)
        entry["original"] = text
        self.page_extracted.emit(page, text, label, raw, eng)
        return text

    def run(self):
        total = len(self._pages)
        for i, page in enumerate(self._pages):
            if self._cancel:
                break
            entry: dict = {}
            try:
                if self._content in ("original", "both"):
                    cached = self._cached_original.get(page)
                    if cached is not None:
                        entry["original"] = cached[0]
                    else:
                        self.page_started.emit(page, "extract")
                        text, label, raw, eng = _batch_extract_page(
                            self._path, page, self._ocr_lang, self._figures_dir)
                        entry["original"] = text
                        self.page_extracted.emit(page, text, label, raw, eng)
                if self._content in ("translated", "both"):
                    cached_t = self._cached_translation.get(page)
                    if cached_t is not None:
                        entry["translated"] = cached_t
                    elif not self._translate_missing:
                        raise RuntimeError(T("export.not_ready"))
                    else:
                        body = _strip_header(self._ensure_original(page, entry))
                        self.page_started.emit(page, "translate")
                        translated = translate_text(
                            body, source=self._src, target=self._target,
                            engine=self._engine)
                        entry["translated"] = translated
                        self.page_translated.emit(page, translated)
                self.results[page] = entry
            except Exception as exc:  # noqa: BLE001 — una pagina non ferma il batch
                self.errors[page] = str(exc)
                self.page_error.emit(page, str(exc))
            self.page_done.emit(page)
            self.progress.emit(i + 1, total)
        self.completed.emit()


class ExportWizardDialog(QDialog):
    """Five-step wizard for the batch export (cloner-style, reader-lite domain).

    Steps: ① pages ② content ③ languages/engine ④ output ⑤ summary.
    The chosen values are exposed through the getters and read by
    ``MainWindow._on_export_batch``.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mw = parent
        self._page_count = int(getattr(parent, "_page_count", 0) or 0)
        self._current_page = int(getattr(parent, "_current_page", 0) or 0)
        path = getattr(parent, "_pdf_path", None)
        self._name = path.name if path is not None else ""
        self._stem = path.stem if path is not None else "documento"

        self.setWindowTitle(T("export.wizard.title"))
        self.setMinimumSize(760, 560)
        self.setStyleSheet(theme.wizard_qss())

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── header ─────────────────────────────────────────────────────
        header = QWidget()
        header.setObjectName("wizHeader")
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 12, 12, 12)
        self._title = QLabel(T("export.wizard.title"))
        self._title.setObjectName("wizTitle")
        h.addWidget(self._title)
        h.addStretch(1)
        btn_x = QPushButton("✕")
        btn_x.setObjectName("wizX")
        btn_x.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_x.clicked.connect(self.reject)
        h.addWidget(btn_x)
        root.addWidget(header)

        # ── steps bar ──────────────────────────────────────────────────
        steps_bar = QWidget()
        steps_bar.setObjectName("wizSteps")
        sb = QHBoxLayout(steps_bar)
        sb.setContentsMargins(16, 8, 16, 8)
        sb.setSpacing(6)
        self._steps: list[tuple[QWidget, QLabel, QLabel]] = []
        self._step_names = [
            T("export.wizard.step.pages"),
            T("export.wizard.step.content"),
            T("export.wizard.step.langs"),
            T("export.wizard.step.output"),
            T("export.wizard.step.summary"),
        ]
        for i, name in enumerate(self._step_names):
            if i:
                sep = QFrame()
                sep.setObjectName("wizSep")
                sep.setFixedWidth(24)
                sb.addWidget(sep)
            w = QWidget()
            w.setObjectName("wizStep")
            wl = QHBoxLayout(w)
            wl.setContentsMargins(0, 0, 0, 0)
            wl.setSpacing(6)
            num = QLabel(str(i + 1))
            num.setObjectName("wizStepNum")
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            nm = QLabel(name)
            nm.setObjectName("wizStepName")
            wl.addWidget(num)
            wl.addWidget(nm)
            sb.addWidget(w)
            self._steps.append((w, num, nm))
        sb.addStretch(1)
        root.addWidget(steps_bar)

        # ── body (stacked steps, scrollable) ───────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setObjectName("wizScroll")
        self._scroll.setWidgetResizable(True)
        self._stack = QStackedWidget()
        self._stack.setObjectName("wizPane")
        self._scroll.setWidget(self._stack)
        root.addWidget(self._scroll, 1)

        self._build_step_pages()

        # ── footer ─────────────────────────────────────────────────────
        footer = QWidget()
        footer.setObjectName("wizFooter")
        f = QHBoxLayout(footer)
        f.setContentsMargins(16, 10, 16, 10)
        self._btn_back = QPushButton(T("export.wizard.back"))
        self._btn_back.setObjectName("wizBtn")
        self._btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_back.clicked.connect(self._go_back)
        f.addWidget(self._btn_back)
        f.addStretch(1)
        self._btn_cancel = QPushButton(T("settings.cancel"))
        self._btn_cancel.setObjectName("wizGhost")
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self.reject)
        f.addWidget(self._btn_cancel)
        self._btn_next = QPushButton(T("export.wizard.next"))
        self._btn_next.setObjectName("wizPrimary")
        self._btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_next.clicked.connect(self._go_next)
        f.addWidget(self._btn_next)
        root.addWidget(footer)

        self._step = 0
        self._sync()

    # ── costruzione delle pagine ───────────────────────────────────────
    def _h3(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("wizH3")
        return lbl

    def _hint(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("wizHint")
        lbl.setWordWrap(True)
        return lbl

    def _page_wrap(self, title: str, hint: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        w.setObjectName("wizViewport")
        v = QVBoxLayout(w)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(10)
        v.addWidget(self._h3(title))
        v.addWidget(self._hint(hint))
        return w, v

    def _build_step_pages(self) -> None:
        # ① Pagine
        w0, v0 = self._page_wrap(
            T("export.wizard.pages.title"), T("export.wizard.pages.hint"))
        self._mode_group = QButtonGroup(self)
        self._mode_current = QRadioButton(T("export.mode.current"))
        self._mode_range = QRadioButton(T("export.mode.range"))
        self._mode_free = QRadioButton(T("export.mode.free"))
        for rb in (self._mode_current, self._mode_range, self._mode_free):
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            self._mode_group.addButton(rb)
            v0.addWidget(rb)
        range_row = QHBoxLayout()
        range_row.addSpacing(24)
        range_row.addWidget(QLabel(T("export.range.from.short")))
        self._range_from = QSpinBox()
        self._range_from.setRange(1, max(1, self._page_count))
        self._range_to = QSpinBox()
        self._range_to.setRange(1, max(1, self._page_count))
        range_row.addWidget(self._range_from)
        range_row.addWidget(QLabel(T("export.range.to.short")))
        range_row.addWidget(self._range_to)
        range_row.addStretch(1)
        v0.addLayout(range_row)
        free_row = QHBoxLayout()
        free_row.addSpacing(24)
        self._free_edit = QLineEdit()
        self._free_edit.setPlaceholderText(T("export.free.placeholder"))
        free_row.addWidget(self._free_edit)
        v0.addLayout(free_row)
        v0.addWidget(self._hint(T("export.free.hint")))
        self._free_err = QLabel("")
        self._free_err.setObjectName("wizError")
        v0.addWidget(self._free_err)
        self._preview_label = QLabel(T("export.wizard.preview.none"))
        self._preview_label.setObjectName("wizEst")
        self._preview_label.setWordWrap(True)
        v0.addWidget(self._preview_label)
        v0.addStretch(1)
        self._stack.addWidget(w0)
        self._mode_current.setChecked(True)
        for rb in (self._mode_current, self._mode_range, self._mode_free):
            rb.toggled.connect(self._on_pages_changed)
        self._range_from.valueChanged.connect(self._on_pages_changed)
        self._range_to.valueChanged.connect(self._on_pages_changed)
        self._free_edit.textChanged.connect(self._on_pages_changed)

        # ② Contenuto
        w1, v1 = self._page_wrap(
            T("export.wizard.content.title"), T("export.wizard.content.hint"))
        self._content_group = QButtonGroup(self)
        self._content_original = QRadioButton(T("export.mode.original"))
        self._content_translated = QRadioButton(T("export.mode.translated"))
        self._content_both = QRadioButton(T("export.mode.both"))
        for rb in (self._content_original, self._content_translated, self._content_both):
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            self._content_group.addButton(rb)
            v1.addWidget(rb)
        self._images_check = QCheckBox(T("export.include_images"))
        self._images_check.setChecked(True)
        v1.addWidget(self._images_check)
        v1.addStretch(1)
        self._stack.addWidget(w1)
        self._content_translated.setChecked(True)

        # ③ Lingue e motore
        w2, v2 = self._page_wrap(
            T("export.wizard.langs.title"), T("export.wizard.langs.hint"))
        form = QFormLayout()
        self._src_combo = QComboBox()
        for code, (flag, name) in TRANSLATION_LANGUAGES.items():
            self._src_combo.addItem(f"{flag} {name}", code)
        self._dst_combo = QComboBox()
        for code, (flag, name) in TRANSLATION_LANGUAGES.items():
            if code == "auto":
                continue
            self._dst_combo.addItem(f"{flag} {name}", code)
        form.addRow(T("settings.lang.source"), self._src_combo)
        form.addRow(T("settings.lang.target"), self._dst_combo)
        v2.addLayout(form)
        eng_row = QHBoxLayout()
        self._engine_group = QButtonGroup(self)
        self._engine_buttons: dict[str, QRadioButton] = {}
        for code in TRANSLATION_ENGINES:
            rb = QRadioButton(T(f"engine.option.{code}"))
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            self._engine_group.addButton(rb)
            self._engine_buttons[code] = rb
            eng_row.addWidget(rb)
        eng_row.addStretch(1)
        v2.addLayout(eng_row)
        self._missing_check = QCheckBox(T("export.translate_missing"))
        self._missing_check.setChecked(True)
        v2.addWidget(self._missing_check)
        v2.addStretch(1)
        self._stack.addWidget(w2)

        # ④ Output
        w3, v3 = self._page_wrap(
            T("export.wizard.output.title"), T("export.wizard.output.hint"))
        self._format_group = QButtonGroup(self)
        self._fmt_merged = QRadioButton(T("export.format.merged"))
        self._fmt_zip = QRadioButton(T("export.format.zip"))
        self._fmt_folder = QRadioButton(T("export.format.folder"))
        for rb in (self._fmt_merged, self._fmt_zip, self._fmt_folder):
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            self._format_group.addButton(rb)
            v3.addWidget(rb)
        out_form = QFormLayout()
        folder_row = QHBoxLayout()
        self._folder_edit = QLineEdit(self._default_folder())
        folder_row.addWidget(self._folder_edit, 1)
        btn_browse = QPushButton(T("export.wizard.output.browse"))
        btn_browse.setObjectName("wizBtn")
        btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_browse.clicked.connect(self._on_browse)
        folder_row.addWidget(btn_browse)
        out_form.addRow(T("export.wizard.output.folder"), folder_row)
        self._name_edit = QLineEdit(self._default_name())
        out_form.addRow(T("export.wizard.output.filename"), self._name_edit)
        v3.addLayout(out_form)
        v3.addStretch(1)
        self._stack.addWidget(w3)
        self._fmt_merged.setChecked(True)

        # ⑤ Riepilogo
        w4, v4 = self._page_wrap(
            T("export.wizard.summary.title"), T("export.wizard.output.hint"))
        self._sum_label = QLabel("")
        self._sum_label.setObjectName("wizSum")
        self._sum_label.setWordWrap(True)
        v4.addWidget(self._sum_label)
        v4.addStretch(1)
        self._stack.addWidget(w4)

        self._sync_page_defaults()

    def _default_folder(self) -> str:
        try:
            docs = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation)
            return docs or str(Path.home())
        except Exception:  # noqa: BLE001
            return str(Path.home())

    def _default_name(self) -> str:
        return f"{self._stem}_estratto"

    def _sync_page_defaults(self) -> None:
        cur = min(max(self._current_page + 1, 1), max(1, self._page_count))
        self._range_from.setValue(cur)
        self._range_to.setValue(min(cur + 9, max(1, self._page_count)))
        self._src_combo.setCurrentIndex(
            max(0, self._src_combo.findData(get_source_lang())))
        self._dst_combo.setCurrentIndex(
            max(0, self._dst_combo.findData(get_target_lang())))
        engine = get_translation_engine()
        if engine in self._engine_buttons:
            self._engine_buttons[engine].setChecked(True)
        elif self._engine_buttons:
            next(iter(self._engine_buttons.values())).setChecked(True)

    # ── navigazione ────────────────────────────────────────────────────
    def _go_back(self) -> None:
        if self._step > 0:
            self._step -= 1
            self._sync()

    def _go_next(self) -> None:
        if self._step == 0 and not self._validate_pages():
            return
        if self._step == 4:
            self.accept()
            return
        self._step += 1
        self._sync()

    def _sync(self) -> None:
        self._stack.setCurrentIndex(self._step)
        for i, (w, _num, _nm) in enumerate(self._steps):
            state = "done" if i < self._step else ("on" if i == self._step else "off")
            w.setProperty("state", state)
            w.style().unpolish(w)
            w.style().polish(w)
        self._btn_back.setEnabled(self._step > 0)
        last = self._step == 4
        self._btn_next.setText(
            T("export.wizard.start") if last else T("export.wizard.next"))
        if self._step == 0:
            self._on_pages_changed()
        if self._step == 4:
            self._update_summary()

    # ── step ①: pagine ─────────────────────────────────────────────────
    def _selected_pages(self):
        """Return (pages, error_message). ``pages`` is None on error."""
        if self._page_count <= 0:
            return None, T("export.free.err.no_pages")
        if self._mode_current.isChecked():
            return [min(max(self._current_page, 0), self._page_count - 1)], ""
        if self._mode_range.isChecked():
            lo = min(self._range_from.value(), self._range_to.value())
            hi = max(self._range_from.value(), self._range_to.value())
            return list(range(lo - 1, hi)), ""
        try:
            pages = parse_pages(self._free_edit.text(), self._page_count)
        except PageSpecError as exc:
            key = f"export.free.err.{exc.code}"
            msg = T(key, **exc.params) if T(key) != key else str(exc)
            return None, msg
        if not pages:
            return None, T("export.free.err.none")
        return pages, ""

    def _validate_pages(self) -> bool:
        pages, err = self._selected_pages()
        self._free_err.setText("" if pages is not None else err)
        return pages is not None

    def _on_pages_changed(self) -> None:
        pages, err = self._selected_pages()
        if pages is None:
            self._preview_label.setText(err)
            self._free_err.setText(err)
            return
        self._free_err.setText("")
        label = format_pages_label(pages)
        self._preview_label.setText(
            T("export.wizard.preview.count", n=len(pages), label=label))
        self._update_summary()

    # ── step ④: output ─────────────────────────────────────────────────
    def _on_browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, T("export.wizard.output.folder"), self._folder_edit.text())
        if folder:
            self._folder_edit.setText(folder)

    # ── getters ────────────────────────────────────────────────────────
    def chosen_pages(self) -> list[int]:
        pages, _ = self._selected_pages()
        return pages or []

    def chosen_content(self) -> str:
        if self._content_original.isChecked():
            return "original"
        if self._content_both.isChecked():
            return "both"
        return "translated"

    def include_images(self) -> bool:
        return bool(self._images_check.isChecked())

    def translate_missing(self) -> bool:
        return bool(self._missing_check.isChecked())

    def chosen_format(self) -> str:
        if self._fmt_zip.isChecked():
            return "zip"
        if self._fmt_folder.isChecked():
            return "folder"
        return "merged"

    def chosen_folder(self) -> Path:
        return Path(self._folder_edit.text().strip() or self._default_folder())

    def chosen_name(self) -> str:
        return self._name_edit.text().strip() or self._default_name()

    def chosen_path(self) -> Path:
        name = self.chosen_name()
        folder = self.chosen_folder()
        if self.chosen_format() == "zip":
            if not name.lower().endswith(".zip"):
                name += ".zip"
            return folder / name
        if not name.lower().endswith(".md"):
            name += ".md"
        return folder / name

    def _update_summary(self) -> None:
        pages, _ = self._selected_pages()
        pages = pages or []
        content = self.chosen_content()
        content_label = {
            "original": T("export.mode.original"),
            "translated": T("export.mode.translated"),
            "both": T("export.mode.both"),
        }.get(content, content)
        if self.include_images():
            content_label += " · " + T("export.include_images")
        src = self._src_combo.currentData() or "auto"
        dst = self._dst_combo.currentData() or "it"
        engine = self._current_engine()
        if self.chosen_format() == "folder":
            out = str(self.chosen_folder() / self.chosen_name())
        else:
            out = str(self.chosen_path())
        cached = self._cached_count(pages, content, engine, dst)
        missing = max(0, len(pages) - cached)
        rows = [
            (T("export.sum.file"), self._name or "—"),
            (T("export.sum.pages"), f"{format_pages_label(pages)}  ({len(pages)})"),
            (T("export.sum.content"), content_label),
            (T("export.sum.langs"),
             f"{flag_endonym(src)} → {flag_endonym(dst)} · "
             f"{T(f'engine.option.{engine}')}"),
            (T("export.sum.missing"), str(missing)),
            (T("export.sum.output"), out),
        ]
        self._sum_label.setText(
            "\n".join(f"{k}:  {v}" for k, v in rows))

    def _current_engine(self) -> str:
        for code, rb in self._engine_buttons.items():
            if rb.isChecked():
                return code
        return get_translation_engine()

    def _cached_count(self, pages, content, engine, target) -> int:
        if self._mw is None or not hasattr(self._mw, "_batch_cached_count"):
            return 0
        return int(self._mw._batch_cached_count(pages, content, engine, target))


class ExportProgressDialog(QDialog):
    """Non-modal progress dialog for the batch export (liquid preview + dots)."""

    cancel_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mw = parent
        self.setWindowTitle(T("export.progress.title"))
        self.setMinimumWidth(720)
        self.setStyleSheet(theme.settings_qss())
        self.setModal(False)

        root = QVBoxLayout(self)
        root.setSpacing(10)

        self._info = QLabel("")
        self._info.setWordWrap(True)
        root.addWidget(self._info)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self._pages_lbl = QLabel("")
        self._pages_lbl.setWordWrap(True)
        left.addWidget(self._pages_lbl)
        self._activity = QLabel("")
        self._activity.setWordWrap(True)
        left.addWidget(self._activity)
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        left.addWidget(self._bar)
        self._stats = QLabel("")
        self._stats.setWordWrap(True)
        left.addWidget(self._stats)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFixedHeight(120)
        left.addWidget(self._log)
        body.addLayout(left, 1)

        # Anteprima "liquida" della pagina in lavorazione.
        self._preview_box = QWidget()
        self._preview_box.setFixedSize(220, 290)
        self._preview = LiquidOverlay(self._preview_box)
        self._preview.setGeometry(self._preview_box.rect())
        self._preview.cancel_requested.connect(self._on_cancel)
        body.addWidget(self._preview_box)
        root.addLayout(body)

        foot = QHBoxLayout()
        self._btn_open = QPushButton(T("export.progress.open_folder"))
        self._btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open.clicked.connect(self._on_open_folder)
        self._btn_open.setEnabled(False)
        foot.addWidget(self._btn_open)
        self._btn_download = QPushButton(T("export.progress.save_download"))
        self._btn_download.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_download.clicked.connect(self._on_copy_download)
        self._btn_download.setEnabled(False)
        foot.addWidget(self._btn_download)
        foot.addStretch(1)
        self._btn_cancel = QPushButton(T("settings.cancel"))
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._on_cancel)
        foot.addWidget(self._btn_cancel)
        self._btn_close = QPushButton(T("export.progress.close"))
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.clicked.connect(self.close)
        self._btn_close.setEnabled(False)
        foot.addWidget(self._btn_close)
        root.addLayout(foot)

        self._dots = 0
        self._activity_base = ""
        self._dots_timer = QTimer(self)
        self._dots_timer.setInterval(500)
        self._dots_timer.timeout.connect(self._tick_dots)
        self._result_path: Path | None = None
        self._total = 0
        self._done = 0
        self._failed = 0
        self._cached = 0
        self._start = time.time()

    # ── API ─────────────────────────────────────────────────────────────
    def begin(self, total: int, cached: int, info: str, pages_label: str) -> None:
        self._total = int(total)
        self._cached = int(cached)
        self._done = 0
        self._failed = 0
        self._start = time.time()
        self._info.setText(info)
        self._pages_lbl.setText(pages_label)
        self._bar.setRange(0, max(1, self._total))
        self._bar.setValue(0)
        self._stats.setText("")
        self._log.clear()
        self._dots_timer.start()
        self._btn_cancel.setEnabled(True)
        self._btn_close.setEnabled(False)
        self._btn_open.setEnabled(False)
        self._btn_download.setEnabled(False)
        self.show()

    def set_preview(self, pixmap, caption: str) -> None:
        if pixmap is None:
            self._preview.stop()
            return
        self._preview.setGeometry(self._preview_box.rect())
        self._preview.start(caption, pixmap=pixmap, cancelable=True)

    def on_page_started(self, page: int, phase: str) -> None:
        key = ("export.progress.activity_extracting"
               if phase == "extract"
               else "export.progress.activity_translating")
        self._activity_base = T(key, page=page + 1)
        self._bar.setRange(0, 0)  # busy durante la pagina
        if self._mw is not None and hasattr(self._mw, "_render_pymupdf"):
            pix = self._mw._render_pymupdf(page)
            self.set_preview(pix, T("export.wizard.preview.caption", n=page + 1))

    def on_page_done(self, page: int) -> None:
        self._done += 1
        self._bar.setRange(0, max(1, self._total))
        self._bar.setValue(self._done)
        self._log.append(T("export.progress.page_ok", page=page + 1))
        self._update_stats()

    def on_page_error(self, page: int, reason: str) -> None:
        self._failed += 1
        self._log.append(T("export.progress.page_fail", page=page + 1, reason=reason))
        self._update_stats()

    def on_finished(self, path: Path | None, cancelled: bool) -> None:
        self._dots_timer.stop()
        self._preview.stop()
        self._bar.setRange(0, max(1, self._total))
        self._bar.setValue(self._done)
        self._result_path = path
        self._btn_cancel.setEnabled(False)
        self._btn_close.setEnabled(True)
        self._btn_open.setEnabled(path is not None)
        self._btn_download.setEnabled(path is not None)
        if cancelled:
            self._activity.setText(T("export.cancelled"))
        else:
            self._activity.setText(T("export.progress.completed_title"))
        elapsed = f"{time.time() - self._start:.1f}s"
        self._stats.setText(
            T("export.progress.completed_summary",
              count=self._done, failed=self._failed, elapsed=elapsed))
        if path is not None:
            self._log.append(T("export.progress.saved_path", path=str(path)))

    # ── interni ─────────────────────────────────────────────────────────
    def retranslate(self) -> None:
        """Re-apply the dialog's static strings after a language switch."""
        self.setWindowTitle(T("export.progress.title"))
        self._btn_open.setText(T("export.progress.open_folder"))
        self._btn_download.setText(T("export.progress.save_download"))
        self._btn_cancel.setText(T("settings.cancel"))
        self._btn_close.setText(T("export.progress.close"))
        self.setStyleSheet(theme.settings_qss())

    def _tick_dots(self) -> None:
        self._dots = (self._dots + 1) % 4
        self._activity.setText(self._activity_base + "." * self._dots)

    def _update_stats(self) -> None:
        elapsed = max(0.0, time.time() - self._start)
        rate = self._done / elapsed if elapsed > 0 and self._done else 0.0
        remaining = self._total - self._done
        eta = f"{remaining / rate:.0f}s" if rate > 0 else "—"
        self._stats.setText(
            T("export.progress.stats", done=self._done, cached=self._cached,
              failed=self._failed, eta=eta))

    def _on_cancel(self) -> None:
        self._btn_cancel.setEnabled(False)
        self._activity_base = T("export.progress.cancelling")
        self.cancel_requested.emit()

    def _on_open_folder(self) -> None:
        if self._result_path is None:
            return
        folder = (self._result_path if self._result_path.is_dir()
                  else self._result_path.parent)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _on_copy_download(self) -> None:
        if self._result_path is None or not self._result_path.exists():
            return
        try:
            downloads = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DownloadLocation)
            dest_dir = Path(downloads or str(Path.home() / "Downloads"))
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / self._result_path.name
            shutil.copyfile(self._result_path, dest)
            self._log.append(
                T("export.progress.saved_path", path=str(dest)))
        except Exception:  # noqa: BLE001
            pass


class PdfReaderWindow(QDialog):
    """Non-modal, read-only PDF reader with its own PyMuPDF document.

    Basic commands only (no text search): navigation, zoom, fit-width/page,
    rotate, open in the system viewer. It uses a separate ``pymupdf.open`` so
    it never conflicts with the main window's ``_mupdf_doc``.
    """

    def __init__(self, path, start_page: int = 0, parent=None):
        super().__init__(parent)
        self._path = Path(path)
        self.setModal(False)
        self.setWindowTitle(T("reader.title", name=self._path.name))
        self.resize(1000, 820)
        self.setStyleSheet(theme.settings_qss())
        self._doc = None
        try:
            self._doc = pymupdf.open(str(self._path))
        except Exception:
            self._doc = None
        self._page = 0
        self._zoom = 1.0
        self._fit_mode = "page"
        self._rotation = 0
        self._pix_item = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QToolBar()
        bar.setMovable(False)
        root.addWidget(bar)

        self.btn_prev = QPushButton(T("reader.prev"))
        self.btn_prev.clicked.connect(self._prev)
        bar.addWidget(self.btn_prev)
        self.spin = QSpinBox()
        self.spin.setMinimum(1)
        self.spin.setKeyboardTracking(False)
        self.spin.setToolTip(T("reader.goto"))
        self.spin.valueChanged.connect(self._on_spin)
        bar.addWidget(self.spin)
        self.lbl_of = QLabel("")
        bar.addWidget(self.lbl_of)
        self.btn_next = QPushButton(T("reader.next"))
        self.btn_next.clicked.connect(self._next)
        bar.addWidget(self.btn_next)
        bar.addSeparator()
        self.btn_zoom_out = QPushButton(T("reader.zoom_out"))
        self.btn_zoom_out.setToolTip(T("reader.zoom_out.tip"))
        self.btn_zoom_out.clicked.connect(self._zoom_out)
        bar.addWidget(self.btn_zoom_out)
        self.btn_zoom_in = QPushButton(T("reader.zoom_in"))
        self.btn_zoom_in.setToolTip(T("reader.zoom_in.tip"))
        self.btn_zoom_in.clicked.connect(self._zoom_in)
        bar.addWidget(self.btn_zoom_in)
        self.btn_fit_width = QPushButton(T("reader.fit_width"))
        self.btn_fit_width.setToolTip(T("reader.fit_width.tip"))
        self.btn_fit_width.clicked.connect(lambda: self._set_fit("width"))
        bar.addWidget(self.btn_fit_width)
        self.btn_fit_page = QPushButton(T("reader.fit_page"))
        self.btn_fit_page.setToolTip(T("reader.fit_page.tip"))
        self.btn_fit_page.clicked.connect(lambda: self._set_fit("page"))
        bar.addWidget(self.btn_fit_page)
        self.btn_rotate = QPushButton(T("reader.rotate"))
        self.btn_rotate.setToolTip(T("reader.rotate.tip"))
        self.btn_rotate.clicked.connect(self._rotate)
        bar.addWidget(self.btn_rotate)
        bar.addSeparator()
        self.btn_external = QPushButton(T("reader.external"))
        self.btn_external.setToolTip(T("reader.external.tip"))
        self.btn_external.clicked.connect(self._open_external)
        bar.addWidget(self.btn_external)

        self._view = QGraphicsView()
        self._view.setFrameShape(QFrame.Shape.NoFrame)
        self._view.setBackgroundBrush(QColor(theme.color("bg")))
        self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scene = QGraphicsScene(self._view)
        self._view.setScene(self._scene)
        root.addWidget(self._view, 1)

        if self._doc is not None and len(self._doc) > 0:
            self.spin.setMaximum(len(self._doc))
            self._page = max(0, min(start_page, len(self._doc) - 1))
            self._render()
        else:
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            self.spin.setEnabled(False)

    # ── rendering ──────────────────────────────────────────────────────
    def _render(self) -> None:
        if self._doc is None:
            return
        page = self._doc[self._page]
        try:
            pix = page.get_pixmap(matrix=pymupdf.Matrix(2.0, 2.0))
        except Exception:
            return
        img = QImage(
            pix.samples, pix.width, pix.height, pix.stride,
            QImage.Format.Format_RGB888,
        )
        pixmap = QPixmap.fromImage(img)
        if self._rotation:
            pixmap = pixmap.transformed(
                QTransform().rotate(self._rotation),
                Qt.TransformationMode.SmoothTransformation,
            )
        self._scene.clear()
        self._pix_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.spin.blockSignals(True)
        self.spin.setValue(self._page + 1)
        self.spin.blockSignals(False)
        self.lbl_of.setText(T("reader.of", total=len(self._doc)))
        self._apply_fit()

    def _apply_fit(self) -> None:
        if self._pix_item is None:
            return
        r = self._pix_item.boundingRect()
        vw = max(1, self._view.viewport().width())
        vh = max(1, self._view.viewport().height())
        if self._fit_mode == "width":
            z = vw / r.width() if r.width() else 1.0
        elif self._fit_mode == "page":
            z = min(
                vw / r.width() if r.width() else 1.0,
                vh / r.height() if r.height() else 1.0,
            )
        else:
            z = self._zoom
        self._view.resetTransform()
        self._view.scale(z, z)

    # ── comandi ────────────────────────────────────────────────────────
    def _set_fit(self, mode: str) -> None:
        self._fit_mode = mode
        self._apply_fit()

    def _zoom_in(self) -> None:
        self._fit_mode = "free"
        self._zoom = min(8.0, self._zoom * 1.25)
        self._apply_fit()

    def _zoom_out(self) -> None:
        self._fit_mode = "free"
        self._zoom = max(0.25, self._zoom / 1.25)
        self._apply_fit()

    def _rotate(self) -> None:
        self._rotation = (self._rotation + 90) % 360
        self._render()

    def _prev(self) -> None:
        if self._page > 0:
            self._page -= 1
            self._render()

    def _next(self) -> None:
        if self._doc is not None and self._page < len(self._doc) - 1:
            self._page += 1
            self._render()

    def _on_spin(self, val: int) -> None:
        if self._doc is None:
            return
        self._page = max(0, min(val - 1, len(self._doc) - 1))
        self._render()

    def _open_external(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._path)))

    def resizeEvent(self, event):  # noqa: N802 — override Qt
        super().resizeEvent(event)
        if self._fit_mode in ("page", "width"):
            self._apply_fit()

    def retranslate(self) -> None:
        self.setWindowTitle(T("reader.title", name=self._path.name))
        self.btn_prev.setText(T("reader.prev"))
        self.btn_next.setText(T("reader.next"))
        self.lbl_of.setText(
            T("reader.of", total=(len(self._doc) if self._doc else 0)))
        self.btn_zoom_out.setToolTip(T("reader.zoom_out.tip"))
        self.btn_zoom_in.setToolTip(T("reader.zoom_in.tip"))
        self.btn_fit_width.setText(T("reader.fit_width"))
        self.btn_fit_width.setToolTip(T("reader.fit_width.tip"))
        self.btn_fit_page.setText(T("reader.fit_page"))
        self.btn_fit_page.setToolTip(T("reader.fit_page.tip"))
        self.btn_rotate.setText(T("reader.rotate"))
        self.btn_rotate.setToolTip(T("reader.rotate.tip"))
        self.btn_external.setText(T("reader.external"))
        self.btn_external.setToolTip(T("reader.external.tip"))
        self.setStyleSheet(theme.settings_qss())
        self._view.setBackgroundBrush(QColor(theme.color("bg")))

    def closeEvent(self, event):  # noqa: N802 — override Qt
        if self._doc is not None:
            try:
                self._doc.close()
            except Exception:
                pass
            self._doc = None
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
#  main window
# ═══════════════════════════════════════════════════════════════════════════════


class MainWindow(QMainWindow):
    # Motore di rendering (PyMuPDF) e di estrazione (PyMuPDF4LLM) fissi;
    # l'engine adattativo dei fix è sempre attivo (nessun dropdown).

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Noesis PDF Reader Lite")
        self.resize(1400, 900)
        self.setMinimumSize(800, 600)

        # Preferenze dal config (inizializzato in main() prima della UI).
        # ``_base_render_scale`` è la risoluzione di render persistita
        # (impostazione "Qualità di rendering", 0.5–4.0); ``_render_scale`` è la
        # risoluzione effettiva usata per render + mappatura zone. Lo zoom di
        # lettura vive nel reader detached, non qui.
        self._base_render_scale: float = float(get_setting("zoom", 3.0))
        self._render_scale: float = self._base_render_scale
        self._theme_mode: str = str(get_setting("theme", "dark"))
        apply_theme_mode(self._theme_mode)
        self._render_md: bool = bool(get_setting("render_md", True))
        self._show_header: bool = bool(get_setting("show_header", True))
        self._remember_tab: bool = bool(get_setting("remember_tab", True))
        self._resume_last_page: bool = bool(get_setting("resume_last_page", True))
        self._last_result: tuple[str, str, float] | None = None  # (text, label, elapsed)
        self._last_elapsed: float = 0.0
        # Lingua con cui le stringhe dei widget sono state applicate: serve a
        # capire se serve una ri-traduzione dopo l'OK delle Impostazioni (la
        # anteprima del dialogo può aver già cambiato la lingua globale).
        self._ui_lang_applied: str = get_language()
        # Persistenza dell'ultima pagina con debounce (2 s dopo l'ultimo cambio).
        self._last_page_timer = QTimer(self)
        self._last_page_timer.setSingleShot(True)
        self._last_page_timer.setInterval(2000)
        self._last_page_timer.timeout.connect(save_config)

        # State
        self._pdf_path: Path | None = None
        self._current_page: int = 0
        self._page_count: int = 0
        self._mupdf_doc = None       # pymupdf Document (render + layout + images)
        self._images_dir: Path | None = None  # dir for extracted figures
        # Gallery **per pagina** (G5): figure automatiche (data URI) + catture
        # manuali (file://), indicizzate per numero di pagina.
        self._page_images: dict[int, list[str]] = {}
        self._current_images: list[str] = []  # lista della pagina corrente
        self._excluded_zones: dict[int, list[tuple]] = {}  # page → excluded PDF rects
        self._inclusion_zones: dict[int, list[tuple]] = {}  # page → numbered inclusion rects
        # Estrazione asincrona: cache del testo grezzo per (pagina, lingua OCR)
        # + thread in background. Sui PDF scansionati l'OCR richiede secondi:
        # niente lavoro sincrono sul thread GUI, e la cache evita di ri-OCRare.
        self._extraction_cache: dict[tuple[int, str], str] = {}
        # Testo finale per (pagina, lingua OCR, chiave zone): il caso "auto"
        # (nessuna zona) è la voce comune e rende la navigazione istantanea.
        self._final_text_cache: dict[tuple[int, str, str], tuple[str, str, float]] = {}
        self._extraction_cache_file: Path | None = None
        self._doc_fingerprint: str = ""
        self._extract_thread: ExtractThread | None = None
        self._retired_extract_threads: list[ExtractThread] = []
        self._extract_generation: int = 0
        self._batch_thread: BatchExportThread | None = None
        self._batch_dialog = None
        # Cattura: modalità attiva (rubber-band) e se interpretare la regione.
        self._capture_active: bool = False
        self._capture_interpret: bool = False
        self._interpret_thread: CaptureInterpretThread | None = None
        self._retired_interpret_threads: list[CaptureInterpretThread] = []

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Toolbar
        self._build_toolbar(root_layout)

        # TOC dock (left, dockable)
        self.toc_panel = TocPanel()
        self.toc_panel.page_selected.connect(self._goto_toc_page)
        self.toc_dock = QDockWidget(T("dock.toc"), self)
        self.toc_dock.setObjectName("tocDock")
        self.toc_dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.toc_dock.setWidget(self.toc_panel)
        self.toc_dock.setMinimumWidth(220)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.toc_dock)
        self.toc_dock.visibilityChanged.connect(self.btn_toc.setChecked)

        # Splitter
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left — mini toolbar + scroll area wrapping the page view
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.pdf_view = PdfPageView()
        self.pdf_view.region_selected.connect(self._on_region_selected)
        self.pdf_view.region_excluded.connect(self._on_region_excluded)
        self.pdf_view.region_included.connect(self._on_region_included)
        self.scroll_area.setWidget(self.pdf_view)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(self._build_page_toolbar())
        left_layout.addWidget(self.scroll_area)

        # Right — text panel with translation tabs
        self.text_panel = TranslatablePanel()
        self.text_panel.image_removed.connect(self._on_image_removed)
        self.text_panel.toast.connect(self._show_toast)
        self.text_panel.copy_requested.connect(self._copy_page)
        self.text_panel.translate_page_requested.connect(self._translate_page)
        self.text_panel.reextract_page_requested.connect(self._regenerate_page)
        self.text_panel.retranslate_page_requested.connect(self._retranslate_page)
        self.text_panel.translate_next_requested.connect(self._translate_next)

        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(self.text_panel)
        self.splitter.setSizes([700, 700])

        root_layout.addWidget(self.splitter)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(T("status.ready"))

        # Shortcuts
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._next_page)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self._prev_page)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self._next_page)
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self._prev_page)
        QShortcut(
            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_E), self, self._on_export_batch
        )
        QShortcut(
            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_M), self, self._toggle_markdown
        )

        # Tema (chiaro/scuro/sistema): QSS dai token del modulo theme.
        self.apply_theme()

        # L'apertura del PDF è gestita in main(): argomento da riga di comando
        # oppure harrison2025.pdf nella directory corrente.

    def apply_theme(self) -> None:
        """Re-apply the active theme to the window and all child widgets."""
        self.setStyleSheet(theme.main_qss())
        self.text_panel.apply_theme()
        self.toc_panel.apply_theme()
        self.pdf_view.apply_theme()
        self.lbl_printed.setStyleSheet("color: %s;" % theme.color("text5"))
        reader = getattr(self, "_reader_window", None)
        if reader is not None:
            reader.retranslate()

    def _on_system_theme(self, _scheme=None) -> None:
        """Follow the OS color scheme when the mode is ``system``."""
        if self._theme_mode == "system":
            apply_theme_mode("system")
            self.apply_theme()

    # ── toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self, parent_layout: QVBoxLayout):
        bar = QToolBar(T("toolbar.nav"))
        bar.setMovable(False)
        parent_layout.addWidget(bar)

        # Apri
        self.btn_open = QPushButton(T("toolbar.open"))
        self.btn_open.clicked.connect(self._on_open)
        bar.addWidget(self.btn_open)

        # Esporta batch (Ctrl+E)
        self.btn_export_batch = QPushButton(T("toolbar.export"))
        self.btn_export_batch.setToolTip(T("toolbar.export.tip"))
        self.btn_export_batch.clicked.connect(self._on_export_batch)
        bar.addWidget(self.btn_export_batch)

        # TOC toggle
        self.btn_toc = QPushButton(T("toolbar.toc"))
        self.btn_toc.setCheckable(True)
        self.btn_toc.setChecked(True)
        self.btn_toc.setToolTip(T("toolbar.toc.tip"))
        self.btn_toc.clicked.connect(
            lambda checked: self.toc_dock.setVisible(checked)
        )
        bar.addWidget(self.btn_toc)

        bar.addSeparator()

        # Prev
        self.btn_prev = QPushButton(T("toolbar.prev"))
        self.btn_prev.clicked.connect(self._prev_page)
        bar.addWidget(self.btn_prev)

        # Page spin
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setValue(1)
        # Keyboard tracking off: with it on, every keystroke committed a value
        # and fired valueChanged -> _set_page -> full page render + text
        # extraction (Docling ~2-6s), freezing the box while typing. Now the
        # page changes only on Enter / focus-out. The up/down spin buttons are
        # hidden via QSS (cleaner look); typing + Enter or ◀ ▶ do navigation.
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(self._on_spin)
        self.page_spin.setEnabled(False)
        bar.addWidget(self.page_spin)

        self.lbl_of = QLabel(T("toolbar.of"))
        bar.addWidget(self.lbl_of)
        self.lbl_total = QLabel("0")
        bar.addWidget(self.lbl_total)

        # Numero di pagina **stampato** (offset rispetto all'indice PDF):
        # informativo, letto dal margine. Vuoto se non riconosciuto.
        self.lbl_printed = QLabel("")
        self.lbl_printed.setStyleSheet("color: %s;" % theme.color("text5"))
        bar.addWidget(self.lbl_printed)

        # Next
        self.btn_next = QPushButton(T("toolbar.next"))
        self.btn_next.clicked.connect(self._next_page)
        bar.addWidget(self.btn_next)

        bar.addSeparator()

        # Reader: menu con reader interno + visualizzatore di sistema.
        self.btn_reader = QToolButton()
        self.btn_reader.setText(T("toolbar.reader"))
        self.btn_reader.setToolTip(T("toolbar.reader.tip"))
        self.btn_reader.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_reader.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reader_menu = QMenu(self.btn_reader)
        self.act_reader_internal = self._reader_menu.addAction(
            T("toolbar.reader.internal"), self._open_reader)
        self.act_reader_external = self._reader_menu.addAction(
            T("toolbar.reader.external"), self._open_external_viewer)
        self.btn_reader.setMenu(self._reader_menu)
        bar.addWidget(self.btn_reader)

        bar.addSeparator()

        # Markdown rendering toggle
        self.btn_md_toggle = QPushButton(T("toolbar.md.on"))
        self.btn_md_toggle.setToolTip(T("toolbar.md.tip"))
        self.btn_md_toggle.setCheckable(True)
        self.btn_md_toggle.setChecked(self._render_md)
        self.btn_md_toggle.clicked.connect(self._toggle_markdown)
        bar.addWidget(self.btn_md_toggle)

        bar.addSeparator()

        # Impostazioni (lingua UI, lingue traduzione, preferenze) — spinto a
        # destra da uno spacer espanso. Il cambio lingua UI avviene SOLO qui.
        _spacer = QWidget()
        _spacer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        bar.addWidget(_spacer)
        self.btn_settings = QPushButton(T("settings.button"))
        self.btn_settings.setToolTip(T("settings.button.tip"))
        self.btn_settings.clicked.connect(self._on_open_settings)
        bar.addWidget(self.btn_settings)

        # Guida online (apre il sito help nel browser di sistema)
        self.btn_help = QPushButton(T("toolbar.help"))
        self.btn_help.setToolTip(T("toolbar.help.tip"))
        self.btn_help.clicked.connect(self._on_open_help)
        bar.addWidget(self.btn_help)

    def _build_page_toolbar(self):
        """Mini toolbar shown above the PDF page viewer (left panel)."""
        bar = QToolBar(T("page_toolbar.title"))
        bar.setMovable(False)

        # Cattura: menu con due azioni — solo immagine, oppure immagine+testo
        # (interpretazione nativo/OCR).
        self.btn_capture = QToolButton()
        self.btn_capture.setText(T("page_toolbar.capture"))
        self.btn_capture.setToolTip(T("page_toolbar.capture.tip"))
        self.btn_capture.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_capture.setCursor(Qt.CursorShape.PointingHandCursor)
        self._capture_menu = QMenu(self.btn_capture)
        self.act_capture_image = self._capture_menu.addAction(
            T("page_toolbar.capture.image"),
            lambda: self._begin_capture(False))
        self.act_capture_interpret = self._capture_menu.addAction(
            T("page_toolbar.capture.interpret"),
            lambda: self._begin_capture(True))
        self.act_capture_interpret.setToolTip(
            T("page_toolbar.capture.interpret.tip"))
        self.btn_capture.setMenu(self._capture_menu)
        bar.addWidget(self.btn_capture)

        # Zone menu (escludi / includi / reset) — un solo pulsante con menu,
        # così la mini-toolbar resta compatta (come il mockup §2.2).
        self.btn_zones = QToolButton()
        self.btn_zones.setText(T("page_toolbar.zone_group"))
        self.btn_zones.setToolTip(T("page_toolbar.zone.tip"))
        self.btn_zones.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_zones.setCursor(Qt.CursorShape.PointingHandCursor)
        self._zones_menu = QMenu(self.btn_zones)
        self.btn_exclude = self._zones_menu.addAction(T("page_toolbar.exclude"))
        self.btn_exclude.setCheckable(True)
        self.btn_exclude.setToolTip(T("page_toolbar.exclude.tip"))
        self.btn_exclude.toggled.connect(self._on_exclude_toggled)
        self.btn_include = self._zones_menu.addAction(T("page_toolbar.include"))
        self.btn_include.setCheckable(True)
        self.btn_include.setToolTip(T("page_toolbar.include.tip"))
        self.btn_include.toggled.connect(self._on_include_toggled)
        self._zones_menu.addSeparator()
        self.btn_reset_zones = self._zones_menu.addAction(T("page_toolbar.reset"))
        self.btn_reset_zones.setToolTip(T("page_toolbar.reset.tip"))
        self.btn_reset_zones.triggered.connect(self._on_reset_zones)
        self.btn_zones.setMenu(self._zones_menu)
        bar.addWidget(self.btn_zones)

        # Estrai: conclude l'editing delle zone e riesegue l'estrazione.
        self.btn_extract_zones = QPushButton(T("page_toolbar.extract"))
        self.btn_extract_zones.setToolTip(T("page_toolbar.extract.tip"))
        self.btn_extract_zones.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_extract_zones.clicked.connect(self._extract_with_zones)
        self.btn_extract_zones.setEnabled(False)
        bar.addWidget(self.btn_extract_zones)
        return bar

    def _update_zone_button(self) -> None:
        """Reflect the active zone mode on the ``🎯 Zone ▾`` button."""
        if self.btn_exclude.isChecked():
            self.btn_zones.setText(T("page_toolbar.exclude"))
        elif self.btn_include.isChecked():
            self.btn_zones.setText(T("page_toolbar.include"))
        else:
            self.btn_zones.setText(T("page_toolbar.zone_group"))

    def _update_capture_button(self) -> None:
        """Reflect the active capture mode on the ``📸 Cattura ▾`` button."""
        self.btn_capture.setText(
            T("page_toolbar.capture.active") if self._capture_active
            else T("page_toolbar.capture")
        )

    def _display_text(
        self,
        text: str,
        page_num: int = -1,
        images: list[str] | None = None,
    ):
        """Display the page text (already the auto-or-manual result)."""
        if images is None:
            images = self._current_images
        self.text_panel.show_text(
            text, as_markdown=self._render_md, page_num=page_num, images=images,
        )

    def _zones_key(self, page_num: int) -> str:
        """Stable fingerprint of the page's manual zones (cache key suffix)."""
        exclude = tuple(self._excluded_zones.get(page_num, ()))
        include = tuple(self._inclusion_zones.get(page_num, ()))
        if not exclude and not include:
            return "()"  # caso auto: chiave canonica, persistita su disco
        return repr((exclude, include))

    def _request_extraction(self, page_num: int):
        """Show the page text, extracting in the background if not cached.

        The whole pipeline (OCR + adaptive layout engine) runs in
        ``ExtractThread`` so the GUI never freezes on scanned PDFs.  The raw
        markdown is cached per ``(page, OCR language)`` and the final text per
        ``(page, OCR language, zones key)``: a repeat view (or a reopen with a
        warm disk cache) is displayed instantly, and changing manual zones
        only re-runs the layout engine (fast path, raw already cached).
        """
        if not self._pdf_path:
            return
        ocr_lang = _tess_lang_code(get_source_lang())
        zones_key = self._zones_key(page_num)
        key = (page_num, ocr_lang, zones_key)
        cached = self._final_text_cache.get(key)
        if cached is not None:
            text, label, elapsed = cached[0], cached[1], cached[2]
            self._last_result = (text, label, elapsed)
            self._last_elapsed = elapsed
            self.text_panel.hide_extraction_overlay()
            self._display_last_result()
            self._show_page_status(elapsed)
            return

        self.status_bar.showMessage(T("status.extracting"))
        self.text_panel.show_extraction_overlay(T("status.extracting"))
        self._extract_generation += 1

        old = self._extract_thread
        if old is not None:
            try:
                old.result_ready.disconnect()
            except TypeError:
                pass  # already disconnected
            if old.isRunning():
                # Non distruggere un thread ancora attivo: lo si ritira e si
                # pulisce quando termina da solo (risultati scartati dal guard).
                self._retired_extract_threads.append(old)
                old.finished.connect(self._forget_retired_extract_thread)

        thread = ExtractThread(
            str(self._pdf_path), page_num, self._extract_generation, ocr_lang,
            exclude=tuple(self._excluded_zones.get(page_num, ())),
            include=tuple(self._inclusion_zones.get(page_num, ())),
            raw=self._extraction_cache.get((page_num, ocr_lang)),
            figures_dir=str(self._get_images_dir()),
            zones_key=zones_key,
        )
        thread.result_ready.connect(self._on_extraction_done)
        self._extract_thread = thread
        thread.start()

    def _forget_retired_extract_thread(self):
        """Drop a retired extract thread once it finishes."""
        thread = self.sender()
        if thread in self._retired_extract_threads:
            self._retired_extract_threads.remove(thread)

    def _on_extraction_done(
        self,
        generation: int,
        page_num: int,
        text: str,
        label: str,
        raw: str,
        elapsed: float,
        engine: str = "ir",
    ):
        """Slot: background pipeline finished (stale results are ignored)."""
        if generation != self._extract_generation:
            return
        ocr_lang = _tess_lang_code(get_source_lang())
        self._extraction_cache[(page_num, ocr_lang)] = raw
        # La pipeline realmente usata (`ir`/`current`) è parte del record
        # finalizzato: l'harness la **osserva** da qui (non la ri-deriva).
        # Il risultato va salvato sotto la chiave-zone **della richiesta**, non
        # sotto quella corrente: se l'utente ha resettato le zone nel frattempo,
        # il risultato stantio non deve sovrascrivere la voce "()" buona.
        thread = self.sender()
        zones_key = getattr(thread, "_zones_key", None) or self._zones_key(page_num)
        self._final_text_cache[(page_num, ocr_lang, zones_key)] = (
            text, label, elapsed, engine,
        )
        self._save_extraction_cache()
        # Mostra solo se le zone della pagina non sono cambiate nel frattempo.
        if (page_num == self._current_page
                and zones_key == self._zones_key(self._current_page)):
            self._finish_extraction(page_num, text, label, elapsed)

    def _finish_extraction(
        self, page_num: int, text: str, label: str, elapsed: float = 0.0
    ):
        """Display a finished extraction (text + engine already computed)."""
        self._last_result = (text, label, elapsed)
        self._last_elapsed = elapsed
        self.text_panel.hide_extraction_overlay()
        self._display_last_result()
        self._show_page_status(elapsed)

    def _load_page_figures(self, page_num: int):
        """Mette in gallery le figure della pagina (dedup).

        Le figure **automatiche** sono embedded nel markdown come JPEG base64:
        qui si estraggono **dai data URI del corpo di pagina** (nessun file).
        Restano supportate le vecchie PNG su disco (modalità ``link``).
        """
        added = False
        body = self.text_panel._page_body or ""
        for uri in re.findall(r"!\[[^\]]*\]\((data:image/[^)]+)\)", body):
            if uri not in self._current_images:
                self._current_images.append(uri)
                added = True
        try:
            images_dir = self._get_images_dir()
        except Exception:
            images_dir = None
        if images_dir is not None:
            for path in sorted(images_dir.glob(f"page_{page_num + 1:04d}_fig_*.png")):
                uri = path.resolve().as_uri()
                if uri not in self._current_images:
                    self._current_images.append(uri)
                    added = True
        if added:
            self.text_panel.show_images(self._current_images, activate=False)

    def _display_last_result(self):
        """Re-display the stored extraction (header only if enabled).

        Used after a language/settings change: no re-extraction needed.
        """
        if self._last_result is None:
            return
        text, label, elapsed = self._last_result
        body = text
        if self._show_header:
            body = self._extraction_header(text, elapsed, label) + text
        self._display_text(body, page_num=self._current_page)
        # Gallery per pagina: ricarica le figure della pagina corrente (funziona
        # anche su pagina **cached**, dove l'estrazione non viene rifatta).
        self._load_page_figures(self._current_page)

    def _toggle_markdown(self):
        """Toggle Markdown rendering on/off and refresh display."""
        self._render_md = not self._render_md
        if self._render_md:
            self.btn_md_toggle.setText(T("toolbar.md.on"))
        else:
            self.btn_md_toggle.setText(T("toolbar.md.plain"))
        set_setting("render_md", self._render_md)
        save_config()
        # Re-render current text (cache-aware: no re-OCR on a cached page)
        self._refresh_current_page_text()

    # ── persistent raw-extraction cache ──────────────────────────────────

    def _set_extraction_cache(self, path: Path | None):
        """Point the raw-extraction cache at this PDF and load saved entries."""
        self._extraction_cache.clear()
        self._extraction_cache_file = None
        self._doc_fingerprint = ""
        if path is None:
            return
        try:
            st = path.stat()
            self._doc_fingerprint = f"{st.st_size}-{st.st_mtime_ns}"
        except Exception:
            return
        cache_dir = _app_data_base() / "extraction"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        self._extraction_cache_file = cache_dir / f"{path.stem}.json"
        self._load_extraction_cache()

    def _load_extraction_cache(self):
        """Load saved raw extraction when it matches the current fingerprint."""
        if (
            self._extraction_cache_file is None
            or not self._extraction_cache_file.exists()
        ):
            return
        try:
            data = json.loads(self._extraction_cache_file.read_text(encoding="utf-8"))
        except Exception:
            return
        if data.get("fingerprint") != self._doc_fingerprint:
            return
        # Un fix del motore cambia l'output: se la revisione non combacia, i
        # "final" salvati sono obsoleti e vanno rieseguiti (i "raw" restano,
        # così il re-extract è veloce).
        finals_ok = data.get("revision") == _CACHE_REVISION
        pages = data.get("pages") or {}
        for page_str, langs in pages.items():
            try:
                page = int(page_str)
            except (TypeError, ValueError):
                continue
            if not isinstance(langs, dict):
                continue
            for lang, value in langs.items():
                if isinstance(value, str):
                    # formato v1: {lang: testo grezzo}
                    self._extraction_cache[(page, lang)] = value
                elif isinstance(value, dict):
                    # formato v2: {lang: {raw, final}}
                    raw = value.get("raw")
                    if isinstance(raw, str):
                        self._extraction_cache[(page, lang)] = raw
                    final = value.get("final")
                    if (
                        finals_ok
                        and isinstance(final, (list, tuple))
                        and len(final) >= 3
                        and isinstance(final[0], str)
                    ):
                        # il "final" persistito è il caso auto (nessuna zona).
                        # Revisione ≥ 2: 4° campo = pipeline reale (ir|current).
                        engine = str(final[3]) if len(final) >= 4 else "ir"
                        self._final_text_cache[(page, lang, "()")] = (
                            final[0], final[1], float(final[2]), engine,
                        )

    def _save_extraction_cache(self):
        """Persist raw + auto final text to disk."""
        if self._extraction_cache_file is None:
            return
        pages: dict[str, dict[str, object]] = {}
        for (page, lang), raw in self._extraction_cache.items():
            entry: dict[str, object] = {"raw": raw}
            final = self._final_text_cache.get((page, lang, "()"))
            if final is not None:
                # 4° campo: pipeline reale (ir|current), se nota.
                entry["final"] = list(final[:4])
            pages.setdefault(str(page), {})[lang] = entry
        payload = {
            "revision": _CACHE_REVISION,
            "fingerprint": self._doc_fingerprint,
            "pages": pages,
        }
        try:
            self._extraction_cache_file.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    def _wait_extraction_threads(self):
        """Wait for any in-flight extraction before the app closes."""
        for t in self._retired_extract_threads:
            if t.isRunning():
                t.wait(3000)
        self._retired_extract_threads.clear()
        if self._extract_thread is not None and self._extract_thread.isRunning():
            self._extract_thread.wait(5000)

    # ── image extraction (manual region, PyMuPDF) ───────────────────────

    def _extract_image_region(
        self, page_num: int, clip, embedded_only: bool = False
    ) -> str | None:
        """Extract the image in a PDF-points rect and save it (file:// URI)."""
        doc = self._get_mupdf_doc()
        if doc is None or not _has_pymupdf:
            return None
        result = _region_image(
            doc, page_num, clip, max(self._render_scale, 4.0),
            embedded_only=embedded_only,
        )
        if result is None:
            return None
        data, ext = result
        images_dir = self._get_images_dir()
        prefix = f"page_{page_num + 1:04d}_region_"
        # Unique name: append after the regions already captured for this page.
        index = 0
        for old in images_dir.glob(prefix + "*"):
            try:
                index = max(index, int(old.stem.rsplit("_", 1)[-1]) + 1)
            except ValueError:
                index += 1
        path = images_dir / f"{prefix}{index}.{ext}"
        try:
            path.write_bytes(data)
        except Exception:
            return None
        return path.resolve().as_uri()

    def _on_region_selected(self, x0: float, y0: float, x1: float, y1: float):
        """Capture the user-selected region into the gallery.

        Always saves the image; when the action was "cattura e interpreta" it
        also starts a background interpretation (native/table/OCR).
        """
        interpret = self._capture_interpret
        self._set_select_mode(False)
        scale = self._render_scale or 1.0
        clip = (x0 / scale, y0 / scale, x1 / scale, y1 / scale)
        uri = self._extract_image_region(self._current_page, clip)
        if uri is None:
            self.status_bar.showMessage(T("status.no_image"))
            return
        self._current_images.append(uri)  # new captures accumulate in the gallery
        self.text_panel.set_capture(
            uri, "image", "", "none", self._current_page, "capture",
            rebuild=False)
        self.text_panel.show_images(self._current_images)
        name = Path(QUrl(uri).toLocalFile()).name
        self.status_bar.showMessage(T("status.image_extracted", name=name))
        if interpret:
            self._start_interpret(self._current_page, clip, uri)

    def _start_interpret(self, page_num: int, clip, uri: str) -> None:
        """Interpret a captured region in the background (native/table/OCR)."""
        if not self._pdf_path:
            return
        thread = CaptureInterpretThread(str(self._pdf_path), page_num, clip, uri)
        thread.result_ready.connect(self._on_capture_interpreted)
        old = self._interpret_thread
        if old is not None:
            try:
                old.result_ready.disconnect()
            except TypeError:
                pass
            if old.isRunning():
                self._retired_interpret_threads.append(old)
                old.finished.connect(self._forget_retired_interpret)
        self._interpret_thread = thread
        thread.start()

    def _forget_retired_interpret(self):
        """Drop a retired interpret thread once it finishes."""
        thread = self.sender()
        if thread in self._retired_interpret_threads:
            self._retired_interpret_threads.remove(thread)

    def _on_capture_interpreted(self, page_num: int, uri: str, text: str, method: str):
        """Attach the interpreted text to its gallery card (or report empty)."""
        if text:
            self.text_panel.set_capture(
                uri, _capture_kind(method), text, method, page_num)
            self.status_bar.showMessage(T("capture.text_ready", chars=len(text)))
        else:
            self.text_panel.set_capture(uri, "image", "", "none", page_num)
            self.status_bar.showMessage(T("capture.empty"))

    def _on_image_removed(self, uri: str):
        """Drop a captured image from the gallery and delete its file."""
        if uri in self._current_images:
            self._current_images.remove(uri)
        self.text_panel._captures.pop(uri, None)
        if not uri.startswith("data:"):  # le figure embedded non hanno file
            try:
                Path(QUrl(uri).toLocalFile()).unlink(missing_ok=True)
            except Exception:
                pass
        self.text_panel.show_images(self._current_images)

    def _begin_capture(self, interpret: bool) -> None:
        """Enter capture mode (image only, or image + interpretation).

        Blocked while the current page has manual zones: the user must reset
        them first, otherwise the two gestures would fight.
        """
        if self._page_has_zones(self._current_page):
            self._show_toast(T("page_toolbar.capture.blocked"), 3000)
            return
        self._capture_interpret = bool(interpret)
        self._set_select_mode(True)

    def _set_select_mode(self, enabled: bool):
        """Update the capture mode (and turn off the other modes)."""
        self._capture_active = enabled
        self.pdf_view.set_select_mode(enabled)
        if enabled:
            self.btn_exclude.setChecked(False)
            self.pdf_view.set_exclude_mode(False)
            self.btn_include.setChecked(False)
            self.pdf_view.set_include_mode(False)
        self._update_zone_button()
        self._update_capture_button()

    def _on_exclude_toggled(self, checked: bool):
        """Enable/disable rubber-band zone exclusion."""
        self._set_exclude_mode(checked)

    def _set_exclude_mode(self, enabled: bool):
        """Update the exclude-zone toggle (and turn off the other modes)."""
        self.btn_exclude.setChecked(enabled)
        self.pdf_view.set_exclude_mode(enabled)
        if enabled:
            self._capture_active = False
            self.pdf_view.set_select_mode(False)
            self.btn_include.setChecked(False)
            self.pdf_view.set_include_mode(False)
        self._update_zone_button()
        self._update_capture_button()

    def _on_include_toggled(self, checked: bool):
        """Enable/disable rubber-band zone inclusion."""
        self._set_include_mode(checked)

    def _set_include_mode(self, enabled: bool):
        """Update the include-zone toggle (and turn off the other modes)."""
        self.btn_include.setChecked(enabled)
        self.pdf_view.set_include_mode(enabled)
        if enabled:
            self._capture_active = False
            self.pdf_view.set_select_mode(False)
            self.btn_exclude.setChecked(False)
            self.pdf_view.set_exclude_mode(False)
        self._update_zone_button()
        self._update_capture_button()

    def _page_has_zones(self, page_num: int) -> bool:
        """True if the page has any manual exclusion/inclusion zone."""
        return bool(self._excluded_zones.get(page_num)) or bool(
            self._inclusion_zones.get(page_num))

    def _update_extract_button(self) -> None:
        """Enable ▶ Estrai only when the current page has manual zones."""
        self.btn_extract_zones.setEnabled(self._page_has_zones(self._current_page))

    def _extract_with_zones(self) -> None:
        """Conclude l'editing delle zone: svuota la cache della pagina (raw +
        final + traduzione, non le figure) e riesegue l'estrazione secondo le
        zone selezionate."""
        if not self._pdf_path or self._mupdf_doc is None or self._page_count == 0:
            return
        # Conclude la fase di editing: spegni tutti i modi di disegno.
        self._set_select_mode(False)
        self._set_exclude_mode(False)
        self._set_include_mode(False)
        page = self._current_page
        msg = T("cache.regenerating", page=page + 1)
        self.status_bar.showMessage(msg)
        self.text_panel._lbl_spinner.setText(msg)
        QApplication.processEvents()
        self._extract_generation += 1  # invalida eventuali estrazioni in volo
        for k in [k for k in self._extraction_cache if k[0] == page]:
            del self._extraction_cache[k]
        for k in [k for k in self._final_text_cache if k[0] == page]:
            del self._final_text_cache[k]
        self.text_panel.invalidate_page(page)
        self._save_extraction_cache()
        self._refresh_current_page_text()
        self._update_extract_button()

    def _on_region_excluded(self, x0: float, y0: float, x1: float, y1: float):
        """Store an excluded zone and re-extract the cleaned page.

        The exclude mode stays active so the user can draw as many zones as
        needed; click 🚫 Escludi zona again to leave the mode.

        If the drawn zone contains an embedded image, the same gesture also
        captures it into the 🖼️ Immagini gallery (embedded rasters only, no
        render fallback), so excluding a figure and keeping it are one action.
        """
        scale = self._render_scale or 1.0
        rect = (
            min(x0, x1) / scale, min(y0, y1) / scale,
            max(x0, x1) / scale, max(y0, y1) / scale,
        )
        if (rect[2] - rect[0]) < 1.0 or (rect[3] - rect[1]) < 1.0:
            return
        zones = self._excluded_zones.setdefault(self._current_page, [])
        zones.append(rect)
        self.pdf_view.show_excluded_zones(self._scene_exclusions(self._current_page))
        self.text_panel.invalidate_page(self._current_page)
        self._refresh_current_page_text()

        msg = T("status.zone_excluded", count=len(zones))
        uri = self._extract_image_region(self._current_page, rect, embedded_only=True)
        if uri is not None:
            self._current_images.append(uri)
            # Aggiungi la figura alla gallery ma riporta il focus alla
            # finestra "Originale": l'utente sta lavorando sull'esclusione
            # della zona, non sulla gallery.
            self.text_panel.show_images(self._current_images, activate=False)
            self.text_panel.show_original()
            name = Path(QUrl(uri).toLocalFile()).name
            msg = T("status.zone_excluded_image", name=name)
        self.status_bar.showMessage(msg)
        self._update_extract_button()

    def _on_region_included(self, x0: float, y0: float, x1: float, y1: float):
        """Store a numbered inclusion zone and re-extract in reading order.

        The include mode stays active so the user can draw the whole reading
        sequence; click 🟩 Includi zona again to leave the mode. The zone
        number is its position in the drawn sequence (shown on the box).
        """
        scale = self._render_scale or 1.0
        rect = (
            min(x0, x1) / scale, min(y0, y1) / scale,
            max(x0, x1) / scale, max(y0, y1) / scale,
        )
        if (rect[2] - rect[0]) < 1.0 or (rect[3] - rect[1]) < 1.0:
            return
        zones = self._inclusion_zones.setdefault(self._current_page, [])
        zones.append(rect)
        self.pdf_view.show_inclusion_zones(self._scene_inclusions(self._current_page))
        self.text_panel.invalidate_page(self._current_page)
        self._refresh_current_page_text()
        self.status_bar.showMessage(T("status.zone_included", count=len(zones)))
        self._update_extract_button()

    def _on_reset_zones(self):
        """Remove all zones (exclusions + inclusions) for the current page."""
        self._excluded_zones.pop(self._current_page, None)
        self._inclusion_zones.pop(self._current_page, None)
        self.pdf_view.show_excluded_zones([])
        self.pdf_view.show_inclusion_zones([])
        self.text_panel.invalidate_page(self._current_page)
        self._refresh_current_page_text()
        self.status_bar.showMessage(T("status.zones_reset"))
        self._update_extract_button()

    def _scene_exclusions(self, page_num: int) -> list[tuple]:
        """Convert the page's excluded zones (PDF points) to scene pixels."""
        scale = self._render_scale or 1.0
        return [
            tuple(v * scale for v in r)
            for r in self._excluded_zones.get(page_num, [])
        ]

    def _scene_inclusions(self, page_num: int) -> list[tuple]:
        """Convert the page's inclusion zones (PDF points) to scene pixels."""
        scale = self._render_scale or 1.0
        return [
            tuple(v * scale for v in r)
            for r in self._inclusion_zones.get(page_num, [])
        ]

    def _refresh_current_page_text(self):
        """Re-display the current page's text (cache-aware re-extraction)."""
        if not self._pdf_path or self._mupdf_doc is None or self._page_count == 0:
            return
        self._request_extraction(self._current_page)

    def _get_images_dir(self) -> Path:
        """Return (creating on first use) the per-document figures directory."""
        if self._images_dir is None:
            self._images_dir = _app_data_base() / "images" / self._pdf_path.stem
        self._images_dir.mkdir(parents=True, exist_ok=True)
        return self._images_dir

    # ── rigenera / svuota cache ───────────────────────────────────────────

    def _purge_page_cache(self, page_num: int) -> None:
        """Svuota la cache di una pagina: raw, final, figure (G7) e traduzione."""
        for k in [k for k in self._extraction_cache if k[0] == page_num]:
            del self._extraction_cache[k]
        for k in [k for k in self._final_text_cache if k[0] == page_num]:
            del self._final_text_cache[k]
        self.text_panel.invalidate_page(page_num)
        try:
            images_dir = self._get_images_dir()
            for p in images_dir.glob(f"page_{page_num + 1:04d}_fig_*.jpg"):
                try:
                    p.unlink()
                except Exception:
                    pass
        except Exception:
            pass
        self._page_images.pop(page_num, None)
        if page_num == self._current_page:
            self._current_images = self._page_images.setdefault(page_num, [])
        if self._last_result is not None and page_num == self._current_page:
            self._last_result = None

    def _regenerate_page(self):
        """Rigenera la pagina corrente: svuota la sua cache e riesegue tutto."""
        if not self._pdf_path or self._mupdf_doc is None or self._page_count == 0:
            return
        msg = T("cache.regenerating", page=self._current_page + 1)
        self.status_bar.showMessage(msg)
        self.text_panel._lbl_spinner.setText(msg)
        QApplication.processEvents()
        self._extract_generation += 1  # invalida eventuali estrazioni in volo
        self._purge_page_cache(self._current_page)
        self._save_extraction_cache()
        self._refresh_current_page_text()

    # ── azioni post-elaborazione (FAB / mini toolbar) ──────────────────

    def _copy_page(self, kind: str) -> None:
        """Copy the active window content (header stripped) to the clipboard."""
        panel = (
            self.text_panel.origin_panel
            if kind == "origin"
            else self.text_panel.translated_panel
        )
        text = _strip_header(panel.toPlainText())
        if not text.strip():
            self._show_toast(T("actions.copy_empty"), 2000)
            return
        QApplication.clipboard().setText(text)
        self._show_toast(T("actions.copied"), 2000)

    def _translate_page(self) -> None:
        """Switch to the translated tab and translate the current page."""
        self.text_panel._set_active_tab(self.text_panel._btn_translated)
        self.text_panel._maybe_show_translation()

    def _retranslate_page(self) -> None:
        """Purge only the current page's translation, then re-translate it."""
        page = self._current_page
        ret = QMessageBox.question(
            self,
            T("actions.translated.retranslate"),
            T("actions.retranslate_confirm", page=page + 1),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        # Purga mirata: solo la traduzione, l'estrazione resta in cache.
        self.text_panel.invalidate_page(page)
        self.text_panel._translated_text = ""
        self.text_panel._set_fab_visible(self.text_panel._fab_translated, False)
        self.text_panel._maybe_show_translation()

    def _translate_next(self) -> None:
        """Move to the next page and translate it (FAB on the translated tab)."""
        if self._current_page + 1 >= self._page_count:
            self._show_toast(T("actions.last_page"), 2500)
            return
        self.text_panel._set_active_tab(self.text_panel._btn_translated)
        self._set_page(self._current_page + 1)

    # ── esportazione batch ─────────────────────────────────────────────

    def _batch_cached_count(self, pages, content, engine, target) -> int:
        """How many of ``pages`` already have the needed content cached."""
        ocr_lang = _tess_lang_code(get_source_lang())
        n = 0
        for p in pages:
            ok = True
            if content in ("original", "both"):
                ok = ok and (p, ocr_lang, "()") in self._final_text_cache
            if content in ("translated", "both"):
                ok = ok and (
                    (p, engine, target) in self.text_panel._page_translation_cache
                )
            if ok:
                n += 1
        return n

    def _on_export_batch(self):
        """Open the wizard and launch the batch export thread."""
        if self._pdf_path is None or self._page_count == 0:
            QMessageBox.information(
                self, T("export.wizard.title"), T("export.need_doc"))
            return
        if self._batch_thread is not None:
            self._show_toast(T("export.busy"), 3000)
            return
        wiz = ExportWizardDialog(self)
        if wiz.exec() != QDialog.DialogCode.Accepted:
            return
        pages = wiz.chosen_pages()
        if not pages:
            return
        content = wiz.chosen_content()
        engine = wiz._current_engine()
        src = wiz._src_combo.currentData() or get_source_lang()
        dst = wiz._dst_combo.currentData() or get_target_lang()
        translate_missing = wiz.translate_missing()
        include_images = wiz.include_images()
        fmt = wiz.chosen_format()
        out_path = wiz.chosen_path()
        out_folder = wiz.chosen_folder() / wiz.chosen_name()

        ocr_lang = _tess_lang_code(get_source_lang())
        cached_original = {
            p: self._final_text_cache[(p, ocr_lang, "()")]
            for p in pages if (p, ocr_lang, "()") in self._final_text_cache
        }
        cached_translation = {
            p: self.text_panel._page_translation_cache[(p, engine, dst)]
            for p in pages
            if (p, engine, dst) in self.text_panel._page_translation_cache
        }
        cached = self._batch_cached_count(pages, content, engine, dst)
        if (
            not translate_missing
            and content in ("translated", "both")
            and cached == 0
        ):
            QMessageBox.information(
                self, T("export.wizard.title"), T("export.none_ready"))
            return

        thread = BatchExportThread(
            str(self._pdf_path), pages, content, translate_missing,
            engine, src, dst, ocr_lang, str(self._get_images_dir()),
            cached_original, cached_translation,
        )
        dlg = ExportProgressDialog(self)
        info = T(
            "export.progress.engine_lang",
            engine=T(f"engine.option.{engine}"),
            src=flag_endonym(src), dst=flag_endonym(dst),
        )
        pages_label = T("export.progress.pages", label=format_pages_label(pages))
        dlg.begin(len(pages), cached, info, pages_label)
        dlg.cancel_requested.connect(thread.cancel)
        thread.page_started.connect(dlg.on_page_started)
        thread.page_done.connect(dlg.on_page_done)
        thread.page_error.connect(dlg.on_page_error)
        thread.page_extracted.connect(self._on_batch_extracted)
        thread.page_translated.connect(
            lambda p, txt, e=engine, t=dst: self._on_batch_translated(p, txt, e, t))
        thread.completed.connect(
            lambda: self._on_batch_completed(
                thread, dlg, pages, content, include_images, fmt,
                out_path, out_folder))
        thread.finished.connect(self._on_batch_thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._batch_thread = thread
        self._batch_dialog = dlg
        thread.start()

    def _on_batch_thread_finished(self) -> None:
        """Release the batch thread slot once it has really stopped."""
        self._batch_thread = None

    def _on_batch_extracted(self, page, text, label, raw, engine) -> None:
        """Warm the app's extraction caches from a batch result."""
        ocr_lang = _tess_lang_code(get_source_lang())
        self._extraction_cache[(page, ocr_lang)] = raw
        self._final_text_cache[(page, ocr_lang, "()")] = (text, label, 0.0, engine)

    def _on_batch_translated(self, page, translated, engine, target) -> None:
        """Warm the app's translation cache from a batch result."""
        self.text_panel._page_translation_cache[(page, engine, target)] = translated

    def _batch_apply_edits(self, window: str, text: str) -> str:
        """Return the user's saved edit for a page body, else the body itself."""
        body = _strip_header(text)
        saved = self.text_panel._edit_cache.get(window, {}).get(_text_key(body))
        return saved if saved is not None else body

    @staticmethod
    def _batch_strip_images(text: str) -> str:
        """Remove Markdown image embeds when figures are excluded."""
        return re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)

    def _batch_page_content(self, entry: dict, content: str, include_images: bool) -> str:
        parts: list[str] = []
        if content in ("original", "both") and "original" in entry:
            parts.append(self._batch_apply_edits("origin", entry["original"]))
        if content in ("translated", "both") and "translated" in entry:
            parts.append(self._batch_apply_edits("translated", entry["translated"]))
        text = "\n\n".join(p for p in parts if p.strip())
        if not include_images:
            text = self._batch_strip_images(text)
        return text

    def _write_batch_output(
        self, thread, pages, content, include_images, fmt, out_path, out_folder
    ) -> Path:
        """Compose and write the batch output; return the created path."""
        stem = self._pdf_path.stem if self._pdf_path else "documento"
        if fmt == "merged":
            chunks = []
            for p in pages:
                body = self._batch_page_content(
                    thread.results.get(p, {}), content, include_images)
                chunks.append(f"<!-- pagina {p + 1} -->\n\n{body}")
            text = "\n\n---\n\n".join(chunks)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
            return out_path
        files: dict[str, str] = {}
        for p in pages:
            body = self._batch_page_content(
                thread.results.get(p, {}), content, include_images)
            files[f"{stem}_pag{p + 1:04d}.md"] = body
        if fmt == "zip":
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for name, body in files.items():
                    zf.writestr(name, body)
            return out_path
        out_folder.mkdir(parents=True, exist_ok=True)
        for name, body in files.items():
            (out_folder / name).write_text(body, encoding="utf-8")
        return out_folder

    def _on_batch_completed(
        self, thread, dlg, pages, content, include_images, fmt,
        out_path, out_folder,
    ) -> None:
        """Write the output, persist caches and notify the user."""
        cancelled = bool(thread._cancel)
        path: Path | None = None
        if thread.results and not cancelled:
            try:
                path = self._write_batch_output(
                    thread, pages, content, include_images, fmt, out_path, out_folder)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, T("export.error"), str(exc))
        self._save_extraction_cache()
        self.text_panel._save_disk_cache()
        done = len(thread.results)
        failed = len(thread.errors)
        dlg.on_finished(path, cancelled)
        self._notify_batch(done, failed, cancelled)
        # Se la pagina corrente è stata (ri)calcolata, aggiorna la vista.
        if self._current_page in thread.results:
            self._refresh_current_page_text()

    def _notify_batch(self, done: int, failed: int, cancelled: bool) -> None:
        """Status-bar notification + beep at the end of a batch (if enabled)."""
        if not bool(get_setting("notify_batch", True)):
            return
        if cancelled:
            msg = T("notify.batch.cancelled")
        elif failed:
            msg = T("notify.batch.partial", count=done, failed=failed)
        else:
            msg = T("notify.batch.done", count=done)
        self.status_bar.showMessage(msg, 6000)
        try:
            QApplication.beep()
        except Exception:  # noqa: BLE001
            pass

    def _clear_document_cache(self):
        """Svuota la cache di TUTTO il documento e svuota i pannelli.

        Non riestrae da solo: dopo il reset il pannello mostra un invito a
        premere "Riestrai pagina" (↻), così l'utente vede che la cache è vuota
        e decide quando riestrarre.
        """
        if not self._pdf_path or self._mupdf_doc is None or self._page_count == 0:
            return
        ret = QMessageBox.question(
            self,
            T("cache.clear.title"),
            T("cache.clear.confirm"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.status_bar.showMessage(T("cache.clearing"))
        self.text_panel._lbl_spinner.setText(T("cache.clearing"))
        QApplication.processEvents()
        self._extract_generation += 1
        self._extraction_cache.clear()
        self._final_text_cache.clear()
        self.text_panel.invalidate_cache()
        try:
            images_dir = self._get_images_dir()
            for p in images_dir.glob("page_*_fig_*.jpg"):
                try:
                    p.unlink()
                except Exception:
                    pass
        except Exception:
            pass
        self._page_images.clear()
        self._current_images = []
        self._last_result = None
        self._save_extraction_cache()
        # Pannello svuotato: niente testo vecchio a schermo + invito a riestrarre.
        self.text_panel.clear_pages(T("cache.cleared_hint"))
        self.status_bar.showMessage(T("cache.cleared"))
        self.text_panel._lbl_spinner.setText(T("cache.cleared"))

    def _cache_diagnostics(self) -> str:
        """Testo (monospazio) su quale cache l'app sta usando per la pagina."""
        out: list[str] = []
        out.append(f"PDF:            {self._pdf_path}")
        out.append(f"pipeline:       {_pipeline_mode()}")
        out.append(f"app data base:  {_app_data_base()}")
        out.append(f"cache file:     {self._extraction_cache_file}")
        f = self._extraction_cache_file
        exists = bool(f and f.exists())
        out.append(f"cache esiste:   {exists}")
        out.append(f"revision attesa:{_CACHE_REVISION}")
        if exists:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                out.append(f"revision disco: {data.get('revision')}")
                out.append(f"fingerprint:    {data.get('fingerprint')}")
            except Exception as e:  # noqa: BLE001
                out.append(f"cache illeggibile: {e}")
        out.append(f"fingerprint doc:{self._doc_fingerprint}")
        out.append("")
        page = self._current_page
        lang = _tess_lang_code(get_source_lang())
        zk = self._zones_key(page)
        out.append(f"pagina:         {page + 1}  (idx {page})")
        out.append(f"lingua OCR:     {lang}")
        out.append(f"zones key:      {zk}")
        out.append(f"raw in memoria: {(page, lang) in self._extraction_cache}")
        out.append(f"final in mem.:  {(page, lang, zk) in self._final_text_cache}")
        out.append(f"pagine in cache:{len({p for (p, _l) in self._extraction_cache})}")
        try:
            d = self._get_images_dir()
            figs = sorted(d.glob(f"page_{page + 1:04d}_fig_*.jpg"))
            out.append(f"figure dir:     {d}")
            out.append(f"figure pagina:  {len(figs)}  {[p.name for p in figs]}")
        except Exception as e:  # noqa: BLE001
            out.append(f"figure: n/d ({e})")
        out.append("")
        tc = getattr(self.text_panel, "_page_translation_cache", {})
        engine = getattr(self.text_panel, "_engine", "?")
        target = getattr(self.text_panel, "_target_lang", "?")
        out.append(f"traduzioni in cache: {len(tc)}")
        out.append(f"trad. pagina:   {(page, engine, target) in tc}")
        if self._last_result is not None:
            _t, label, elapsed = self._last_result
            out.append(f"ultimo esito:   label={label}  {elapsed:.2f}s")
        # Confronto lunghezze/estremi: ciò che l'app ha in memoria vs la cache.
        last_txt = (self._last_result[0] if self._last_result else "") or ""
        body = getattr(self.text_panel, "_page_body", "") or ""
        cache_final = self._final_text_cache.get((page, lang, zk))
        out.append("")
        out.append(f"last_result:    {len(last_txt)} char")
        out.append(f"body mostrato:  {len(body)} char")
        out.append(f"final in cache: {len(cache_final[0]) if cache_final else 0} char")
        for name, txt in (("last_result", last_txt), ("body", body)):
            tail = [x for x in txt.split("\n") if x.strip()][-2:]
            out.append(f"ultime 2 righe [{name}]:")
            for ln in tail:
                out.append("   " + ln[:110])
        return "\n".join(out)

    def _show_cache_info(self):
        """Pannello diagnostico: quale cache e cosa c'e' per la pagina corrente."""
        dlg = QDialog(self)
        dlg.setWindowTitle(T("cache.info.title"))
        dlg.resize(760, 500)
        dlg.setStyleSheet(theme.settings_qss())
        lay = QVBoxLayout(dlg)
        edit = QTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(self._cache_diagnostics())
        edit.setStyleSheet(
            "font-family: 'Consolas','monospace'; font-size: 12px;"
        )
        lay.addWidget(edit)
        row = QHBoxLayout()
        btn_copy = QPushButton(T("cache.info.copy"))
        btn_copy.clicked.connect(
            lambda: (
                QApplication.clipboard().setText(edit.toPlainText()),
                self.status_bar.showMessage(T("cache.info.copied")),
            )
        )
        btn_close = QPushButton(T("cache.info.close"))
        btn_close.clicked.connect(dlg.accept)
        row.addStretch()
        row.addWidget(btn_copy)
        row.addWidget(btn_close)
        lay.addLayout(row)
        dlg.exec()

    # ── navigation ────────────────────────────────────────────────────────

    def _set_page(self, page_num: int):
        if self._mupdf_doc is None or self._page_count == 0:
            return
        count = self._page_count
        page_num = max(0, min(page_num, count - 1))
        self._current_page = page_num
        self.text_panel.hide_extraction_overlay()
        self._set_select_mode(False)
        # Gallery per pagina: la lista corrente diventa quella di questa pagina
        # (le figure già note ricompaiono subito).
        self._current_images = self._page_images.setdefault(page_num, [])
        self._remember_last_page(page_num)

        # Render left
        self._display_page(page_num)

        # Extract text right (async, cache-aware: OCR pages don't block the UI)
        if self._pdf_path:
            self._request_extraction(page_num)

        # Update toolbar
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page_num + 1)
        self.page_spin.blockSignals(False)
        printed = _printed_page_number(
            self._mupdf_doc[page_num] if self._mupdf_doc is not None else None)
        self.lbl_printed.setText(
            T("toolbar.printed", n=printed) if printed is not None else "")

        # Sync TOC highlight
        self.toc_panel.select_page(page_num)
        self._update_extract_button()

    def _remember_last_page(self, page_num: int):
        """Track the current page per document; persist with a 2 s debounce."""
        if not self._resume_last_page or not self._pdf_path:
            return
        pages = dict(get_setting("last_pages", {}) or {})
        pages[self._pdf_path.name] = int(page_num)
        set_setting("last_pages", pages)
        self._last_page_timer.start()

    def _show_page_status(self, elapsed: float = 0.0):
        """Status-bar message for the current page (or the ready hint)."""
        if not self._pdf_path:
            self.status_bar.showMessage(T("status.ready"))
            return
        self.status_bar.showMessage(
            T(
                "status.page",
                page=self._current_page + 1,
                total=self._page_count,
                name=self._pdf_path.name,
                ms=f"{elapsed*1000:.0f}",
            )
        )

    def _next_page(self):
        self._set_page(self._current_page + 1)

    def _prev_page(self):
        self._set_page(self._current_page - 1)

    def _on_spin(self, val: int):
        self._set_page(val - 1)

    def _goto_toc_page(self, page_idx: int):
        """Navigate to a page selected from the TOC."""
        self._set_page(page_idx)

    # ── settings dialog ────────────────────────────────────────────────────

    def _on_open_settings(self):
        """Open the config dialog; apply on OK."""
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._apply_settings(dlg.values())

    def _on_open_help(self):
        """Open the online help site in the system browser."""
        QDesktopServices.openUrl(QUrl(HELP_URL))

    def _open_reader(self):
        """Open the detached, read-only reader on the current page."""
        if self._pdf_path is None:
            return
        old = getattr(self, "_reader_window", None)
        if old is not None:
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
        self._reader_window = PdfReaderWindow(
            self._pdf_path, self._current_page, self)
        self._reader_window.show()

    def _open_external_viewer(self):
        """Open the PDF with the operating system's default viewer."""
        if self._pdf_path is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._pdf_path)))

    def _apply_settings(self, values: dict):
        """Apply the settings dialog choices (languages + preferences)."""
        ui_changed = values.get("lang") != self._ui_lang_applied
        src = values.get("src_lang", get_source_lang())
        dst = values.get("dst_lang", get_target_lang())
        engine = values.get("engine", get_translation_engine())
        langs_changed = src != get_source_lang() or dst != get_target_lang()
        engine_changed = engine != get_translation_engine()

        if values.get("lang") in LANGUAGES:
            set_language(values["lang"])
        for key in ("zoom", "font_size", "render_md", "show_header",
                    "resume_last_page", "remember_tab", "save_edits",
                    "notify_batch"):
            if key in values:
                set_setting(key, values[key])
        set_source_lang(src)   # setters validati (auto solo in sorgente)
        set_target_lang(dst)
        set_translation_engine(engine)
        save_config()

        # Applicazione immediata delle preferenze
        new_theme = str(values.get("theme", self._theme_mode))
        if new_theme != self._theme_mode:
            self._theme_mode = new_theme
            set_setting("theme", new_theme)
            save_config()
            apply_theme_mode(new_theme)
            self.apply_theme()
        zoom = float(values.get("zoom", self._base_render_scale))
        if abs(zoom - self._base_render_scale) > 1e-9:
            # "Qualità di rendering": nuova risoluzione base, re-render pagina.
            self._base_render_scale = zoom
            self._render_scale = self._base_render_scale
            set_setting("zoom", self._base_render_scale)
            save_config()
            if self._mupdf_doc is not None and self._page_count > 0:
                self._display_page(self._current_page)
        self._render_md = bool(values.get("render_md", self._render_md))
        self._show_header = bool(values.get("show_header", self._show_header))
        self._resume_last_page = bool(
            values.get("resume_last_page", self._resume_last_page)
        )
        self._remember_tab = bool(values.get("remember_tab", self._remember_tab))
        self.btn_md_toggle.setChecked(self._render_md)
        self.btn_md_toggle.setText(
            T("toolbar.md.on") if self._render_md else T("toolbar.md.plain")
        )
        self.text_panel.set_font_size(int(values.get("font_size", 12)))
        self.text_panel.set_translation_languages(src, dst, engine)
        self.text_panel.set_save_edits(bool(values.get("save_edits", True)))

        if ui_changed:
            self._retranslate_all()
        else:
            self._display_last_result()  # header on/off + nuovo font
        if (langs_changed or engine_changed) and self.text_panel._btn_translated.isChecked():
            self.text_panel._maybe_show_translation()

    def _show_toast(self, message: str, ms: int):
        """Show a transient message in the status bar (the app's 'toast')."""
        self.status_bar.showMessage(message, ms)

    def clear_saved_edits(self):
        """Wipe the current document's saved edits and re-show fresh text."""
        if self._mupdf_doc is None:
            return
        self.text_panel.clear_saved_edits()
        self._display_last_result()
        self.status_bar.showMessage(T("settings.edits.clear_done"), 3000)

    def _retranslate_all(self):
        """Re-apply every UI string after a language switch."""
        self.retranslate()
        self.text_panel.retranslate()
        if self._batch_dialog is not None:
            self._batch_dialog.retranslate()
        if self._mupdf_doc is not None:
            # Il TOC riporta "(senza titolo)" e il suffisso "p." per pagina:
            # ricostruirlo è economico e lo allinea alla lingua attiva.
            self.toc_panel.build_toc(self._mupdf_doc)
        self._display_last_result()
        self._show_page_status(self._last_elapsed)
        self._ui_lang_applied = get_language()

    def retranslate(self):
        """Re-apply the MainWindow's own chrome strings."""
        self.btn_settings.setText(T("settings.button"))
        self.btn_settings.setToolTip(T("settings.button.tip"))
        self.btn_help.setText(T("toolbar.help"))
        self.btn_help.setToolTip(T("toolbar.help.tip"))
        self.btn_open.setText(T("toolbar.open"))
        self.btn_export_batch.setText(T("toolbar.export"))
        self.btn_export_batch.setToolTip(T("toolbar.export.tip"))
        self.btn_toc.setText(T("toolbar.toc"))
        self.btn_toc.setToolTip(T("toolbar.toc.tip"))
        self.btn_prev.setText(T("toolbar.prev"))
        self.btn_next.setText(T("toolbar.next"))
        self.lbl_of.setText(T("toolbar.of"))
        self.btn_reader.setText(T("toolbar.reader"))
        self.btn_reader.setToolTip(T("toolbar.reader.tip"))
        self.act_reader_internal.setText(T("toolbar.reader.internal"))
        self.act_reader_external.setText(T("toolbar.reader.external"))
        self.btn_md_toggle.setText(
            T("toolbar.md.on") if self._render_md else T("toolbar.md.plain")
        )
        self.btn_md_toggle.setToolTip(T("toolbar.md.tip"))
        self.btn_capture.setToolTip(T("page_toolbar.capture.tip"))
        self.act_capture_image.setText(T("page_toolbar.capture.image"))
        self.act_capture_interpret.setText(T("page_toolbar.capture.interpret"))
        self.act_capture_interpret.setToolTip(
            T("page_toolbar.capture.interpret.tip"))
        self._update_capture_button()
        self.btn_extract_zones.setText(T("page_toolbar.extract"))
        self.btn_extract_zones.setToolTip(T("page_toolbar.extract.tip"))
        self.btn_exclude.setText(T("page_toolbar.exclude"))
        self.btn_exclude.setToolTip(T("page_toolbar.exclude.tip"))
        self.btn_include.setText(T("page_toolbar.include"))
        self.btn_include.setToolTip(T("page_toolbar.include.tip"))
        self.btn_reset_zones.setText(T("page_toolbar.reset"))
        self.btn_reset_zones.setToolTip(T("page_toolbar.reset.tip"))
        self._update_zone_button()
        self.toc_dock.setWindowTitle(T("dock.toc"))
        self.pdf_view.retranslate()
        reader = getattr(self, "_reader_window", None)
        if reader is not None:
            reader.retranslate()

    def _extraction_header(self, text: str, elapsed: float, label: str = "auto") -> str:
        """Build the header line shown above the extracted text.

        ``label`` is a key suffix ("auto"/"manual"): it is resolved through
        T() at display time, so a language switch re-renders it correctly.
        ``ocr`` is the OCR language derived from the source-language setting
        ("🌐 Auto" when detection is automatic) and ``engine`` is the active
        translation engine: both are resolved at display time too, so they
        stay in sync with settings/language changes.
        """
        src = get_source_lang()
        return T(
            "header.line",
            ms=f"{elapsed*1000:.1f}",
            chars=len(text),
            label=T(f"engine.label.{label}"),
            ocr=flag_endonym(src or "auto"),
            engine=T(f"engine.option.{get_translation_engine()}"),
        )

    # ── rendering engine ──────────────────────────────────────────────────

    def _get_mupdf_doc(self):
        """Open (lazily) the pymupdf document for rendering."""
        if self._mupdf_doc is None and self._pdf_path and _has_pymupdf:
            try:
                self._mupdf_doc = pymupdf.open(str(self._pdf_path))
            except Exception:
                self._mupdf_doc = None
        return self._mupdf_doc

    def _render_pymupdf(self, page_num: int) -> QPixmap | None:
        doc = self._get_mupdf_doc()
        if doc is None:
            return None
        try:
            page = doc[page_num]
            pix = page.get_pixmap(
                matrix=pymupdf.Matrix(self._render_scale, self._render_scale)
            )
            img = QImage(
                pix.samples, pix.width, pix.height, pix.stride,
                QImage.Format.Format_RGB888,
            )
            return QPixmap.fromImage(img)
        except Exception:
            return None

    def _render_page(self, page_num: int) -> QPixmap | None:
        """Render a page with PyMuPDF (single rendering engine)."""
        return self._render_pymupdf(page_num)

    def _display_page(self, page_num: int):
        """Render + show a page, with an informative fallback message."""
        pix = self._render_page(page_num)
        if pix is not None:
            self.pdf_view.show_page(pix)
            self.pdf_view.show_excluded_zones(self._scene_exclusions(page_num))
            self.pdf_view.show_inclusion_zones(self._scene_inclusions(page_num))
            return
        if not _has_pymupdf:
            self.pdf_view.show_message(T("view.no_pymupdf"))
        else:
            self.pdf_view.show_message(T("view.page_unavailable"))

    # ── file open ─────────────────────────────────────────────────────────

    def _on_open(self):
        path_str, _ = QFileDialog.getOpenFileName(
            self, T("dlg.open"), "", T("dlg.open_filter")
        )
        if path_str:
            self._open_pdf(Path(path_str))

    def _open_pdf(self, path: Path):
        if not path.exists():
            QMessageBox.warning(self, T("dlg.error"), T("dlg.file_not_found", path=path))
            return

        if self._mupdf_doc is not None:
            self._mupdf_doc.close()
            self._mupdf_doc = None

        try:
            self._mupdf_doc = pymupdf.open(str(path))
            self._pdf_path = path
            self._page_count = len(self._mupdf_doc)

            self._images_dir = None
            self._page_images = {}
            self._current_images = []
            self._excluded_zones = {}
            self._inclusion_zones = {}
            self._update_extract_button()
            self.lbl_printed.setText("")
            self.text_panel.set_document(path)
            self._set_extraction_cache(path)

            self.page_spin.setEnabled(True)
            self.page_spin.setMaximum(max(self._page_count, 1))
            self.btn_export_batch.setEnabled(True)
            self.btn_reader.setEnabled(True)
            self.lbl_total.setText(str(self._page_count))

            # Build the multi-level table of contents
            self.toc_panel.build_toc(self._mupdf_doc)

            if self._page_count > 0:
                start = 0
                if self._resume_last_page:
                    try:
                        start = int(
                            (get_setting("last_pages", {}) or {}).get(path.name, 0) or 0
                        )
                    except (TypeError, ValueError):
                        start = 0
                self._set_page(start)
            else:
                self.pdf_view.show_page(None)
                self._display_text(T("view.empty_pdf"))
                self.status_bar.showMessage(T("status.empty_pdf"))
                self.btn_export_batch.setEnabled(False)
                self.btn_reader.setEnabled(False)
        except Exception as e:
            QMessageBox.critical(self, T("dlg.pdf_error"), T("dlg.cannot_open", e=e))
            self._mupdf_doc = None
            self._pdf_path = None
            self._page_count = 0

    def closeEvent(self, event):
        if self._mupdf_doc is not None:
            self._mupdf_doc.close()
            self._mupdf_doc = None
        self._last_page_timer.stop()
        if self._batch_thread is not None and self._batch_thread.isRunning():
            self._batch_thread.cancel()
            self._batch_thread.wait(5000)
        if self._interpret_thread is not None and self._interpret_thread.isRunning():
            self._interpret_thread.wait(3000)
        self._wait_extraction_threads()
        self._save_extraction_cache()
        save_config()  # flush ultima pagina / ultima tab
        self.text_panel.shutdown()
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
#  entry point
# ═══════════════════════════════════════════════════════════════════════════════


def _asset_file(name: str) -> str:
    """Percorso di ``assets/<name>``, consapevole del bundle PyInstaller."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "assets", name)


def _app_icon() -> QIcon:
    """Icona dell'app (PNG 256×256, incluso nei build)."""
    path = _asset_file("noesispdf-256.png")
    return QIcon(path) if os.path.exists(path) else QIcon()


def main():
    # Nei build congelati (PyInstaller) punta l'OCR di PyMuPDF al Tesseract
    # incluso nel bundle (binario + librerie + tessdata) invece di richiederlo
    # come installazione di sistema.
    _setup_bundled_tesseract()

    app = QApplication(sys.argv)
    app.setApplicationName("noesis-pdf-reader-lite")
    app.setWindowIcon(_app_icon())

    # Config v2: al primo avvio vengono scritti i default (lingua UI = lingua
    # dell'OS o italiano); le scelte persistono tra gli aggiornamenti (la
    # cartella dati è ancorata al nome app, non alla versione).
    config_path = _config_file_path()
    cfg = init_config(config_path, defaults={**DEFAULTS, "lang": _detect_os_lang()})
    set_language(cfg["lang"])
    apply_theme_mode(str(cfg.get("theme", "dark")))

    window = MainWindow()
    window.show()

    # Segui il tema del sistema quando la modalità è "system".
    try:
        app.styleHints().colorSchemeChanged.connect(window._on_system_theme)
    except Exception:  # noqa: BLE001 — non deve mai impedire l'avvio
        pass

    # Apri un PDF passato da riga di comando (percorso assoluto o relativo);
    # in mancanza, fallback su harrison2025.pdf nella directory corrente.
    if len(sys.argv) > 1:
        pdf = Path(sys.argv[1])
    else:
        default = Path("harrison2025.pdf")
        pdf = default if default.exists() else None
    if pdf is not None:
        QTimer.singleShot(100, lambda: window._open_pdf(pdf))

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
