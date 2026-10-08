#!/usr/bin/env python3
"""Rappresentazione logica di pagina — schema **ispirato a DoclingDocument**.

Replica (sottoinsieme fedele) del modello `docling_core.types.doc` + le nostre
aggiunte per l'indipendenza e il debug:

- `source`      : chi ha prodotto il nodo (``geometry``/``gnn``/``heuristic``/``oracle``)
- `confidence`  : affidabilità del nodo
- `flags`       : proprietà notevoli (``spanning``, ``raster``, ``empty``, …)
- `geometry`    : lo **scheletro geometrico** puro (gutters, colonne, bande) — è
  il livello *indipendente dal GNN* su cui si scriveranno gli invarianti.

Docling è MIT ma **non è una dipendenza**: ne replichiamo lo schema. Tutto
deterministico, offline, senza nuove librerie (solo ``dataclasses``/``enum``/
``pymupdf``).

Uso come libreria::

    from page_model import build_page_document
    docmodel = build_page_document("corpus1/fe22.pdf", 1037)
    print(docmodel.to_json(indent=2))
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Enum (nomi allineati a Docling)
# ─────────────────────────────────────────────────────────────────────────────


class DocItemLabel(str, Enum):
    CAPTION = "caption"
    CHART = "chart"
    CHECKBOX_SELECTED = "checkbox_selected"
    CHECKBOX_UNSELECTED = "checkbox_unselected"
    CODE = "code"
    DOCUMENT_INDEX = "document_index"
    EMPTY_VALUE = "empty_value"
    FIELD_HEADING = "field_heading"
    FIELD_HINT = "field_hint"
    FIELD_ITEM = "field_item"
    FIELD_KEY = "field_key"
    FIELD_REGION = "field_region"
    FIELD_VALUE = "field_value"
    FOOTNOTE = "footnote"
    FORM = "form"
    FORMULA = "formula"
    GRADING_SCALE = "grading_scale"
    HANDWRITTEN_TEXT = "handwritten_text"
    KEY_VALUE_REGION = "key_value_region"
    LIST_ITEM = "list_item"
    MARKER = "marker"
    PAGE_FOOTER = "page_footer"
    PAGE_HEADER = "page_header"
    PARAGRAPH = "paragraph"
    PICTURE = "picture"
    REFERENCE = "reference"
    SECTION_HEADER = "section_header"
    TABLE = "table"
    TEXT = "text"
    TITLE = "title"


class GroupLabel(str, Enum):
    CHAPTER = "chapter"
    COMMENT_SECTION = "comment_section"
    FORM_AREA = "form_area"
    INLINE = "inline"
    KEY_VALUE_AREA = "key_value_area"
    LIST = "list"
    ORDERED_LIST = "ordered_list"
    PICTURE_AREA = "picture_area"
    SECTION = "section"
    SHEET = "sheet"
    SLIDE = "slide"
    UNSPECIFIED = "unspecified"


class TableCellLabel(str, Enum):
    BODY = "body"
    COLUMN_HEADER = "column_header"
    ROW_HEADER = "row_header"
    ROW_SECTION = "row_section"


class ContentLayer(str, Enum):
    BODY = "body"
    FURNITURE = "furniture"
    BACKGROUND = "background"


class CoordOrigin(str, Enum):
    TOPLEFT = "TOPLEFT"
    BOTTOMLEFT = "BOTTOMLEFT"


class ImageRefMode(str, Enum):
    EMBEDDED = "embedded"
    PLACEHOLDER = "placeholder"
    REFERENCED = "referenced"


# ── nostre aggiunte ──────────────────────────────────────────────────────────
class NodeSource(str, Enum):
    GEOMETRY = "geometry"      # regola geometrica pura (PyMuPDF)
    GNN = "gnn"                # content map / modello PyMuPDF4LLM
    HEURISTIC = "heuristic"    # euristica (font, posizione)
    ORACLE = "oracle"          # oracolo indipendente (DocLayout, dev-only)
    MIXED = "mixed"


# ─────────────────────────────────────────────────────────────────────────────
# Tipi base
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class BoundingBox:
    l: float = 0.0
    t: float = 0.0
    r: float = 0.0
    b: float = 0.0
    coord_origin: CoordOrigin = CoordOrigin.TOPLEFT

    @property
    def width(self) -> float:
        return self.r - self.l

    @property
    def height(self) -> float:
        return self.b - self.t

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def as_tuple(self) -> tuple:
        return (self.l, self.t, self.r, self.b)

    def normalized(self, size: "Size") -> "BoundingBox":
        w = size.width or 1.0
        h = size.height or 1.0
        return BoundingBox(self.l / w, self.t / h, self.r / w, self.b / h,
                           self.coord_origin)

    @staticmethod
    def from_tuple(bb: tuple, coord_origin: CoordOrigin = CoordOrigin.TOPLEFT):
        return BoundingBox(bb[0], bb[1], bb[2], bb[3], coord_origin)

    def overlaps(self, other: "BoundingBox") -> bool:
        return (min(self.r, other.r) - max(self.l, other.l) > 0
                and min(self.b, other.b) - max(self.t, other.t) > 0)

    def intersection_over_union(self, other: "BoundingBox") -> float:
        ix = max(0.0, min(self.r, other.r) - max(self.l, other.l))
        iy = max(0.0, min(self.b, other.b) - max(self.t, other.t))
        inter = ix * iy
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def intersection_over_self(self, other: "BoundingBox") -> float:
        ix = max(0.0, min(self.r, other.r) - max(self.l, other.l))
        iy = max(0.0, min(self.b, other.b) - max(self.t, other.t))
        inter = ix * iy
        return inter / self.area if self.area > 0 else 0.0


@dataclass
class Size:
    width: float = 0.0
    height: float = 0.0

    def as_tuple(self) -> tuple:
        return (self.width, self.height)


@dataclass
class ProvenanceItem:
    page_no: int = 0
    bbox: Optional[BoundingBox] = None
    charspan: tuple = (0, 0)


@dataclass
class RefItem:
    cref: str = ""


@dataclass
class ImageRef:
    mimetype: str = "image/png"
    dpi: int = 0
    size: Optional[Size] = None
    uri: str = ""
    mode: ImageRefMode = ImageRefMode.PLACEHOLDER


@dataclass
class DocumentOrigin:
    mimetype: str = "application/pdf"
    binary_hash: str = ""
    filename: str = ""
    uri: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Nodi
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DocItem:
    self_ref: str = ""
    label: DocItemLabel = DocItemLabel.TEXT
    parent: Optional[RefItem] = None
    children: list = field(default_factory=list)
    content_layer: ContentLayer = ContentLayer.BODY
    prov: list = field(default_factory=list)
    # ── nostre aggiunte ──
    source: NodeSource = NodeSource.GEOMETRY
    confidence: float = 1.0
    flags: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    # ── Docling ──
    comments: list = field(default_factory=list)

    @property
    def bbox(self) -> Optional[BoundingBox]:
        return self.prov[0].bbox if self.prov else None


@dataclass
class TextItem(DocItem):
    text: str = ""
    orig: str = ""
    formatting: Optional[dict] = None
    hyperlink: Optional[str] = None


@dataclass
class SectionHeaderItem(TextItem):
    label: DocItemLabel = DocItemLabel.SECTION_HEADER
    level: int = 1


@dataclass
class TitleItem(TextItem):
    label: DocItemLabel = DocItemLabel.TITLE


@dataclass
class ListItem(TextItem):
    label: DocItemLabel = DocItemLabel.LIST_ITEM
    enumerated: bool = False
    marker: str = ""


@dataclass
class CodeItem(TextItem):
    label: DocItemLabel = DocItemLabel.CODE


@dataclass
class FormulaItem(DocItem):
    label: DocItemLabel = DocItemLabel.FORMULA
    text: str = ""
    orig: str = ""


@dataclass
class CheckboxItem(DocItem):
    label: DocItemLabel = DocItemLabel.CHECKBOX_UNSELECTED
    checked: bool = False


@dataclass
class ReferenceItem(DocItem):
    label: DocItemLabel = DocItemLabel.REFERENCE
    text: str = ""


@dataclass
class GroupItem(DocItem):
    label: DocItemLabel = DocItemLabel.TEXT  # Docling: GroupItem uses GroupLabel
    group_label: GroupLabel = GroupLabel.UNSPECIFIED
    name: str = ""


@dataclass
class FloatingItem(DocItem):
    captions: list = field(default_factory=list)
    footnotes: list = field(default_factory=list)
    references: list = field(default_factory=list)
    image: Optional[ImageRef] = None
    caption_text: str = ""


@dataclass
class PictureItem(FloatingItem):
    label: DocItemLabel = DocItemLabel.PICTURE
    annotations: list = field(default_factory=list)


@dataclass
class TableCell:
    bbox: Optional[BoundingBox] = None
    row_span: int = 1
    col_span: int = 1
    start_row_offset_idx: int = 0
    end_row_offset_idx: int = 0
    start_col_offset_idx: int = 0
    end_col_offset_idx: int = 0
    text: str = ""
    column_header: bool = False
    row_header: bool = False
    row_section: bool = False
    fillable: bool = False
    label: TableCellLabel = TableCellLabel.BODY


@dataclass
class TableData:
    num_rows: int = 0
    num_cols: int = 0
    grid: list = field(default_factory=list)
    orientation: str = "normal"
    table_cells: list = field(default_factory=list)


@dataclass
class TableItem(FloatingItem):
    label: DocItemLabel = DocItemLabel.TABLE
    data: Optional[TableData] = None
    annotations: list = field(default_factory=list)


@dataclass
class KeyValueItem(FloatingItem):
    label: DocItemLabel = DocItemLabel.KEY_VALUE_REGION
    graph: dict = field(default_factory=dict)


@dataclass
class PageItem:
    page_no: int = 1
    size: Optional[Size] = None
    image: Optional[ImageRef] = None


@dataclass
class DoclingDocument:
    schema_name: str = "DoclingDocument"
    version: str = "1.0.0+noesis"
    name: str = ""
    origin: Optional[DocumentOrigin] = None
    pages: dict = field(default_factory=dict)
    body: Optional[GroupItem] = None
    furniture: Optional[GroupItem] = None
    groups: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    pictures: list = field(default_factory=list)
    key_value_items: list = field(default_factory=list)
    field_regions: list = field(default_factory=list)
    form_items: list = field(default_factory=list)
    # ── nostre aggiunte ──
    geometry: dict = field(default_factory=dict)
    num_pages: int = 1

    def to_dict(self) -> dict:
        return _dump(self)

    def to_json(self, indent: int = 2, **kw) -> str:
        import json
        return json.dumps(self.to_dict(), indent=indent,
                          ensure_ascii=False, **kw)


# ─────────────────────────────────────────────────────────────────────────────
# serializzazione
# ─────────────────────────────────────────────────────────────────────────────


def _dump(obj: Any) -> Any:
    from dataclasses import asdict, is_dataclass
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj):
        out = {}
        for k, v in asdict(obj).items():
            out[k] = _dump(v)
        return out
    if isinstance(obj, dict):
        return {str(k): _dump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_dump(v) for v in obj]
    return obj


# ─────────────────────────────────────────────────────────────────────────────
# Builder geometrico (prototipo, deterministico, dev-only)
# ─────────────────────────────────────────────────────────────────────────────

#: mappa classi della content map (GNN) → etichette Docling
_GNN_LABEL = {
    "text": DocItemLabel.PARAGRAPH,
    "section-header": DocItemLabel.SECTION_HEADER,
    "title": DocItemLabel.TITLE,
    "list-item": DocItemLabel.LIST_ITEM,
    "caption": DocItemLabel.CAPTION,
    "footnote": DocItemLabel.FOOTNOTE,
    "page-header": DocItemLabel.PAGE_HEADER,
    "page-footer": DocItemLabel.PAGE_FOOTER,
    "page-number": DocItemLabel.PAGE_FOOTER,
    "table": DocItemLabel.TABLE,
    "picture": DocItemLabel.PICTURE,
    "formula": DocItemLabel.FORMULA,
}


def _is_bold(flags: int) -> bool:
    return bool(flags & 16)


class _Builder:
    def __init__(self, page, page_no: int):
        self.page = page
        self.page_no = page_no
        self.W = page.rect.width
        self.H = page.rect.height
        self.counters = {"texts": 0, "tables": 0, "pictures": 0, "groups": 0}
        self._leaf_nodes: dict[int, Any] = {}
        self.doc = DoclingDocument(num_pages=1)
        self.doc.origin = DocumentOrigin(filename=getattr(page.parent, "name", ""))
        self.doc.pages = {str(page_no): PageItem(
            page_no=page_no, size=Size(self.W, self.H))}
        self.doc.body = GroupItem(self_ref="#/body", parent=None,
                                  group_label=GroupLabel.SECTION, name="_root_",
                                  content_layer=ContentLayer.BODY,
                                  source=NodeSource.GEOMETRY)
        self.doc.furniture = GroupItem(self_ref="#/furniture", parent=None,
                                       group_label=GroupLabel.SECTION,
                                       name="_root_",
                                       content_layer=ContentLayer.FURNITURE,
                                       source=NodeSource.GEOMETRY)

    # ── helper refs ──
    def _ref(self, kind: str) -> str:
        i = self.counters[kind]
        self.counters[kind] += 1
        return f"#/{kind}/{i}"

    def _add(self, kind: str, item):
        getattr(self.doc, kind).append(item)
        return item

    # ── lettura primitive ──
    def text_blocks(self) -> list[dict]:
        """Blocchi di testo **PyMuPDF puro** (indipendenti dal GNN)."""
        out = []
        try:
            d = self.page.get_text("dict")
        except Exception:
            return out
        for blk in d.get("blocks", []):
            if blk.get("type") != 0:
                continue
            lines = blk.get("lines", [])
            txt = " ".join(s.get("text", "") for ln in lines for s in ln.get("spans", []))
            spans = [s for ln in lines for s in ln.get("spans", [])]
            sizes = [s.get("size", 0) for s in spans if s.get("text", "").strip()]
            bold = any(_is_bold(s.get("flags", 0)) for s in spans)
            dirs = [ln.get("dir", (1, 0)) for ln in lines]
            rot = any(abs(dy) > abs(dx) for dx, dy in dirs)
            out.append({
                "bbox": tuple(blk["bbox"]),
                "text": " ".join(txt.split()),
                "n_lines": len(lines),
                "font_size": round(max(sizes), 1) if sizes else 0.0,
                "bold": bold,
                "rotated": rot,
            })
        return out

    def gutters(self, blocks: list[dict], min_gap: float = 8.0) -> list[tuple]:
        """Strisce verticali **vuote** (gutters) proiettando i bbox dei blocchi."""
        if not blocks:
            return []
        intervals = sorted((b["bbox"][0], b["bbox"][2]) for b in blocks)
        merged: list[list[float]] = []
        for x0, x1 in intervals:
            if merged and x0 <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], x1)
            else:
                merged.append([x0, x1])
        gaps = []
        for a, b in zip(merged, merged[1:]):
            if b[0] - a[1] >= min_gap:
                gaps.append((round(a[1], 1), round(b[0], 1)))
        return gaps

    def build(self):
        blocks = self.text_blocks()
        images = []
        try:
            images = list(self.page.get_image_info())
        except Exception:
            images = []

        # 1) furniture: fascia alta/bassa (chrome)
        top = self.H * 0.07
        bot = self.H * 0.93
        header = [b for b in blocks if b["bbox"][3] <= top]
        footer = [b for b in blocks if b["bbox"][1] >= bot]
        body = [b for b in blocks if b not in header and b not in footer]

        # 2) gutters + colonne sul corpo (scheletro geometrico)
        #    solo blocchi NON full-width: un blocco a tutta larghezza "chiude"
        #    le colonne e nasconderebbe il gutter.
        narrow = [b for b in body
                  if b["bbox"][2] - b["bbox"][0] < 0.6 * self.W]
        gaps = self.gutters(narrow)
        cols = []
        edges = [0.0] + [g[0] for g in gaps] + [self.W]
        # semplice: una colonna per regione tra i gutter "grandi"
        self.doc.geometry = {
            "page_size": [self.W, self.H],
            "gutters": gaps,
            "n_columns": len(gaps) + 1,
        }

        # 3) etichette dal GNN (se disponibile) — solo la semantica
        gnn = self._gnn_elements()

        # 4) costruzione nodi
        body_group = self.doc.body
        ordered = self._order(body, gaps)

        # azione: mappa ogni blocco → nodo
        for b in ordered:
            node = self._leaf(b, images, gnn)
            if node is None:
                continue
            body_group.children.append(RefItem(node.self_ref))

        # furniture
        for b in header:
            self._attach_furniture(b, DocItemLabel.PAGE_HEADER,
                                   "page_header" if b["text"].strip().isdigit() else "page_header")
        for b in footer:
            self._attach_furniture(b, DocItemLabel.PAGE_FOOTER, "page_footer")

        # immagini come PictureItem (raster) non già consumate
        self._pictures(images, body, gnn)
        # tabelle
        self._tables(body, gnn)
        return self.doc

    def _gnn_elements(self) -> list[dict]:
        try:
            import ir_layout
            _t, els = ir_layout.page_elements(self.page.parent, self.page.number)
            return els
        except Exception:
            return []

    def _best_gnn(self, bbox: tuple, gnn: list[dict]) -> Optional[dict]:
        best, bi = None, 0.0
        for e in gnn:
            iou = _iou(bbox, tuple(e["bbox"]))
            if iou > bi:
                best, bi = e, iou
        return best if bi >= 0.3 else None

    def _leaf(self, b: dict, images: list, gnn: list[dict]):
        g = self._best_gnn(b["bbox"], gnn)
        label = DocItemLabel.PARAGRAPH
        source = NodeSource.GEOMETRY
        attrs = {}
        if g is not None:
            label = _GNN_LABEL.get(g.get("class", "text"), DocItemLabel.PARAGRAPH)
            source = NodeSource.GNN
        else:
            # euristica: font grande + bold → header; altrimenti paragrafo
            if b["bold"] and b["font_size"] >= 9.5:
                label = DocItemLabel.SECTION_HEADER
                source = NodeSource.HEURISTIC
        flags = []
        if b["bbox"][2] - b["bbox"][0] >= 0.6 * self.W:
            flags.append("spanning")
        if b["rotated"]:
            flags.append("rotated")
        prov = [ProvenanceItem(self.page_no, BoundingBox.from_tuple(b["bbox"]))]

        if label == DocItemLabel.SECTION_HEADER:
            node = SectionHeaderItem(
                self_ref=self._ref("texts"), label=label,
                content_layer=ContentLayer.BODY, prov=prov, source=source,
                flags=flags, text=b["text"], orig=b["text"],
                level=self._heading_level(b))
            return self._remember(b, self._add("texts", node))
        if label == DocItemLabel.TITLE:
            node = TitleItem(self_ref=self._ref("texts"), prov=prov,
                             source=source, flags=flags, text=b["text"],
                             orig=b["text"])
            return self._remember(b, self._add("texts", node))
        if label == DocItemLabel.LIST_ITEM:
            node = ListItem(self_ref=self._ref("texts"), prov=prov,
                            source=source, flags=flags, text=b["text"],
                            orig=b["text"], marker="•")
            return self._remember(b, self._add("texts", node))
        if label in (DocItemLabel.CAPTION, DocItemLabel.FOOTNOTE,
                     DocItemLabel.PAGE_FOOTER, DocItemLabel.PAGE_HEADER):
            node = TextItem(self_ref=self._ref("texts"), label=label, prov=prov,
                            source=source, flags=flags, text=b["text"],
                            orig=b["text"])
            return self._remember(b, self._add("texts", node))
        node = TextItem(self_ref=self._ref("texts"), label=label, prov=prov,
                        source=source, flags=flags, text=b["text"],
                        orig=b["text"])
        return self._remember(b, self._add("texts", node))

    def _remember(self, b: dict, node):
        self._leaf_nodes[id(b)] = node
        return node

    def _heading_level(self, b: dict) -> int:
        if b["font_size"] >= 15 or (b["bold"] and b["font_size"] >= 12):
            return 1
        if b["font_size"] >= 11:
            return 2
        return 3

    def _attach_furniture(self, b: dict, label: DocItemLabel, name: str):
        prov = [ProvenanceItem(self.page_no, BoundingBox.from_tuple(b["bbox"]))]
        node = TextItem(self_ref=self._ref("texts"), label=label,
                        content_layer=ContentLayer.FURNITURE, prov=prov,
                        source=NodeSource.GEOMETRY, text=b["text"],
                        orig=b["text"])
        self._add("texts", node)
        self.doc.furniture.children.append(RefItem(node.self_ref))

    def _pictures(self, images: list, body: list[dict], gnn: list[dict]):
        # immagini raster non coperte da nodi testo (scheletro: PictureItem)
        for im in images:
            bb = tuple(im["bbox"])
            prov = [ProvenanceItem(self.page_no, BoundingBox.from_tuple(bb))]
            cap = self._nearest_caption(bb, body)
            pic = PictureItem(self_ref=self._ref("pictures"), prov=prov,
                              source=NodeSource.GEOMETRY, flags=["raster"],
                              image=ImageRef(size=Size(bb[2] - bb[0], bb[3] - bb[1]),
                                             mode=ImageRefMode.PLACEHOLDER))
            if cap is not None:
                pic.captions.append(RefItem(cap.self_ref))
                pic.caption_text = cap.text
            self._add("pictures", pic)
            self.doc.body.children.append(RefItem(pic.self_ref))

    def _nearest_caption(self, bb: tuple, blocks: list[dict]):
        best, dy0 = None, 1e9
        for b in blocks:
            t = b["text"]
            if not t:
                continue
            lo = t.lower()
            if not (lo.startswith("fig") or lo.startswith("table")
                    or lo.startswith("figure") or lo.startswith("tab")):
                continue
            x0, y0, x1, y1 = b["bbox"]
            if y0 >= bb[3] - 2 and y0 - bb[3] <= 90:
                d = y0 - bb[3]
                if d < dy0:
                    best, dy0 = b, d
        return self._leaf_nodes.get(id(best)) if best is not None else None

    def _tables(self, body: list[dict], gnn: list[dict]):
        try:
            finder = self.page.find_tables(strategy="lines")
            tables = list(finder.tables)
        except Exception:
            tables = []
        for t in tables:
            bb = tuple(t.bbox)
            try:
                rows_txt = t.extract()
            except Exception:
                rows_txt = []
            rows_obj = list(getattr(t, "rows", []) or [])
            n_rows = len(rows_txt) if rows_txt else getattr(t, "row_count", 0)
            n_cols = max((len(r) for r in rows_txt),
                         default=getattr(t, "col_count", 0))
            cells: list[TableCell] = []
            grid: list = []
            for ri, row in enumerate(rows_txt):
                row_cells = rows_obj[ri].cells if ri < len(rows_obj) else []
                populated = sum(1 for x in row
                                if x is not None and str(x).strip())
                g = []
                for ci, txt in enumerate(row):
                    cb = None
                    if ci < len(row_cells) and row_cells[ci]:
                        cb = tuple(row_cells[ci])
                    txt = (txt or "").strip()
                    if ri == 0:
                        lbl = TableCellLabel.COLUMN_HEADER
                    elif populated == 1:
                        lbl = TableCellLabel.ROW_SECTION
                    elif ci == 0 and txt:
                        lbl = TableCellLabel.ROW_HEADER
                    else:
                        lbl = TableCellLabel.BODY
                    cells.append(TableCell(
                        bbox=BoundingBox.from_tuple(cb) if cb else None,
                        start_row_offset_idx=ri, end_row_offset_idx=ri + 1,
                        start_col_offset_idx=ci, end_col_offset_idx=ci + 1,
                        text=txt,
                        column_header=(lbl == TableCellLabel.COLUMN_HEADER),
                        row_header=(lbl == TableCellLabel.ROW_HEADER),
                        row_section=(lbl == TableCellLabel.ROW_SECTION),
                        label=lbl))
                    g.append({"col": ci, "text": txt})
                grid.append(g)
            data = TableData(num_rows=n_rows, num_cols=n_cols, grid=grid,
                             table_cells=cells)
            prov = [ProvenanceItem(self.page_no, BoundingBox.from_tuple(bb))]
            tbl = TableItem(self_ref=self._ref("tables"), prov=prov,
                            source=NodeSource.GEOMETRY, data=data,
                            flags=["lines-grid"])
            cap = self._nearest_caption(bb, body)
            if cap is not None:
                tbl.captions.append(RefItem(cap.self_ref))
                tbl.caption_text = cap.text
            self._add("tables", tbl)
            self.doc.body.children.append(RefItem(tbl.self_ref))

    def _clip(self, bbox: tuple) -> str:
        try:
            return " ".join(self.page.get_text("text", clip=PymupdfRect(*bbox)).split())
        except Exception:
            return ""

    def _order(self, blocks: list[dict], gaps: list[tuple]) -> list[dict]:
        """Ordine di lettura geometrico: bande full-width, poi colonne sx→dx."""
        if not blocks:
            return []
        col_bounds = [0.0]
        for g in gaps:
            col_bounds.append(g[0])
        col_bounds.append(self.W)
        ncol = len(col_bounds) - 1

        full = [b for b in blocks if b["bbox"][2] - b["bbox"][0] >= 0.6 * self.W]
        rest = [b for b in blocks if b not in full]
        seps = sorted(full, key=lambda b: b["bbox"][1])
        sep_y = [b["bbox"][1] for b in seps]

        def col_of(b) -> int:
            mid = (b["bbox"][0] + b["bbox"][2]) / 2
            c = 0
            for i in range(1, ncol):
                if mid > col_bounds[i]:
                    c = i
            return c

        bands: list[list[dict]] = [[] for _ in range(len(seps) + 1)]
        for b in rest:
            bi = sum(1 for y in sep_y if b["bbox"][1] >= y)
            bands[bi].append(b)
        out: list[dict] = []
        for i, band in enumerate(bands):
            band.sort(key=lambda b: (col_of(b), b["bbox"][1], b["bbox"][0]))
            out.extend(band)
            if i < len(seps):
                out.append(seps[i])
        return out


def _iou(a: tuple, b: tuple) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def build_page_document(pdf_path: str, page_index: int) -> DoclingDocument:
    """Costruisce la rappresentazione logica (Docling-like) di **una pagina**."""
    global PymupdfRect
    import pymupdf
    PymupdfRect = pymupdf.Rect
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_index]
        b = _Builder(page, page_index + 1)
        model = b.build()
        model.name = pdf_path
        return model


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, required=True, help="1-based")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    model = build_page_document(args.pdf, args.page - 1)
    js = model.to_json(indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(js)
        print(f"scritto {args.out}")
    else:
        print(js)
