#!/usr/bin/env python3
"""Noesis PDF Reader Lite — PyQt6 split view: rendered page (left) + extracted text (right).

Versione semplificata: un solo motore di rendering (PyMuPDF), un solo motore di
estrazione (PyMuPDF4LLM) e l'engine adattativo dei fix di layout sempre attivo.
Traduzione e gallery delle figure incluse; nessun dropdown a runtime.
"""

import concurrent.futures
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import markdown as _md_lib

# Engine adattativo dei fix di layout (puro, senza PyQt): profilo → piano →
# pipeline. Importa lazy alcuni helper puri da questo modulo.
import layout_engine

_MD_EXTENSIONS = ["tables", "fenced_code", "codehilite"]

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
    Qt, QThread, QTimer, pyqtSignal, QUrl, QStandardPaths, QRectF,
)
from PyQt6.QtGui import (
    QImage, QPixmap, QFont, QKeySequence, QShortcut,
    QPen, QBrush, QColor, QPainter,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QSplitter,
    QScrollArea,
    QLabel,
    QTextEdit,
    QToolBar,
    QFileDialog,
    QSpinBox,
    QPushButton,
    QDialog,
    QMessageBox,
    QStatusBar,
    QWidget,
    QVBoxLayout,
    QDockWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QGraphicsView,
    QGraphicsScene,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsTextItem,
    QFrame,
)

# ═══════════════════════════════════════════════════════════════════════════════
#  helpers
# ═══════════════════════════════════════════════════════════════════════════════


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

    When ``embedded_only`` is True the render fallback is skipped and only an
    embedded raster whose placement is (mostly) inside the selection is
    returned. Used by the exclude-zone gesture so that excluding a text zone
    (header/footer/caption) never dumps a rendered PNG into the gallery.
    """
    if not _has_pymupdf:
        return None
    try:
        page = doc[page_num]
        rect = pymupdf.Rect(clip)
        if rect.width < 1.0 or rect.height < 1.0:
            return None

        # 1) embedded image whose placement is (fully, or in embedded_only
        #    mode at least half) inside the selection
        for img in page.get_images(full=True):
            xref = img[0]
            for r in page.get_image_rects(xref):
                if embedded_only:
                    # Sloppy selection around a figure (e.g. figure + caption)
                    # is fine: accept an image at least half inside the zone.
                    area = r.get_area()
                    if area <= 0 or (r & rect).get_area() / area < 0.5:
                        continue
                elif not (
                    r.x0 >= rect.x0 and r.y0 >= rect.y0
                    and r.x1 <= rect.x1 and r.y1 <= rect.y1
                ):
                    continue
                converted = _image_as_png(doc, xref)
                if converted is not None:
                    return converted

        if embedded_only:
            return None  # no render fallback in embedded-only mode

        # 2) fallback: render the selected region at high resolution
        pix = page.get_pixmap(clip=rect, matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png"), "png"
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
#  layout fixes (generic corrections for two-column / chapter-open pages)
# ═══════════════════════════════════════════════════════════════════════════════


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
                if not t.strip():
                    continue
                spans.append(
                    {
                        "text": t,
                        "size": s["size"],
                        "bold": bool(s["flags"] & 16),
                        "italic": bool(s["flags"] & 2),
                    }
                )
                max_size = max(max_size, s["size"])
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
        for s in line:
            t = s["text"]
            if s["bold"] and s["italic"]:
                parts.append(f"***{t}***")
            elif s["bold"]:
                parts.append(f"**{t}**")
            elif s["italic"]:
                parts.append(f"*{t}*")
            else:
                parts.append(t)
        lines.append("".join(parts).strip())

    if not as_column:
        return " ".join(lines)

    size = block["max_size"]
    level = 0
    if size >= 14:
        level = 1
    elif size >= 12:
        level = 2
    elif size >= 9.8 and any(s["bold"] for l in block["lines"] for s in l):
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
    header = (header + [""] * ncols)[:ncols]
    out.append("| " + " | ".join(header) + " |")
    out.append("| " + " | ".join("---" for _ in range(ncols)) + " |")
    for _, row in rows[1:]:
        cell_md = [txt.replace("\n", "<br>") for _, _, txt in row]
        cell_md = (cell_md + [""] * ncols)[:ncols]
        out.append("| " + " | ".join(cell_md) + " |")
    return "\n".join(out)


def _box_to_md(text: str) -> str:
    """Render a box's text as a single-column markdown table (first line = header)."""
    lines = []
    for ln in text.splitlines():
        ln = re.sub(r"[\x07\t]+", " ", ln)
        ln = re.sub(r"\s+", " ", ln).strip()
        if ln:
            lines.append(ln)
    if not lines:
        return ""
    out = [f"| {lines[0]} |", "| --- |"]
    out += [f"| {ln} |" for ln in lines[1:]]
    return "\n".join(out)


def _box_title(page, rect: tuple, exclude: tuple = ()) -> tuple[str, tuple | None]:
    """Text block directly above a box (same x-range) → (title, bbox or None)."""
    x0, y0, x1, y1 = rect
    for b in _collect_blocks(page, exclude):
        if not (b["y1"] <= y0 and b["y1"] >= y0 - 30):
            continue
        if not (b["x0"] >= x0 - 40 and b["x1"] <= x1 + 40):
            continue
        t = " ".join(s["text"] for line in b["lines"] for s in line)
        t = re.sub(r"\s+", " ", t).strip()
        if t:
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
        b for b in blocks if not any(_inside(b, r) for r in table_regions)
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
#  Google Translate (stdlib only, no API key)
# ═══════════════════════════════════════════════════════════════════════════════

_GT_URL = "https://translate.googleapis.com/translate_a/single"
_GT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def _gt_translate_one(text: str, source: str, target: str) -> str:
    """Call Google Translate API for a single chunk of text."""
    params = {
        "client": "gtx",
        "sl": source,
        "tl": target,
        "dt": "t",
        "q": text,
    }
    full_url = f"{_GT_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(full_url, headers=_GT_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result and result[0]:
        return "".join(item[0] for item in result[0] if item[0])
    return text


# Markdown structural patterns protected during translation.
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_TABLE_RE = re.compile(
    r"^\s*\|[^\n]*\|\s*\n\s*\|[\s:|-]+\|\s*\n(?:\s*\|[^\n]*\|\s*\n?)+",
    re.MULTILINE,
)
_SEP_CELL_RE = re.compile(r":?-{3,}:?")


_MAX_WORKERS = 8  # concurrent translation requests (I/O-bound)


def _translate_cell(cell: str, source: str, target: str) -> str:
    """Translate one table cell, falling back to the original on failure."""
    try:
        return _gt_translate_one(cell, source, target).strip()
    except Exception:
        return cell


def _translate_table(table: str, source: str, target: str) -> str:
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
                pool.submit(_translate_cell, c, source, target): c
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
    para: str, source: str, target: str, chunk_size: int
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
        return _protect("TBL", _translate_table(m.group(0), source, target))

    para = _MD_TABLE_RE.sub(_table_repl, para)

    # Nothing left to translate (only protected tokens) → skip the API call.
    if not re.search(r"[^\W\d_]", re.sub(r"@@[A-Z]+\d+@@", "", para)):
        out = para
    elif len(para) <= chunk_size:
        try:
            out = _gt_translate_one(para, source, target)
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
                sub_translated.append(_gt_translate_one(ch, source, target))
            except Exception:
                sub_translated.append(ch)
        out = " ".join(sub_translated)

    # Google may add spaces around/inside tokens; normalize them back.
    out = re.sub(r"@@\s*([A-Z]+)\s*(\d+)\s*@@", r"@@\1\2@@", out)
    for tok, value in protected.items():
        out = out.replace(tok, value)
    return out


def translate_text_google(
    text: str, source: str = "en", target: str = "it", chunk_size: int = 1500
) -> str:
    """Translate text using Google Translate's public API.

    Translates **each paragraph independently** (split on ``\n\n``) so
    paragraph breaks never pass through the API, and fetches those paragraphs
    **concurrently** so a short page doesn't wait on many sequential
    round-trips.  Markdown tables and image links are protected so Google
    doesn't mangle their syntax; table cell contents are translated
    individually (also concurrently).
    """
    if not text or not text.strip():
        return text

    paragraphs = text.split("\n\n")
    results: list[str] = [""] * len(paragraphs)
    tasks = [(i, p) for i, p in enumerate(paragraphs) if p.strip()]
    if not tasks:
        return text

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(_MAX_WORKERS, len(tasks))
    ) as pool:
        futures = {
            pool.submit(_translate_paragraph, p, source, target, chunk_size): i
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

    return "\n\n".join(results)


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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(300)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor(43, 43, 43))
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
        self._exclusion_items: list[QGraphicsRectItem] = []
        self._rubber_item: QGraphicsRectItem | None = None
        self._rubber_origin = None
        self._band_pen = QPen(QColor(74, 144, 217), 2, Qt.PenStyle.DashLine)
        self._band_brush = QBrush(QColor(74, 144, 217, 70))
        self._exclude_pen = QPen(QColor(217, 83, 79), 2, Qt.PenStyle.DashLine)
        self._exclude_brush = QBrush(QColor(217, 83, 79, 70))

        self._show_message_text("Apri un PDF per iniziare")

    # ── scene management ──────────────────────────────────────────────

    def _clear_scene(self):
        self._scene.clear()
        self._pix_item = None
        self._text_item = None
        self._rubber_item = None
        self._rubber_origin = None
        self._exclusion_items = []

    def _show_message_text(self, text: str):
        self._clear_scene()
        item = QGraphicsTextItem(text)
        item.setDefaultTextColor(QColor(136, 136, 136))
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
            self._show_message_text("(pagina non disponibile)")
            return
        self._full_pixmap = pixmap
        self._clear_scene()
        self._pix_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self._fit_to_view()

    def show_message(self, text: str):
        """Show a plain status message instead of a page (e.g. while loading)."""
        self._full_pixmap = None
        self._show_message_text(text)

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

    def _interactive(self) -> bool:
        return self._select_mode or self._exclude_mode

    def _rubber_style(self) -> tuple[QPen, QBrush]:
        if self._exclude_mode:
            return self._exclude_pen, self._exclude_brush
        return self._band_pen, self._band_brush

    def _clear_exclusion_overlay(self):
        for item in self._exclusion_items:
            self._scene.removeItem(item)
        self._exclusion_items = []

    def show_excluded_zones(self, zones):
        """Draw the excluded zones (scene coords) as red overlays."""
        self._clear_exclusion_overlay()
        pen = QPen(QColor(217, 83, 79), 2, Qt.PenStyle.SolidLine)
        brush = QBrush(QColor(217, 83, 79, 60))
        for x0, y0, x1, y1 in zones:
            item = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
            item.setPen(pen)
            item.setBrush(brush)
            item.setZValue(10)
            self._scene.addItem(item)
            self._exclusion_items.append(item)

    def _clear_rubber(self):
        if self._rubber_item is not None:
            self._scene.removeItem(self._rubber_item)
            self._rubber_item = None
        self._rubber_origin = None

    # ── sizing ──────────────────────────────────────────────────────────

    def _fit_to_view(self):
        """Scale the full-resolution pixmap to fit the current view size."""
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
                    else:
                        self.region_selected.emit(
                            rect.left(), rect.top(), rect.right(), rect.bottom()
                        )
            except Exception:
                self._clear_rubber()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TextPanel(QTextEdit):
    """Right panel — shows extracted text, optionally rendered as Markdown/HTML."""

    _HTML_CSS = """
    <style>
      body { font-family: 'Segoe UI', sans-serif; font-size: 13px;
             color: #1a1a1a; line-height: 1.7; margin: 0; }
      h1 { font-size: 1.5em; border-bottom: 2px solid #4a90d9; padding-bottom: 4px; }
      h2 { font-size: 1.3em; color: #2c5f8a; margin-top: 1em; }
      h3 { font-size: 1.15em; color: #3a7ab5; }
      strong { color: #1a3a5c; }
      em { color: #555; }
      code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px;
             font-family: 'Consolas', monospace; font-size: 0.9em; }
      pre { background: #f5f5f5; padding: 12px; border-radius: 6px;
            border: 1px solid #ddd; overflow-x: auto; }
      table { border-collapse: collapse; width: 100%; margin: 10px 0; }
      th { background: #4a90d9; color: #fff; padding: 8px 12px;
           text-align: left; font-weight: 600; }
      td { border: 1px solid #ddd; padding: 6px 12px; }
      tr:nth-child(even) { background: #f8f9fa; }
      blockquote { border-left: 4px solid #4a90d9; margin: 10px 0;
                   padding: 6px 16px; background: #f0f4f8; color: #444; }
      ul, ol { padding-left: 24px; }
      li { margin: 3px 0; }
      hr { border: none; border-top: 1px solid #ddd; margin: 16px 0; }
      a { color: #4a90d9; }
    </style>
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(QFont("Segoe UI", 12))
        self.setStyleSheet(
            "QTextEdit { background: #ffffff; color: #1a1a1a; padding: 12px; }"
        )

    def show_text(self, text: str, as_markdown: bool = True):
        """Display text, optionally rendering as Markdown → HTML."""
        if as_markdown:
            html_body = _md_lib.markdown(text, extensions=_MD_EXTENSIONS)
            self.setHtml(self._HTML_CSS + html_body)
        else:
            self.setPlainText(text)

    def show_html(self, html_body: str):
        """Display raw HTML with CSS styling."""
        self.setHtml(self._HTML_CSS + html_body)


class TranslateThread(QThread):
    """Background thread for Google Translate to keep UI responsive."""

    result_ready = pyqtSignal(int, str, str)  # generation_id, kind, translated_text

    def __init__(
        self,
        text: str,
        generation: int,
        kind: str = "origin",
        source: str = "en",
        target: str = "it",
    ):
        super().__init__()
        self._text = text
        self._generation = generation
        self._kind = kind
        self._source = source
        self._target = target

    def run(self):
        translated = translate_text_google(
            self._text, source=self._source, target=self._target
        )
        self.result_ready.emit(self._generation, self._kind, translated)


class TranslatablePanel(QWidget):
    """Wraps TextPanel with tabs: original, Italian translation, and images."""

    # Emitted when the user removes a captured image from the gallery.
    image_removed = pyqtSignal(str)  # file:// URI

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Tab bar ────────────────────────────────────────────────────
        self._tab_bar = QWidget()
        self._tab_bar.setFixedHeight(36)
        self._tab_bar.setStyleSheet("""
            QWidget#tabBar {
                background: #3a3a3a;
                border-bottom: 1px solid #555;
            }
        """)
        self._tab_bar.setObjectName("tabBar")

        tab_layout = QHBoxLayout(self._tab_bar)
        tab_layout.setContentsMargins(4, 2, 4, 2)
        tab_layout.setSpacing(2)

        self._btn_original = QPushButton("📄 Originale")
        self._btn_translated = QPushButton("🇮🇹 Italiano")
        self._btn_images = QPushButton("🖼️ Immagini")

        tab_style = """
            QPushButton {
                background: #444; color: #aaa;
                border: 1px solid #555; border-bottom: none;
                border-radius: 6px 6px 0 0;
                padding: 4px 16px; font-size: 13px;
            }
            QPushButton:hover { background: #555; color: #ddd; }
            QPushButton:checked {
                background: #fff; color: #1a1a1a;
                border-color: #ddd; font-weight: bold;
            }
        """
        for btn in (self._btn_original, self._btn_translated, self._btn_images):
            btn.setCheckable(True)
            btn.setFlat(True)
            btn.setStyleSheet(tab_style)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            tab_layout.addWidget(btn)

        # Spinner label for translation in progress
        self._lbl_spinner = QLabel("")
        self._lbl_spinner.setStyleSheet("color: #aaa; font-size: 13px; padding: 4px 8px;")
        tab_layout.addWidget(self._lbl_spinner)

        tab_layout.addStretch()
        self._btn_original.setChecked(True)

        # ── Original-tab toolbar: apply/reset the manual exclusions ────
        self._origin_toolbar = QWidget()
        ot = QHBoxLayout(self._origin_toolbar)
        ot.setContentsMargins(6, 4, 6, 4)
        ot.setSpacing(6)
        self._btn_apply_exclusions = QPushButton("🚫 Applica esclusioni")
        self._btn_apply_exclusions.setCheckable(True)
        self._btn_apply_exclusions.setToolTip(
            "Mostra nell'Originale la versione 'cleaned' (zone escluse applicate)"
        )
        self._btn_reset = QPushButton("↺ Reset")
        self._btn_reset.setToolTip(
            "Torna alla versione automatica dell'Originale"
        )
        tool_style = (
            "QPushButton { background: #444; color: #ddd; border: 1px solid #555;"
            " border-radius: 4px; padding: 3px 10px; font-size: 12px; }"
            "QPushButton:hover { background: #555; }"
            "QPushButton:checked { background: #c0392b; color: #fff; }"
        )
        for b in (self._btn_apply_exclusions, self._btn_reset):
            b.setStyleSheet(tool_style)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            ot.addWidget(b)
        ot.addStretch()

        # ── Text panel ─────────────────────────────────────────────────
        self.text_panel = TextPanel()

        layout.addWidget(self._tab_bar)
        layout.addWidget(self._origin_toolbar)
        layout.addWidget(self.text_panel)

        # ── Images panel (gallery of extracted figures) ────────────────
        self.images_panel = QScrollArea()
        self.images_panel.setWidgetResizable(True)
        self.images_panel.setStyleSheet(
            "QScrollArea { background: #f5f5f5; border: none; }"
        )
        self.images_panel.hide()
        layout.addWidget(self.images_panel)

        # ── State ──────────────────────────────────────────────────────
        self._original_text: str = ""
        self._cleaned_text: str = ""
        self._showing_cleaned: bool = False  # Originale shows the cleaned version?
        self._translated: dict[str, str] = {"origin": "", "cleaned": ""}
        self._render_md: bool = True
        self._page_translation_cache: dict[int, dict[str, str]] = {}
        self._current_page: int = -1
        self._images: list[str] = []  # file:// URIs of manually captured regions
        self._threads: dict[str, TranslateThread] = {}
        self._generations: dict[str, int] = {"origin": 0, "cleaned": 0}
        self._cache_file: Path | None = None  # on-disk translation cache file
        self._doc_fingerprint: str = ""  # invalidates the cache if the PDF changes

        # ── Connections ────────────────────────────────────────────────
        self._btn_original.clicked.connect(self._on_show_original)
        self._btn_translated.clicked.connect(self._on_show_translated)
        self._btn_images.clicked.connect(self._on_show_images)
        self._btn_apply_exclusions.clicked.connect(self._on_apply_exclusions_toggled)
        self._btn_reset.clicked.connect(self._on_reset_original)

        self._rebuild_images_panel()

    def show_text(
        self,
        text: str,
        cleaned_text: str | None = None,
        as_markdown: bool = True,
        page_num: int = -1,
        images: list[str] | None = None,
        apply_exclusions: bool | None = None,
    ):
        """Display the original (and, if given, the cleaned) text.

        If ``page_num`` is provided, translations are cached per page.
        ``images`` are the file:// URIs of the regions captured for the page.
        ``apply_exclusions``: True → show the cleaned version now (e.g. after
        drawing a zone); False → revert to the automatic version; None → keep
        the current choice.
        """
        self._render_md = as_markdown
        self._original_text = text
        self._cleaned_text = cleaned_text if cleaned_text is not None else text
        self._current_page = page_num
        self._images = list(images or [])
        self._rebuild_images_panel()

        if apply_exclusions is not None:
            self._showing_cleaned = apply_exclusions
            self._btn_apply_exclusions.setChecked(apply_exclusions)

        if self._btn_images.isChecked():
            return  # images tab active — gallery already rebuilt above

        if self._btn_translated.isChecked():
            self._maybe_show_translation(self._current_kind())
            return

        # Original tab is active (default)
        self._set_active_tab(self._btn_original)
        self.text_panel.show_text(self._current_source(), as_markdown=as_markdown)
        self._lbl_spinner.setText("")

    def _current_kind(self) -> str:
        return "cleaned" if self._showing_cleaned else "origin"

    def _current_source(self) -> str:
        return self._cleaned_text if self._showing_cleaned else self._original_text

    def _set_active_tab(self, active):
        for btn in (self._btn_original, self._btn_translated, self._btn_images):
            btn.setChecked(btn is active)
        if active is self._btn_images:
            self._origin_toolbar.hide()
            self.text_panel.hide()
            self.images_panel.show()
            self._rebuild_images_panel()
        else:
            self.images_panel.hide()
            self.text_panel.show()
            self._origin_toolbar.setVisible(active is self._btn_original)

    def _on_show_original(self):
        self._set_active_tab(self._btn_original)
        self.text_panel.show_text(self._current_source(), as_markdown=self._render_md)
        self._lbl_spinner.setText("")

    def _on_apply_exclusions_toggled(self, checked: bool):
        self._showing_cleaned = checked
        if self._btn_original.isChecked():
            self.text_panel.show_text(self._current_source(), as_markdown=self._render_md)

    def _on_reset_original(self):
        self._showing_cleaned = False
        self._btn_apply_exclusions.setChecked(False)
        if self._btn_original.isChecked():
            self.text_panel.show_text(self._original_text, as_markdown=self._render_md)

    def _on_show_translated(self):
        self._set_active_tab(self._btn_translated)
        self._maybe_show_translation(self._current_kind())

    def _on_show_images(self):
        self._set_active_tab(self._btn_images)
        self._lbl_spinner.setText("")

    def _maybe_show_translation(self, kind: str):
        """Show the cached translation for ``kind`` or start a new one."""
        cached = self._page_translation_cache.get(self._current_page, {})
        if self._current_page >= 0 and kind in cached:
            self._translated[kind] = cached[kind]
            self.text_panel.show_text(cached[kind], as_markdown=self._render_md)
            self._lbl_spinner.setText("")
        else:
            self._start_translation(kind)

    def show_images(self, images: list[str]):
        """Set the current page's captured regions and activate the gallery tab."""
        self._images = list(images or [])
        self._rebuild_images_panel()
        self._set_active_tab(self._btn_images)

    # ── Images gallery ─────────────────────────────────────────────────

    def _rebuild_images_panel(self):
        """Rebuild the gallery from the current page's figure URIs."""
        container = QWidget()
        lay = QVBoxLayout(container)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        if not self._images:
            hint = QLabel(
                "Nessuna zona catturata.\n\n"
                "Usa 🖱️ Seleziona zona per ritagliare una figura dalla pagina."
            )
            hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
            hint.setWordWrap(True)
            hint.setStyleSheet("color: #888; font-size: 14px; padding: 24px;")
            lay.addWidget(hint)
        else:
            for uri in self._images:
                lay.addWidget(self._make_image_card(uri))
        lay.addStretch()

        old = self.images_panel.takeWidget()
        if old is not None:
            old.deleteLater()
        self.images_panel.setWidget(container)

    def _make_image_card(self, uri: str) -> QWidget:
        path = str(QUrl(uri).toLocalFile())
        card = QWidget()
        card.setObjectName("imgCard")
        card.setStyleSheet(
            "QWidget#imgCard { background: #fff; border: 1px solid #ddd;"
            " border-radius: 6px; }"
        )
        v = QVBoxLayout(card)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(6)

        pix = QPixmap(path)
        thumb = QLabel()
        thumb.setPixmap(
            pix.scaledToWidth(340, Qt.TransformationMode.SmoothTransformation)
        )
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setCursor(Qt.CursorShape.PointingHandCursor)
        thumb.setToolTip("Clicca per ingrandire")
        thumb.mousePressEvent = lambda _e, u=uri: self._show_image_full(u)
        v.addWidget(thumb)

        info = QLabel(f"{Path(path).name}  ·  {pix.width()}×{pix.height()} px")
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("border: none; color: #555; font-size: 12px;")
        v.addWidget(info)

        row = QHBoxLayout()
        btn_style = (
            "QPushButton { background: #4a90d9; color: #fff; border: none;"
            " border-radius: 4px; padding: 6px 12px; font-size: 12px; }"
            "QPushButton:hover { background: #3a7ab5; }"
        )
        btn_save = QPushButton("💾 Salva")
        btn_save.clicked.connect(lambda _=False, u=uri: self._save_image(u))
        btn_copy = QPushButton("📋 Copia")
        btn_copy.clicked.connect(lambda _=False, u=uri: self._copy_image(u))
        for b in (btn_save, btn_copy):
            b.setStyleSheet(btn_style)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            row.addWidget(b)
        btn_remove = QPushButton("🗑️ Rimuovi")
        btn_remove.clicked.connect(lambda _=False, u=uri: self._remove_image(u))
        btn_remove.setStyleSheet(
            "QPushButton { background: #d9534f; color: #fff; border: none;"
            " border-radius: 4px; padding: 6px 12px; font-size: 12px; }"
            "QPushButton:hover { background: #c9302c; }"
        )
        btn_remove.setCursor(Qt.CursorShape.PointingHandCursor)
        row.addWidget(btn_remove)
        v.addLayout(row)
        return card

    def _show_image_full(self, uri: str):
        path = str(QUrl(uri).toLocalFile())
        dlg = QDialog(self)
        dlg.setWindowTitle(Path(path).name)
        dlg.resize(900, 720)
        lay = QVBoxLayout(dlg)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        lbl = QLabel()
        lbl.setPixmap(QPixmap(path))
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(lbl)
        lay.addWidget(scroll)
        dlg.exec()

    def _copy_image(self, uri: str):
        img = QImage(QUrl(uri).toLocalFile())
        if not img.isNull():
            QApplication.clipboard().setImage(img)
            self._lbl_spinner.setText("✅ Copiata")

    def _save_image(self, uri: str):
        src = str(QUrl(uri).toLocalFile())
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Salva immagine",
            Path(src).name,
            "PNG (*.png);;JPEG (*.jpg);;Tutti i file (*)",
        )
        if dest:
            shutil.copyfile(src, dest)
            self._lbl_spinner.setText("✅ Salvata")

    def _remove_image(self, uri: str):
        """Ask the main window to drop a captured image from the gallery."""
        self.image_removed.emit(uri)

    def _start_translation(self, kind: str):
        """Fire a background translation for ``kind`` ('origin' or 'cleaned')."""
        text = self._original_text if kind == "origin" else self._cleaned_text
        self._lbl_spinner.setText("⏳ Traducendo...")
        self._generations[kind] += 1

        old = self._threads.get(kind)
        if old is not None:
            try:
                old.result_ready.disconnect()
            except TypeError:
                pass  # already disconnected

        thread = TranslateThread(
            text, generation=self._generations[kind], kind=kind,
            source="en", target="it",
        )
        thread.result_ready.connect(self._on_translation_done)
        self._threads[kind] = thread
        thread.start()

    def _on_translation_done(self, generation: int, kind: str, translated: str):
        """Slot: background translation finished."""
        # Ignore stale results from superseded requests
        if generation != self._generations.get(kind):
            return

        self._translated[kind] = translated

        # Cache per page
        if self._current_page >= 0:
            self._page_translation_cache.setdefault(self._current_page, {})[kind] = translated
            self._save_disk_cache()

        # Show if the Italiano tab is active and this is the current source
        if self._btn_translated.isChecked() and kind == self._current_kind():
            self.text_panel.show_text(translated, as_markdown=self._render_md)

        self._lbl_spinner.setText("✅")

    def show_html(self, html_body: str):
        """Forward to inner TextPanel."""
        self.text_panel.setHtml(self.text_panel._HTML_CSS + html_body)

    # ── persistent translation cache ───────────────────────────────────

    def set_document(self, path: Path | None):
        """Point the translation cache at this PDF and load saved translations."""
        self._page_translation_cache.clear()
        self._cache_file = None
        self._doc_fingerprint = ""
        if path is None:
            return
        try:
            st = path.stat()
            self._doc_fingerprint = f"{st.st_size}-{st.st_mtime_ns}"
        except Exception:
            return
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppDataLocation
        )
        if not base:
            base = str(Path.home() / ".noesis-pdf-reader")
        cache_dir = Path(base) / "translation"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        self._cache_file = cache_dir / f"{path.stem}.json"
        self._load_disk_cache()

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
                # backward-compatible: old cache stored only the origin text
                self._page_translation_cache.setdefault(page, {})["origin"] = value
            elif isinstance(value, dict):
                entry: dict[str, str] = {}
                for kind in ("origin", "cleaned"):
                    if isinstance(value.get(kind), str):
                        entry[kind] = value[kind]
                if entry:
                    self._page_translation_cache[page] = entry

    def _save_disk_cache(self):
        """Persist the in-memory translation cache to disk."""
        if self._cache_file is None:
            return
        payload = {
            "fingerprint": self._doc_fingerprint,
            "pages": {str(k): dict(v) for k, v in self._page_translation_cache.items()},
        }
        try:
            self._cache_file.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    def invalidate_cache(self):
        """Clear per-page translation cache."""
        self._page_translation_cache.clear()

    def invalidate_page(self, page_num: int):
        """Drop the cached translation for one page (its content changed)."""
        self._page_translation_cache.pop(page_num, None)
        self._save_disk_cache()

    def shutdown(self):
        """Wait for any in-flight translation before the app closes."""
        for thread in self._threads.values():
            if thread is not None and thread.isRunning():
                thread.wait(5000)
        self._threads.clear()
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
        self.tree.setStyleSheet(
            "QTreeWidget { background: #2b2b2b; color: #ddd; border: none;"
            " font-size: 13px; }"
            "QTreeWidget::item { padding: 2px 0; }"
            "QTreeWidget::item:selected { background: #3a6bc5; color: #fff; }"
        )
        layout.addWidget(self.tree)

        self._items: list[QTreeWidgetItem] = []  # flat, in document order
        self._syncing: bool = False

        self.tree.itemClicked.connect(self._on_item_clicked)

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
            title = (title or "").strip() or "(senza titolo)"
            item = QTreeWidgetItem([f"{title}  ·  p. {page_idx + 1}"])
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

        # State
        self._pdf_path: Path | None = None
        self._current_page: int = 0
        self._page_count: int = 0
        self._mupdf_doc = None       # pymupdf Document (render + layout + images)
        self._images_dir: Path | None = None  # dir for extracted figures
        self._current_images: list[str] = []  # captured regions, kept until the book closes
        self._excluded_zones: dict[int, list[tuple]] = {}  # page → excluded PDF rects
        self._render_scale: float = 3.0
        self._render_md: bool = True  # toggle Markdown rendering

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
        self.toc_dock = QDockWidget("Indice", self)
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

        self.splitter.addWidget(left_panel)
        self.splitter.addWidget(self.text_panel)
        self.splitter.setSizes([700, 700])

        root_layout.addWidget(self.splitter)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(
            "Pronto — apri un file PDF con 📂 Apri PDF  |  "
            "Backend testo: PyMuPDF4LLM ⚡"
        )

        # Shortcuts
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._next_page)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self._prev_page)
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self._next_page)
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self._prev_page)
        QShortcut(QKeySequence.StandardKey.ZoomIn, self, self._zoom_in)
        QShortcut(QKeySequence.StandardKey.ZoomOut, self, self._zoom_out)
        QShortcut(
            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_0), self, self._zoom_reset
        )
        QShortcut(
            QKeySequence(Qt.Modifier.CTRL | Qt.Key.Key_M), self, self._toggle_markdown
        )

        # Dark theme
        self.setStyleSheet("""
            QMainWindow { background: #2b2b2b; }
            QToolBar {
                background: #333; padding: 4px; spacing: 6px;
                border-bottom: 1px solid #444;
            }
            QToolBar QPushButton {
                background: #444; color: #eee; border: 1px solid #555;
                border-radius: 4px; padding: 6px 14px; font-size: 13px;
            }
            QToolBar QPushButton:hover { background: #555; }
            QToolBar QPushButton:pressed { background: #666; }
            QToolBar QPushButton:checked { background: #3a6bc5; color: #fff; }
            QToolBar QSpinBox {
                background: #444; color: #eee; border: 1px solid #555;
                border-radius: 4px; padding: 4px 8px; font-size: 13px;
                min-width: 60px;
            }
            QToolBar QLabel { color: #ccc; font-size: 13px; }
            QStatusBar { background: #333; color: #aaa; }
        """)

        # L'apertura del PDF è gestita in main(): argomento da riga di comando
        # oppure harrison2025.pdf nella directory corrente.

    # ── toolbar ───────────────────────────────────────────────────────────

    def _build_toolbar(self, parent_layout: QVBoxLayout):
        bar = QToolBar("Navigazione")
        bar.setMovable(False)
        parent_layout.addWidget(bar)

        # Apri
        btn_open = QPushButton("📂 Apri PDF")
        btn_open.clicked.connect(self._on_open)
        bar.addWidget(btn_open)

        # TOC toggle
        self.btn_toc = QPushButton("📑 Indice")
        self.btn_toc.setCheckable(True)
        self.btn_toc.setChecked(True)
        self.btn_toc.setToolTip("Mostra/nascondi l'indice (TOC) del PDF")
        self.btn_toc.clicked.connect(
            lambda checked: self.toc_dock.setVisible(checked)
        )
        bar.addWidget(self.btn_toc)

        bar.addSeparator()

        # Prev
        self.btn_prev = QPushButton("◀ Prec.")
        self.btn_prev.clicked.connect(self._prev_page)
        bar.addWidget(self.btn_prev)

        # Page spin
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setValue(1)
        # Keyboard tracking off: with it on, every keystroke committed a value
        # and fired valueChanged -> _set_page -> full page render + text
        # extraction (Docling ~2-6s), freezing the box while typing. Now the
        # page changes only on Enter / focus-out (arrows still work instantly).
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(self._on_spin)
        self.page_spin.setEnabled(False)
        bar.addWidget(self.page_spin)

        bar.addWidget(QLabel("di"))
        self.lbl_total = QLabel("0")
        bar.addWidget(self.lbl_total)

        # Next
        self.btn_next = QPushButton("Succ. ▶")
        self.btn_next.clicked.connect(self._next_page)
        bar.addWidget(self.btn_next)

        bar.addSeparator()

        # Zoom
        self.btn_zoom_out = QPushButton("🔍−")
        self.btn_zoom_out.setToolTip("Riduci zoom (Ctrl+-)")
        self.btn_zoom_out.clicked.connect(self._zoom_out)
        bar.addWidget(self.btn_zoom_out)

        self.zoom_label = QLabel("Scala: 3.0x")
        bar.addWidget(self.zoom_label)

        self.btn_zoom_in = QPushButton("🔍+")
        self.btn_zoom_in.setToolTip("Aumenta zoom (Ctrl++)")
        self.btn_zoom_in.clicked.connect(self._zoom_in)
        bar.addWidget(self.btn_zoom_in)

        bar.addSeparator()

        bar.addSeparator()

        # Markdown rendering toggle
        self.btn_md_toggle = QPushButton("📝 MD ✓")
        self.btn_md_toggle.setToolTip(
            "Attiva/disattiva rendering Markdown → HTML\n"
            "(Ctrl+M per toggle)"
        )
        self.btn_md_toggle.setCheckable(True)
        self.btn_md_toggle.setChecked(True)
        self.btn_md_toggle.clicked.connect(self._toggle_markdown)
        bar.addWidget(self.btn_md_toggle)

    def _build_page_toolbar(self):
        """Mini toolbar shown above the PDF page viewer (left panel)."""
        bar = QToolBar("Pagina")
        bar.setMovable(False)

        # Region selection (extract the image under a mouse-drawn rectangle)
        self.btn_select_region = QPushButton("🖱️ Seleziona zona")
        self.btn_select_region.setCheckable(True)
        self.btn_select_region.setToolTip(
            "Trascina col mouse una zona della pagina\n"
            "per estrarne l'immagine nella tab 🖼️ Immagini"
        )
        self.btn_select_region.clicked.connect(self._on_select_region_toggled)
        bar.addWidget(self.btn_select_region)

        # Zone exclusion (manual cleaning fed to the adaptive engine)
        self.btn_exclude = QPushButton("🚫 Escludi zona")
        self.btn_exclude.setCheckable(True)
        self.btn_exclude.setToolTip(
            "Trascina col mouse una zona (header, footer, immagine,\n"
            "didascalia…) per escluderla: il motore adattativo riordina\n"
            "il testo rimanente. È aggiuntivo al sistema automatico.\n\n"
            "Se la zona contiene un'immagine, viene estratta anche nella\n"
            "tab 🖼️ Immagini (escludi + estrai in un solo gesto)."
        )
        self.btn_exclude.clicked.connect(self._on_exclude_toggled)
        bar.addWidget(self.btn_exclude)

        self.btn_reset_exclusions = QPushButton("🧹 Reset esclusioni")
        self.btn_reset_exclusions.setToolTip(
            "Rimuove tutte le zone escluse dalla pagina corrente"
        )
        self.btn_reset_exclusions.clicked.connect(self._on_reset_exclusions)
        bar.addWidget(self.btn_reset_exclusions)
        return bar

    def _display_text(
        self,
        text: str,
        page_num: int = -1,
        images: list[str] | None = None,
        cleaned_text: str | None = None,
        apply_exclusions: bool | None = None,
    ):
        """Display the original text (and, if given, the cleaned text)."""
        if images is None:
            images = self._current_images
        self.text_panel.show_text(
            text, cleaned_text=cleaned_text, as_markdown=self._render_md,
            page_num=page_num, images=images, apply_exclusions=apply_exclusions,
        )

    def _extract_and_display(self, page_num: int, apply_exclusions: bool | None = None) -> float:
        """Extract origin + cleaned texts and display them; returns elapsed ms."""
        if not self._pdf_path:
            return 0.0
        origin, cleaned, elapsed = self._extract_text(page_num)
        self._display_text(
            self._extraction_header(origin, elapsed, "Engine adattativo") + origin,
            page_num=page_num,
            cleaned_text=self._extraction_header(cleaned, elapsed, "Pulito manualmente") + cleaned,
            apply_exclusions=apply_exclusions,
        )
        return elapsed

    def _toggle_markdown(self):
        """Toggle Markdown rendering on/off and refresh display."""
        self._render_md = not self._render_md
        if self._render_md:
            self.btn_md_toggle.setText("📝 MD ✓")
        else:
            self.btn_md_toggle.setText("📝 Plain")
        # Re-render current text
        if self._mupdf_doc is not None and self._page_count > 0 and self._pdf_path:
            self._extract_and_display(self._current_page)

    # ── text extraction backends ──────────────────────────────────────────

    def _extract_text(self, page_num: int) -> tuple[str, str, float]:
        """Extract text with PyMuPDF4LLM + adaptive engine (always on).

        Returns ``(origin, cleaned, elapsed)``: ``origin`` is the automatic
        engine output; ``cleaned`` is the same page rebuilt after the manually
        excluded zones (identical to ``origin`` when nothing is excluded).
        """
        t0 = time.perf_counter()
        raw = self._extract_pymupdf4llm(page_num)
        exclude = tuple(self._excluded_zones.get(page_num, ()))
        origin = self._apply_engine(raw, page_num)
        cleaned = (
            self._apply_engine(raw, page_num, exclude=exclude)
            if exclude else origin
        )
        elapsed = time.perf_counter() - t0
        return origin, cleaned, elapsed

    # ── Nuovi backend Markdown ────────────────────────────────────────

    def _extract_pymupdf4llm(self, page_num: int) -> str:
        """PyMuPDF4LLM: blazing fast, native Markdown with tables."""
        if not _has_pymupdf4llm:
            return "(pymupdf4llm non installato — esegui: pip install pymupdf4llm)"
        try:
            md = pymupdf4llm.to_markdown(str(self._pdf_path), pages=[page_num])
            return md.strip() or "(nessun testo estraibile su questa pagina)"
        except Exception as e:
            return f"(errore pymupdf4llm: {e})"

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
        """Extract the user-selected page region and show it in the gallery."""
        self._set_select_mode(False)
        scale = self._render_scale or 1.0
        clip = (x0 / scale, y0 / scale, x1 / scale, y1 / scale)
        uri = self._extract_image_region(self._current_page, clip)
        if uri is None:
            self.status_bar.showMessage(
                "Nessuna immagine estraibile dalla zona selezionata"
            )
            return
        self._current_images.append(uri)  # new captures accumulate in the gallery
        self.text_panel.show_images(self._current_images)
        name = Path(QUrl(uri).toLocalFile()).name
        self.status_bar.showMessage(f"Immagine estratta dalla zona: {name}")

    def _on_image_removed(self, uri: str):
        """Drop a captured image from the gallery and delete its file."""
        if uri in self._current_images:
            self._current_images.remove(uri)
        try:
            Path(QUrl(uri).toLocalFile()).unlink(missing_ok=True)
        except Exception:
            pass
        self.text_panel.show_images(self._current_images)

    def _on_select_region_toggled(self, checked: bool):
        """Enable/disable rubber-band selection on the left panel."""
        self._set_select_mode(checked)

    def _set_select_mode(self, enabled: bool):
        """Update the select-zone toggle (and turn off exclude mode)."""
        self.btn_select_region.setChecked(enabled)
        self.pdf_view.set_select_mode(enabled)
        if enabled:
            self.btn_exclude.setChecked(False)
            self.pdf_view.set_exclude_mode(False)

    def _on_exclude_toggled(self, checked: bool):
        """Enable/disable rubber-band zone exclusion."""
        self._set_exclude_mode(checked)

    def _set_exclude_mode(self, enabled: bool):
        """Update the exclude-zone toggle (and turn off select mode)."""
        self.btn_exclude.setChecked(enabled)
        self.pdf_view.set_exclude_mode(enabled)
        if enabled:
            self.btn_select_region.setChecked(False)
            self.pdf_view.set_select_mode(False)

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
        self._refresh_current_page_text(apply_exclusions=True)

        msg = (
            f"Zona esclusa ({len(zones)} sulla pagina) — trascina altre zone "
            "o premi 🚫 Escludi zona per terminare"
        )
        uri = self._extract_image_region(self._current_page, rect, embedded_only=True)
        if uri is not None:
            self._current_images.append(uri)
            self.text_panel.show_images(self._current_images)
            name = Path(QUrl(uri).toLocalFile()).name
            msg = (
                f"Zona esclusa e immagine estratta ({name}) — trascina altre "
                "zone o premi 🚫 Escludi zona per terminare"
            )
        self.status_bar.showMessage(msg)

    def _on_reset_exclusions(self):
        """Remove all excluded zones for the current page."""
        self._excluded_zones.pop(self._current_page, None)
        self.pdf_view.show_excluded_zones([])
        self.text_panel.invalidate_page(self._current_page)
        self._refresh_current_page_text(apply_exclusions=False)
        self.status_bar.showMessage("Esclusioni rimosse per questa pagina")

    def _scene_exclusions(self, page_num: int) -> list[tuple]:
        """Convert the page's excluded zones (PDF points) to scene pixels."""
        scale = self._render_scale or 1.0
        return [
            tuple(v * scale for v in r)
            for r in self._excluded_zones.get(page_num, [])
        ]

    def _refresh_current_page_text(self, apply_exclusions: bool | None = None):
        """Re-extract and re-display the current page's text."""
        if not self._pdf_path or self._mupdf_doc is None or self._page_count == 0:
            return
        self._extract_and_display(self._current_page, apply_exclusions=apply_exclusions)

    def _get_images_dir(self) -> Path:
        """Return (creating on first use) the per-document figures directory."""
        if self._images_dir is None:
            base = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation
            )
            if not base:
                base = str(Path.home() / ".noesis-pdf-reader")
            self._images_dir = Path(base) / "images" / self._pdf_path.stem
        self._images_dir.mkdir(parents=True, exist_ok=True)
        return self._images_dir

    # ── navigation ────────────────────────────────────────────────────────

    def _set_page(self, page_num: int):
        if self._mupdf_doc is None or self._page_count == 0:
            return
        count = self._page_count
        page_num = max(0, min(page_num, count - 1))
        self._current_page = page_num

        # Render left
        self._display_page(page_num)

        # Extract text right
        elapsed = 0.0
        if self._pdf_path:
            elapsed = self._extract_and_display(page_num, apply_exclusions=False)

        # Update toolbar
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(page_num + 1)
        self.page_spin.blockSignals(False)

        self.status_bar.showMessage(
            f"Pagina {page_num + 1} di {count}  —  {self._pdf_path.name}"
            f"  |  Testo: PyMuPDF4LLM ⚡ ({elapsed*1000:.0f} ms)"
        )

        # Sync TOC highlight
        self.toc_panel.select_page(page_num)

    def _next_page(self):
        self._set_page(self._current_page + 1)

    def _prev_page(self):
        self._set_page(self._current_page - 1)

    def _on_spin(self, val: int):
        self._set_page(val - 1)

    def _goto_toc_page(self, page_idx: int):
        """Navigate to a page selected from the TOC."""
        self._set_page(page_idx)

    def _extraction_header(self, text: str, elapsed: float, label: str = "Engine adattativo") -> str:
        """Build the header line shown above the extracted text."""
        return (
            f"── Backend: PyMuPDF4LLM ⚡"
            f"  │  {elapsed*1000:.1f} ms"
            f"  │  {len(text)} caratteri"
            f"  │  Fix: {label} ──\n\n"
        )

    def _apply_engine(self, text: str, page_num: int, exclude: tuple = ()) -> str:
        """Apply the adaptive fix engine (layout_engine.py) to the page.

        Profilo → Piano → Pipeline. When ``exclude`` is non-empty, the page is
        rebuilt skipping those zones (manual cleaning) before the cosmetic
        fixes; otherwise the automatic plan is applied unchanged.
        """
        doc = self._get_mupdf_doc()
        if doc is None:
            return text
        try:
            page = doc[page_num]
            profile = layout_engine.profile_page(page, exclude=exclude)
            plan = layout_engine.plan_fixes(profile, "PyMuPDF4LLM ⚡", mode="auto")
            if exclude:
                cleaned = _column_aware_markdown(page, exclude=exclude) or text
                plan = [f for f in plan if f.id != "reorder_columns"]
                return layout_engine.apply_plan(cleaned, page, profile, plan) or cleaned
            return layout_engine.apply_plan(text, page, profile, plan) or text
        except Exception:
            return text

    # ── zoom ───────────────────────────────────────────────────────────────

    def _zoom_in(self):
        self._render_scale = min(4.0, self._render_scale + 0.25)
        self._update_zoom()

    def _zoom_out(self):
        self._render_scale = max(0.5, self._render_scale - 0.25)
        self._update_zoom()

    def _zoom_reset(self):
        self._render_scale = 3.0
        self._update_zoom()

    def _update_zoom(self):
        self.zoom_label.setText(f"Scala: {self._render_scale:.2f}x")
        if self._mupdf_doc is not None and self._page_count > 0:
            self._display_page(self._current_page)

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
            return
        if not _has_pymupdf:
            self.pdf_view.show_message("(pymupdf non installato)")
        else:
            self.pdf_view.show_message("(pagina non disponibile)")

    # ── file open ─────────────────────────────────────────────────────────

    def _on_open(self):
        path_str, _ = QFileDialog.getOpenFileName(
            self, "Apri PDF", "", "PDF Files (*.pdf);;All Files (*)"
        )
        if path_str:
            self._open_pdf(Path(path_str))

    def _open_pdf(self, path: Path):
        if not path.exists():
            QMessageBox.warning(self, "Errore", f"File non trovato:\n{path}")
            return

        if self._mupdf_doc is not None:
            self._mupdf_doc.close()
            self._mupdf_doc = None

        try:
            self._mupdf_doc = pymupdf.open(str(path))
            self._pdf_path = path
            self._page_count = len(self._mupdf_doc)

            self._images_dir = None
            self._current_images = []
            self._excluded_zones = {}
            self.text_panel.set_document(path)

            self.page_spin.setEnabled(True)
            self.page_spin.setMaximum(max(self._page_count, 1))
            self.lbl_total.setText(str(self._page_count))

            # Build the multi-level table of contents
            self.toc_panel.build_toc(self._mupdf_doc)

            if self._page_count > 0:
                self._set_page(0)
            else:
                self.pdf_view.show_page(None)
                self._display_text("(PDF vuoto)")
                self.status_bar.showMessage("PDF senza pagine")
        except Exception as e:
            QMessageBox.critical(self, "Errore PDF", f"Impossibile aprire il PDF:\n{e}")
            self._mupdf_doc = None
            self._pdf_path = None
            self._page_count = 0

    def closeEvent(self, event):
        if self._mupdf_doc is not None:
            self._mupdf_doc.close()
            self._mupdf_doc = None
        self.text_panel.shutdown()
        super().closeEvent(event)


# ═══════════════════════════════════════════════════════════════════════════════
#  entry point
# ═══════════════════════════════════════════════════════════════════════════════


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("noesis-pdf-reader-lite")
    window = MainWindow()
    window.show()

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
