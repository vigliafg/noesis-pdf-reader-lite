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

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

#: caratteri di controllo emessi talvolta da PyMuPDF nei testi (es. \x07)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f]")

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


def _classic_pin() -> tuple:
    """Torna alla modalità **classica** di PyMuPDF per lo scheletro geometrico.

    L'import di ``pymupdf4llm`` (usato dallo strato GNN) ha due effetti
    **globali** che cambiano ``get_text``/``find_tables``: attiva
    ``pymupdf.layout`` e disabilita le *quad corrections*. Lo scheletro
    geometrico deve esserne indipendente → si salva lo stato, si torna alla
    modalità classica e lo si ripristina subito dopo.
    """
    import pymupdf
    state = (getattr(pymupdf, "_get_layout", None),
             pymupdf.TOOLS.unset_quad_corrections())
    try:
        pymupdf._get_layout = None
    except Exception:
        pass
    pymupdf.TOOLS.unset_quad_corrections(False)
    return state


def _classic_restore(state: tuple) -> None:
    import pymupdf
    try:
        pymupdf._get_layout = state[0]
    except Exception:
        pass
    pymupdf.TOOLS.unset_quad_corrections(state[1])


class _Builder:
    def __init__(self, page, page_no: int):
        self.page = page
        self.page_no = page_no
        self.W = page.rect.width
        self.H = page.rect.height
        self.counters = {"texts": 0, "tables": 0, "pictures": 0, "groups": 0}
        self._leaf_nodes: dict[int, Any] = {}
        self._by_ref: dict[str, Any] = {}
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
        self._by_ref[item.self_ref] = item
        return item

    # ── lettura primitive ──
    def text_blocks(self) -> list[dict]:
        """Blocchi di testo **PyMuPDF puro** (indipendenti dal GNN)."""
        out = []
        old = _classic_pin()
        try:
            d = self.page.get_text("dict")
        except Exception:
            d = {}
        finally:
            _classic_restore(old)
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
                "text": _CTRL_RE.sub("", " ".join(txt.split())),
                "n_lines": len(lines),
                "font_size": round(max(sizes), 1) if sizes else 0.0,
                "bold": bold,
                "rotated": rot,
            })
        return out

    def gutters_profile(self, blocks: list[dict], min_gap: float = 6.0,
                        frac: float = 0.06) -> list[tuple]:
        """Gutters da **profilo di copertura** in x (robusto ai blocchi "ponte").

        Per ogni colonna di 1pt si somma l'altezza dei blocchi che la coprono; una
        striscia con copertura ≤ ``frac`` dell'estensione verticale è un gutter.
        A differenza del merge di intervalli, un singolo blocco che attraversa non
        cancella il gutter (contribuisce poca altezza).
        """
        if not blocks:
            return []
        x0 = min(b["bbox"][0] for b in blocks)
        x1 = max(b["bbox"][2] for b in blocks)
        y0 = min(b["bbox"][1] for b in blocks)
        y1 = max(b["bbox"][3] for b in blocks)
        span = max(1.0, y1 - y0)
        W = int(x1 - x0) + 2
        cov = [0.0] * W
        for b in blocks:
            bx0 = max(0, int(b["bbox"][0] - x0))
            bx1 = min(W, int(b["bbox"][2] - x0))
            h = b["bbox"][3] - b["bbox"][1]
            for i in range(bx0, bx1):
                cov[i] += h
        thr = frac * span
        gaps: list[tuple] = []
        start = None
        for i, v in enumerate(cov):
            if v <= thr:
                if start is None:
                    start = i
            elif start is not None:
                if i - start >= min_gap:
                    gaps.append((round(x0 + start, 1), round(x0 + i, 1)))
                start = None
        if start is not None and W - start >= min_gap:
            gaps.append((round(x0 + start, 1), round(x0 + W, 1)))
        # scarta i gutter che toccano i bordi (margini di pagina)
        return [g for g in gaps if g[0] > x0 + 2 and g[1] < x1 - 2]

    def _col_bounds(self, gaps: list[tuple]) -> list[float]:
        return [(g[0] + g[1]) / 2 for g in gaps]

    def _col_of(self, bbox: tuple, bounds: list[float]) -> int:
        mid = (bbox[0] + bbox[2]) / 2
        return sum(1 for b in bounds if mid > b)

    def _assemble(self, items: list[tuple], bounds: list[float]):
        """Costruisce l'albero del ``body``: bande (full-width) → colonne → foglie.

        L'ordine di lettura è il **traversal** dei figli del body.
        """
        def is_sep(bbox: tuple) -> bool:
            return (bbox[2] - bbox[0]) >= 0.6 * self.W

        seps = sorted([it for it in items if is_sep(it[0])],
                      key=lambda t: t[0][1])
        rest = [it for it in items if not is_sep(it[0])]
        sep_y = [t[0][1] for t in seps]
        bands: list[list[tuple]] = [[] for _ in range(len(seps) + 1)]
        for it in rest:
            bi = sum(1 for y in sep_y if it[0][1] >= y)
            bands[bi].append(it)

        body = self.doc.body

        def order_key(it):
            return (self._col_of(it[0], bounds), it[0][1], it[0][0])

        for i, band in enumerate(bands):
            cols: dict[int, list[tuple]] = {}
            for it in band:
                cols.setdefault(self._col_of(it[0], bounds), []).append(it)
            nonempty = [c for c in sorted(cols) if cols[c]]
            if len(nonempty) <= 1:
                for it in sorted(band, key=lambda t: (t[0][1], t[0][0])):
                    self._append_child(body, it[1])
            else:
                for ci in nonempty:
                    grp = GroupItem(
                        self_ref=self._ref("groups"), group_label=GroupLabel.SECTION,
                        name=f"column_{ci}",
                        meta={"region": "column", "col": ci},
                        source=NodeSource.GEOMETRY, content_layer=ContentLayer.BODY)
                    self._add("groups", grp)
                    grp.parent = RefItem(body.self_ref)
                    for it in sorted(cols[ci], key=lambda t: (t[0][1], t[0][0])):
                        self._append_child(grp, it[1])
                    body.children.append(RefItem(grp.self_ref))
            if i < len(seps):
                self._append_child(body, seps[i][1])

    def _append_child(self, group, node):
        group.children.append(RefItem(node.self_ref))
        node.parent = RefItem(group.self_ref)

    def _nest_sections(self, container):
        """Annida i figli di ``container`` in ``GroupItem`` di sezione.

        Un ``section_header`` apre una sezione (con ``level``); gli elementi
        successivi vi appartengono finché non arriva un header di livello ≤.
        L'ordine di lettura è preservato (è il traversal dei figli).
        """
        children = list(container.children)
        if not children:
            return
        container.children = []
        # stack di (livello, gruppo_sezione)
        stack: list[tuple[int, GroupItem]] = []
        for ref in children:
            node = self._by_ref.get(ref.cref)
            if isinstance(node, SectionHeaderItem):
                lvl = int(getattr(node, "level", 1) or 1)
                while stack and stack[-1][0] >= lvl:
                    stack.pop()
                grp = GroupItem(
                    self_ref=self._ref("groups"), group_label=GroupLabel.SECTION,
                    name=(node.text[:48] or "section"),
                    meta={"region": "section", "level": lvl},
                    source=NodeSource.HEURISTIC, content_layer=ContentLayer.BODY)
                self._add("groups", grp)
                parent = stack[-1][1] if stack else container
                parent.children.append(RefItem(grp.self_ref))
                grp.parent = RefItem(parent.self_ref)
                grp.children.append(ref)
                node.parent = RefItem(grp.self_ref)
                stack.append((lvl, grp))
            else:
                parent = stack[-1][1] if stack else container
                parent.children.append(ref)
                if node is not None:
                    node.parent = RefItem(parent.self_ref)

    def _nest_sections_all(self):
        body = self.doc.body
        for ch in list(body.children):
            node = self._by_ref.get(ch.cref)
            if isinstance(node, GroupItem) and (node.meta or {}).get("region") == "column":
                self._nest_sections(node)
        self._nest_sections(body)

    def build(self):
        blocks = self.text_blocks()
        images = []
        try:
            images = list(self.page.get_image_info())
        except Exception:
            images = []

        # tabelle: griglia dalle **linee** (indipendente dal GNN)
        tables = self._find_tables()
        table_boxes = [tuple(t.bbox) for t in tables]

        def in_table(bb) -> bool:
            return any(_cover(bb, tb) >= 0.5 for tb in table_boxes)

        # 1) furniture: fascia alta/bassa (chrome)
        top = self.H * 0.07
        bot = self.H * 0.93
        header = [b for b in blocks if b["bbox"][3] <= top]
        footer = [b for b in blocks if b["bbox"][1] >= bot]
        body = [b for b in blocks
                if b not in header and b not in footer
                and not in_table(b["bbox"])]

        # 2) scheletro geometrico: gutters (testo del corpo + tabelle)
        skel = list(body) + [{"bbox": tb, "text": ""} for tb in table_boxes]
        gaps = self.gutters_profile(skel)
        bounds = self._col_bounds(gaps)
        self.doc.geometry = {
            "page_size": [self.W, self.H],
            "gutters": gaps,
            "columns_edges": [0.0] + [g[0] for g in gaps] + [self.W],
            "n_columns": len(gaps) + 1,
        }

        # 3) etichette dal GNN (solo la semantica)
        gnn = self._gnn_elements()

        # 4) nodi foglia (testo), poi tabelle/figure; nessun append diretto
        for b in body:
            self._leaf(b, images, gnn)
        items: list[tuple] = []
        for b in body:
            node = self._leaf_nodes.get(id(b))
            if node is not None:
                items.append((b["bbox"], node))
        items += self._make_tables(tables, body)
        items += self._make_pictures(images, body)

        # 5) albero: bande → colonne → foglie (ordine = traversal)
        self._assemble(items, bounds)
        # 5b) gerarchia logica: sezioni/capitoli dagli header
        self._nest_sections_all()

        # 6) furniture
        for b in header:
            self._attach_furniture(b, DocItemLabel.PAGE_HEADER, "page_header")
        for b in footer:
            self._attach_furniture(b, DocItemLabel.PAGE_FOOTER, "page_footer")
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

    def _make_pictures(self, images: list, body: list[dict]):
        out: list[tuple] = []
        for im in images:
            bb = tuple(im["bbox"])
            if (bb[2] - bb[0]) < 24 or (bb[3] - bb[1]) < 24:
                continue  # scarta decorazioni minime
            prov = [ProvenanceItem(self.page_no, BoundingBox.from_tuple(bb))]
            pic = PictureItem(self_ref=self._ref("pictures"), prov=prov,
                              source=NodeSource.GEOMETRY, flags=["raster"],
                              image=ImageRef(size=Size(bb[2] - bb[0], bb[3] - bb[1]),
                                             mode=ImageRefMode.PLACEHOLDER))
            cap = self._nearest_caption(bb, body)
            if cap is not None:
                pic.captions.append(RefItem(cap.self_ref))
                pic.caption_text = cap.text
            self._add("pictures", pic)
            out.append((bb, pic))
        return out

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

    def _find_tables(self) -> list:
        """Tabelle dalla griglia di linee, con filtro anti falsi positivi.

        ``strategy="lines"`` è permissivo: sfondi/testo possono generare
        "tabelle" che sono in realtà riquadri (es. un box con bordo o un titolo
        sottolineato). Si accettano solo griglie con **≥2 righe e ≥2 colonne**
        che siano, in più, **complesse** (≥3 righe o ≥3 colonne) **oppure**
        confermate da ``lines_strict`` (linee realmente disegnate).
        """
        old = _classic_pin()
        try:
            try:
                cand = list(self.page.find_tables(strategy="lines").tables)
            except Exception:
                cand = []
            try:
                strict = [tuple(t.bbox) for t in
                          self.page.find_tables(strategy="lines_strict").tables]
            except Exception:
                strict = []
        finally:
            _classic_restore(old)
        if not cand:
            return []
        out: list = []
        for t in cand:
            try:
                rc, cc = int(t.row_count), int(t.col_count)
            except Exception:
                continue
            if rc < 2 or cc < 2:
                continue
            if rc >= 3 or cc >= 3:
                out.append(t)
                continue
            bb = tuple(t.bbox)
            if any(_iou(bb, s) >= 0.5 for s in strict):
                out.append(t)
        return out

    def _table_from_text(self, rows_txt: list, tidx: int) -> tuple:
        """Fallback (nessuna geometria di cella): griglia dal solo testo."""
        cells: list[TableCell] = []
        grid: list = []
        n_cols = max((len(r) for r in rows_txt), default=0)
        for ri, row in enumerate(rows_txt):
            g = []
            for ci, txt in enumerate(row):
                txt = _CTRL_RE.sub("", (txt or "").strip())
                lbl = (TableCellLabel.COLUMN_HEADER if ri == 0
                       else TableCellLabel.BODY)
                idx = len(cells)
                cells.append(TableCell(
                    start_row_offset_idx=ri, end_row_offset_idx=ri + 1,
                    start_col_offset_idx=ci, end_col_offset_idx=ci + 1,
                    text=txt, column_header=(ri == 0), label=lbl))
                g.append({"$ref": f"#/tables/{tidx}/data/table_cells/{idx}"})
            grid.append(g)
        return cells, grid, len(rows_txt), n_cols

    def _make_tables(self, tables: list, body: list[dict]) -> list[tuple]:
        out: list[tuple] = []
        for t in tables:
            bb = tuple(t.bbox)
            old = _classic_pin()
            try:
                rows_txt = t.extract()
            except Exception:
                rows_txt = []
            finally:
                _classic_restore(old)
            rows_obj = list(getattr(t, "rows", []) or [])

            def cells_of(i: int) -> list:
                if 0 <= i < len(rows_obj):
                    return list(getattr(rows_obj[i], "cells", []) or [])
                return []

            def cell_at(i: int, j: int):
                cs = cells_of(i)
                return cs[j] if 0 <= j < len(cs) else None

            # ``rows[i].cells[j]`` è la griglia di PyMuPDF: indice riga/colonna
            # = coordinate di griglia, ``None`` = slot coperto da una cella
            # unita (span orizzontale o verticale).
            n_rows = len(rows_obj) if rows_obj else int(getattr(t, "row_count", 0) or 0)
            n_cols = max((len(cells_of(i)) for i in range(len(rows_obj))),
                         default=0) or int(getattr(t, "col_count", 0) or 0)

            # confini approssimati di riga (y0 minimo) e colonna (x0 minimo):
            # servono solo come guardia geometrica per gli span.
            row_start: list[float] = []
            carry = 0.0
            for i in range(n_rows):
                ys = [c[1] for c in cells_of(i) if c]
                if ys:
                    carry = min(ys)
                row_start.append(carry)
            col_start: list[float] = []
            carry = 0.0
            for j in range(n_cols):
                xs = [cell_at(i, j)[0] for i in range(n_rows) if cell_at(i, j)]
                if xs:
                    carry = min(xs)
                col_start.append(carry)

            tidx = len(self.doc.tables)
            cells: list[TableCell] = []
            grid: list = [[None] * n_cols for _ in range(n_rows)]
            for i in range(n_rows):
                cs = cells_of(i)
                for j in range(min(n_cols, len(cs))):
                    cb = cs[j]
                    if not cb:
                        continue  # slot coperto da una cella unita
                    cb = tuple(cb)
                    # span verticale: righe sotto coperte (None) e dentro il rect
                    r1 = i + 1
                    while (r1 < n_rows and not cell_at(r1, j)
                           and row_start[r1] < cb[3] - 0.5):
                        r1 += 1
                    # span orizzontale: colonne a destra coperte e dentro il rect
                    c1 = j + 1
                    while (c1 < n_cols and not cell_at(i, c1)
                           and col_start[c1] < cb[2] - 0.5):
                        c1 += 1
                    row_span, col_span = r1 - i, c1 - j
                    txt = ""
                    if i < len(rows_txt) and j < len(rows_txt[i]):
                        txt = _CTRL_RE.sub("", (rows_txt[i][j] or "").strip())
                    if j == 0 and col_span == n_cols and txt:
                        lbl = TableCellLabel.ROW_SECTION
                    elif i == 0:
                        lbl = TableCellLabel.COLUMN_HEADER
                    elif j == 0 and row_span == 1 and txt:
                        lbl = TableCellLabel.ROW_HEADER
                    else:
                        lbl = TableCellLabel.BODY
                    idx = len(cells)
                    cells.append(TableCell(
                        bbox=BoundingBox.from_tuple(cb),
                        row_span=row_span, col_span=col_span,
                        start_row_offset_idx=i, end_row_offset_idx=r1,
                        start_col_offset_idx=j, end_col_offset_idx=c1,
                        text=txt,
                        column_header=(lbl == TableCellLabel.COLUMN_HEADER),
                        row_header=(lbl == TableCellLabel.ROW_HEADER),
                        row_section=(lbl == TableCellLabel.ROW_SECTION),
                        label=lbl))
                    ref = {"$ref": f"#/tables/{tidx}/data/table_cells/{idx}"}
                    for rr in range(i, r1):
                        for ccol in range(j, c1):
                            grid[rr][ccol] = ref

            if not cells:  # fallback: nessuna geometria di cella
                cells, grid, n_rows, n_cols = self._table_from_text(rows_txt, tidx)

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
            out.append((bb, tbl))
        return out

    def _order(self, blocks: list[dict], gaps: list[tuple]) -> list[dict]:
        """Deprecato: l'ordine è ora il traversal di ``_assemble``."""
        raise NotImplementedError


def _iou(a: tuple, b: tuple) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def _cover(inner: tuple, outer: tuple) -> float:
    """Frazione dell'area di ``inner`` contenuta in ``outer``."""
    ix = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    iy = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    a = max(1e-6, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return (ix * iy) / a


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
