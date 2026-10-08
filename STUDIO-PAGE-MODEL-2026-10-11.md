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
4. **Ordine di lettura**: bande a tutta larghezza → colonne sx→dx; poi
   **gerarchia di sezione** (`GroupLabel.SECTION` dagli header, annidata per
   `level`) → il traversal dei figli del `body` è l'ordine di lettura.
5. **Etichette** dal GNN (content map) per IoU, altrimenti euristiche
   (font/bold) → `section_header`/`paragraph`/`list_item`/`caption`.
6. **Tabelle**: `find_tables(strategy="lines")` con **filtro anti falsi
   positivi** (serve ≥2×2 **e** (≥3 righe o ≥3 colonne **oppure** conferma da
   `lines_strict`)) → `TableData` con griglia da `rows[i].cells` e
   **`row_span`/`col_span` reali**; `row_section` (righe a tutta larghezza),
   `column_header` (riga 0), `row_header` (colonna 0).
7. **Figure**: immagini raster → `PictureItem` (`flags=["raster"]`) con
   `captions` per prossimità.
8. **Isolamento**: lo scheletro geometrico legge PyMuPDF in modalità
   **classica** (`_classic_pin`), annullando gli effetti **globali** di
   `pymupdf4llm` (`pymupdf.layout` attivo + quad corrections disattivate) →
   determinismo tra chiamate.

CLI: `.venv/bin/python page_model.py <pdf> --page N --out page.json`.

## 3. Evidenza (dopo il refactor: gutter a profilo + nesting)

- `fe22 p1038` (difetto d'ordine): profilo di copertura → gutter `[206–222]` e
  `[383–399]` → **3 colonne** (sidebar + 2 content), costruite come **3
  `GroupItem` "column"**; l'ordine di lettura (traversal) tiene ogni colonna
  **contigua** invece di interlacciare (come faceva il motore).
- `ce24 p480` (tabella complessa): l'**intera `TABLE 46-2`** come un unico
  `TableItem` **25×7**, **54 celle** da `rows[i].cells` (slot `None` = celle
  unite), `coverage 175/175` **senza buchi**, `row_span`/`col_span` reali,
  `row_section` (le sezioni "WHY?/HOW TO USE?/…"), `column_header`, `row_header`.
- **Filtro falsi positivi**: su 150 pagine campione scarta **4/4** candidati
  dubbi (es. `fe22 p1038` "ICD-10CM CODE" 2×2, `mw15 p200`, `al17 p375`) senza
  perdere tabelle vere (nessun candidato 2×2 legittimo nel campione).
- **Determinismo**: `ce24 p480` resta 25×7 anche dopo che il GNN
  (`pymupdf4llm`) ha alterato lo stato globale di PyMuPDF (test dedicato).
- **Gerarchia sezioni**: su `fe22 p1038` gli header aprono `GroupItem`
  `region=section`, annidati per `level` (es. `BASIC INFORMATION` → `PHYSICAL
  FINDINGS…`); l'ordine di lettura (traversal) è preservato. Validato su 36
  pagine campione: nessun gruppo vuoto, tutti i riferimenti risolvibili.
- **Invarianti dello scheletro** (`tools/measure_page_model.py`, S1–S5: albero /
  provenienza / griglia / ordine / colonne): **39/40** pagine campione senza
  violazioni (l'unica era un blocco vuoto a bbox sentinella di PyMuPDF, ora
  saltato); 4/4 pagine-difetto pulite.
- **Misura sui difetti aperti** (riferimento geometrico indipendente):
  - `ce24 p480` → **una** tabella 25×7 (nessun interlacciamento da ordine);
  - `fe22 p1038` → **3 colonne contigue** + sezioni;
  - `arxiv_2609.37412 p30` → **101 blocchi di testo**: la prosa c'è, la perdita
    (`recall 0.67`) è in **emissione**, non nella geometria;
  - `su19 p1050` → **una** immagine raster CMYK (parzialmente fuori pagina,
    `y0<0`), **non 3**: la geometria non conferma "3 figure"; il difetto
    "figura vuota" è di **rendering** (CMYK/off-page), non di rilevazione.
- `ha22 p3355` / `su19 p1050`: `PictureItem` (`flags=["raster"]`) con `captions`.
- Pagina normale (`plos`): 1 colonna, nessun gruppo spurio.

## 4. Limiti attuali (onesti)

- Il filtro falsi positivi **scarta anche tabelle piccole senza linee tracciate**
  (2×2 "borderless"): compromesso accettato, il campione non ne ha mostrato di
  legittime.
- Le **sezioni** interne sono `row_section` in modo euristico (solo righe a
  tutta larghezza); i sotto-header a metà tabella (es. `STARTING DOSE`) restano
  `body` finché non si usa il **font** per cella.
- Su tabelle **frammentate** la griglia può avere **buchi** (`None`), fedeli
  alla griglia di PyMuPDF (es. `biorxiv p34`, 16/35 coperti).
- Il **nesting** copre bande+colonne+sezioni; la gestione dedicata degli
  **spanning** (oltre a separatore di banda / `flags=["spanning"]`) resta da
  affinare.
- La **semantica** (label) viene dal GNN: lo *scheletro* è indipendente, la
  *semantica* no → mantenere la separazione (invarianti sul solo scheletro).

## 5. Principio (anti-cecità correlata)

Due strati tenuti **separati**: **scheletro geometrico** (PyMuPDF puro,
indipendente) + **etichette** (GNN/euristiche). Il motore userà l'albero
etichettato; gli **invarianti** controlleranno lo **scheletro**. Non si mescolano.

## 6. Prossimi passi

- [x] **Gutter detection** a profilo di copertura → colonne corrette su
  `fe22 p1038`.
- [x] **Nesting** in `GroupItem` (colonne/bande) e ordine = traversal.
- [x] **Tabelle**: filtro falsi positivi + celle unite (`row_span`/`col_span`).
- [x] **Gerarchia sezioni/capitoli** (`GroupLabel.SECTION` dagli header,
  annidata per `level`).
- [x] **Invarianti sullo scheletro** (`tools/measure_page_model.py`, S1–S5) e
  **misura** sui difetti aperti.
- [~] **Elementi spanning**: marcati (`flags=["spanning"]`) e usati come
  separatori di banda; la gestione dedicata nell'albero resta da affinare.
- [~] **Integrazione diagnostica opt-in** in `ir_layout.build_markdown`
  (`order_log`, default off → output identico) + `tools/measure_order.py`.
- [ ] Solo dopo (se i numeri lo giustificano): valutare l'uso **produttore** in
  runtime, sostituendo `reorder_boxes` — mai etichette/emissione.

## 7. Integrazione diagnostica (opt-in, non distruttiva)

Per misurare se l'ordine del page model è migliore di quello **emesso** dal
motore, l'integrazione resta **accanto** alla pipeline (mai produttore, per ora):

- `ir_layout.build_markdown(..., order_log=[])`: hook **opt-in** (default
  `None` → output **identico**) che registra l'ordine emesso (`class`, `bbox`).
- `page_model.build_page_model_from_page(page, page_no)`: builder da una `page`
  già aperta (non riapre il PDF nella pipeline).
- `tools/measure_order.py`: abbina i blocchi emessi alle foglie del page model
  per **copertura** (frazione del blocco dentro la foglia) e conta le
  **inversioni** (Kendall) tra i due ordini; non tocca l'md.

Run: 4 difetti + **50 held-out** (seed `20261024`), 54 pagine totali.
Risultato: **44/54 senza inversioni**, **38/4328** inversioni (0,009). Peggiori:
`fe22 p1038` **13** (difetto noto — il page model lo conferma), poi
`arxiv_2609.38133 p14` 8, `fe23 p362` 4, `to22 p380` 4, `arxiv p30` 3,
`arxiv_2609.38151 p19` 2.

Lettura **onesta**: l'ordine emesso dal motore coincide con quello geometrico
indipendente sulla grande maggioranza delle pagine; i disaccordi sono pochi e
concentrati e costituiscono una **mappa di dove guardare**, non una prova che il
page model sia migliore. Limiti della misura: copre solo i blocchi
**abbinnati** (su alcune pagine `engine_unmatched` è alto) e non vede i difetti
**dentro** una tabella (`ce24 p480` → 1 foglia).
