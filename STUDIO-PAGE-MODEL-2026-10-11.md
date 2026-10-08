# Page Model — rappresentazione logica di pagina (schema Docling) — 2026-10-11

Tentativo architetturale: portare ogni pagina a una **rappresentazione logica
gerarchica** (albero), replicando lo schema **DoclingDocument** + nostre aggiunte.
Direzione emersa dalla discussione: la geometria di pagina diventa l'artefatto di
prima classe che (a) rende l'**ordine una conseguenza della struttura**, (b) fa da
**riferimento indipendente** (offline, deterministico, senza nuove dipendenze).

## 1. Schema

`page_model.py` replica il sottoinsieme fedele di `docling_core.types.doc`:

- **Item**: `texts`, `tables`, `pictures`, `key_value_items`; radici `body` e
  `furniture`; `groups`.
- **Envelope** (`DocItem`): `self_ref`, `label` (`DocItemLabel`), `parent`,
  `children`, `content_layer` (`body`/`furniture`/`background`), `prov`
  (`page_no`, `bbox`, `charspan`), `meta`, `comments`.
- **Tipi**: `TextItem`/`SectionHeaderItem.level`/`TitleItem`/`ListItem`/
  `FormulaItem`/`ReferenceItem`/`CheckboxItem`; `GroupItem` (`GroupLabel`);
  `FloatingItem` (`captions`/`footnotes`/`references`/`image`); `TableItem.data`
  (`TableData`: `num_rows`/`num_cols`/`grid`/`table_cells`) e `TableCell`
  (`row_span`/`col_span`, offset, `text`, `column_header`/`row_header`/
  `row_section`, `TableCellLabel`); `PageItem`/`Size`/`BoundingBox`
  (`CoordOrigin`); `DocumentOrigin`.
- **Nostre aggiunte**: `source` (geometry/gnn/heuristic/oracle), `confidence`,
  `flags` (`spanning`, `raster`, …); a livello documento `geometry`
  (gutters/colonne/dimensioni) = **scheletro geometrico indipendente**.

Nessuna dipendenza nuova: solo `dataclasses`/`enum` + `pymupdf`. Docling (MIT) è
usato come **riferimento di schema**, non importato.

## 2. Builder (prototipo, dev-only)

`page_model.build_page_document(pdf, page_index)` costruisce, in modo
deterministico:

1. **Primitive PyMuPDF pure**: blocchi `get_text("dict")` (font, bold, dir),
   `get_image_info()`, `find_tables(strategy="lines")`.
2. **Furniture**: fascia alta/bassa → `page_header`/`page_footer` in
   `content_layer="furniture"`.
3. **Scheletro geometrico**: **gutters** (proiezione x dei blocchi non
   full-width) → numero di colonne.
4. **Ordine di lettura**: bande a tutta larghezza + colonne sx→dx → `body.children`.
5. **Etichette** dal GNN (content map) per IoU, altrimenti euristiche
   (font/bold) → `section_header`/`paragraph`/`list_item`/`caption`.
6. **Tabelle**: `find_tables(lines)` → `TableData` con celle, `column_header`
   (riga 0), `row_header` (colonna 0), `row_section` (righe a cella unica).
7. **Figure**: immagini raster → `PictureItem` (`flags=["raster"]`) con
   `captions` per prossimità.

CLI: `.venv/bin/python page_model.py <pdf> --page N --out page.json`.

## 3. Evidenza (dopo il refactor: gutter a profilo + nesting)

- `fe22 p1038` (difetto d'ordine): profilo di copertura → gutter `[206–222]` e
  `[383–399]` → **3 colonne** (sidebar + 2 content), costruite come **3
  `GroupItem` "column"**; l'ordine di lettura (traversal) tiene ogni colonna
  **contigua** invece di interlacciare (come faceva il motore).
- `ce24 p480` (tabella complessa): `find_tables(strategy="lines")` cattura
  l'**intera `TABLE 46-2`** come un unico `TableItem` **25×7** (175 celle) con
  `row_section` (le sezioni "WHY?/HOW TO USE?/…"), `column_header`, `row_header`.
- `ha22 p3355` / `su19 p1050`: `PictureItem` (`flags=["raster"]`) con `captions`.
- Pagina normale (`plos`): 1 colonna, nessun gruppo spurio.

## 4. Limiti attuali (onesti)

- `find_tables(lines)` può produrre **falsi positivi** (es. `fe22 p1038`: un
  blocco "ICD-10CM CODE" letto come tabella 2×2 nella sidebar).
- Le **sezioni** interne alla tabella sono `row_section` in modo euristico (righe
  a cella unica); le **celle unite** non sono ancora ricostruite con
  `row_span`/`col_span`.
- Il **nesting** copre bande+colonne; mancano la gerarchia **sezioni/capitoli**
  (`GroupLabel.SECTION` per gli header) e la gestione degli **spanning**.
- La **semantica** (label) viene dal GNN: lo *scheletro* è indipendente, la
  *semantica* no → mantenere la separazione (invarianti sul solo scheletro).

## 5. Principio (anti-cecità correlata)

Due strati tenuti **separati**: **scheletro geometrico** (PyMuPDF puro,
indipendente) + **etichette** (GNN/euristiche). Il motore userà l'albero
etichettato; gli **invarianti** controlleranno lo **scheletro**. Non si mescolano.

## 6. Prossimi passi

1. **Gutter detection** robusta (profilo di copertura) → colonne corrette su
   `fe22 p1038` e `ce24 p480`.
2. **Nesting** in `GroupItem` (colonne/bande) e ordine = traversal.
3. **Tabelle**: sezioni + celle unite dalla griglia `lines`.
4. **Invarianti sullo scheletro** (nuovo livello) e **misura** sui difetti aperti.
5. Solo dopo: valutare l'uso in **runtime** (emissione).
