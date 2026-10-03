# Base di conoscenza — Motore PyMuPDF4LLM (v1.28.2)

> Documento di studio del motore di estrazione. Fonti: **sorgente installato** in
> `.venv/lib/python3.14/site-packages/pymupdf4llm` (+ `pymupdf/layout`) e
> **documentazione ufficiale** (pymupdf.readthedocs.io, PyPI).
> Data: 2026-10-03. Riferimento progetto: `noesis-pdf-reader-lite-ir`.

---

## 0. Sintesi esecutiva (le 10 cose da sapere)

1. **Il motore attivo NON è quello euristico classico**: `pymupdf4llm._use_layout == True`.
   PyMuPDF 1.28.2 include `pymupdf.layout` (GNN) e `pymupdf4llm` lo attiva di default.
   Il pacchetto esterno `pymupdf_layout` **non è installato e non serve**: i modelli ONNX
   sono in `pymupdf/layout/resources/onnx/`.
2. Il cuore è un **GNN (BoxRFDGNN) su ONNX, CPU-only**, addestrato sugli interni del PDF
   (non su immagini renderizzate). Classifica i box in **11 classi**.
3. Le **euristiche classiche** (`pymupdf_rag.py`, `multi_column.py`, `get_text_lines.py`)
   sono il **percorso legacy**, attivo solo con `use_layout(False)`.
4. Il **reading order** è un **post-processing euristico** (`utils.find_reading_order`)
   applicato ai box del GNN → **modificabile senza toccare il modello**.
5. Le **tabelle** hanno una griglia predetta da modelli ONNX dedicati (`table_grid_model_*`,
   V1…V4). **Tabella senza griglia → scartata** (spiega le perdite di contenuto).
6. **Formule e figure** sono classi `formula`/`picture` → emesse come **immagini base64**.
7. L'**OCR** ha un **modello di decisione ONNX** (22 feature, soglia 0.93) + **plugin**
   (Tesseract/RapidOCR/PaddleOCR) con strategia **ibrida** (OCR solo delle zone senza testo).
8. `page_chunks=True` espone **`page_boxes`** (`class`+`bbox`+`pos`): la base su cui è
   costruito il nostro `ir_layout.py`.
9. Parametri come `hdr_info`, `table_strategy`, `margins`, `graphics_limit`, `image_size_limit`
   sono **solo del percorso legacy**; nel percorso layout sono **ignorati silenziosamente**
   (assorbiti da `**kwargs`, senza warning).
10. La **marginalia** (header/footer) nel percorso layout è inclusa di default
    (`header=True, footer=True`): l'omissione osservata nei test è dovuta al **nostro strato IR**,
    non al motore.

---

## 1. Identità, versioni, dipendenze

| voce | valore |
|---|---|
| `pymupdf4llm` | **1.28.2** |
| `pymupdf` | **1.28.2** (pin esatto: `__init__` lancia `ImportError` se la versione non combacia) |
| Dipendenze dichiarate | `psutil`, `pymupdf`, `pymupdf_layout`, `tabulate` |
| Di fatto usate anche | `numpy`, `onnxruntime` (1.30.0) |
| `pymupdf_layout` (esterno) | **non installato** (modulo non importabile) |
| `pymupdf.layout` (interno) | **presente e attivo** (ONNX bundled) |
| Licenza | AGPL-3.0 o commerciale Artifex |

Moduli del pacchetto:

```
pymupdf4llm/
  __init__.py            # dispatch layout/legacy + API pubblica
  __main__.py            # CLI
  batch_converter.py     # conversione batch multi-processo
  worker_sizing.py       # auto_workers() (CPU/RAM)
  helpers/
    document_layout.py   # PERCORSO LAYOUT (attivo): parse_document + ParsedDocument
    pymupdf_rag.py       # PERCORSO LEGACY: to_markdown euristico
    multi_column.py      # column_boxes() (legacy)
    get_text_lines.py    # get_raw_lines() (usato da ENTRAMBI)
    utils.py             # reading order, tabelle, immagini, helper geometrici
    progress.py
  ocr/
    analyze_page.py      # decisione OCR + ONNX
    compute_ocr_features.py
    *_api.py             # plugin: tesseract, rapidocr, paddleocr, rapidtess, paddletess
    ocr_decision_model.onnx
  llama/pdf_markdown_reader.py
```

---

## 2. Architettura: due motori e il dispatch

`pymupdf4llm/__init__.py` decide a runtime:

```
try:    import pymupdf.layout          # bundled in pymupdf 1.28.2
except: use_layout(False)
else:   use_layout(True)               # <-- il nostro caso
```

- `use_layout(True)` → `pymupdf.layout.activate()` imposta `pymupdf._get_layout`
  (la funzione che esegue il GNN). **Inoltre rimuove `IdentifyHeaders`/`TocHeaders`**
  (non disponibili nel percorso layout).
- `to_markdown` / `to_json` / `to_text` **dispacciano**:
  - layout attivo → `document_layout.parse_document(...).to_markdown/json/text(...)`
  - layout spento → `pymupdf_rag.to_markdown(...)` (euristiche)

`to_json`/`to_text` **funzionano solo in modalità layout** (nel legacy sollevano
`NotImplementedError`).

**Eccezione**: `table_output="html"` usa `table_html` (ricostruzione tabelle) restando
sul percorso layout; nel legacy ricade su un wiring proprio.

---

## 3. Percorso LAYOUT (attivo) — `document_layout.py`

### 3.1 Pipeline `parse_document()` (per pagina)

1. Apertura: se formato **Image** → `convert_to_pdf()`; se **Office** e PyMuPDF Pro
   disponibile → conversione.
2. Rimozione di `StructTreeRoot` (performance).
3. Per ogni pagina:
   1. `remove_rotation()`.
   2. **Decisione OCR** `make_ocr_decision(page, use_ocr)` → `analyze_page` (§7).
   3. Se serve OCR → esecuzione del plugin OCR (`keep_ocr_text=False`) e merge del testo.
   4. `page.get_textpage(flags=FLAGS)` + `extractDICT()` → `blocks`.
   5. **`get_layout_locked(page, return_raw=True)`** → esegue il **GNN** e popola
      **`page.layout_information`** (lista di box con `class_name`, `group_bbox`,
      `table_grid`, …).
      ⚠️ `page.get_layout()` **restituisce `None`**: il risultato va letto dalla
      **proprietà** `page.layout_information`. L'attivazione del modello avviene
      all'`import pymupdf4llm` (via `pymupdf.layout.activate()`), non con il solo
      `import pymupdf`.
   6. (Opz.) rendering HTML tabelle riusando il layout raw.
   7. Costruzione `new_layout_info`:
      - **scarta box minuscoli** (`group_bbox` ≤ 2 pt su un lato);
      - **scarta tabelle senza `table_grid`**.
   8. (Opz.) `normalize_layout_boxes` per il modo HTML.
   9. `utils.clean_pictures(page, blocks)` — estende i box `picture`/`formula` con il
      testo/immagini/vettori intersecanti.
   10. `utils.add_image_orphans(page, blocks)` — aggiunge immagini/vettori "orfani"
       come box `picture`.
   11. **`utils.find_reading_order(page.rect, blocks, layout_information)`** → ordina i box (§5).
   12. Per ogni box costruisce un `LayoutBox`:
       - `picture`/`formula` → **pixmap** (embed base64 o file);
       - `table` → griglia (`get_table_details`) o HTML;
       - altri → `textlines` via `get_raw_lines`;
       - `title`/`section-header` → `max_fontsize`.
4. `update_header_tags(pages, header_fontsizes)` → mappa le font-size su livelli 1..6.

### 3.2 Classi di box (output del GNN)

```
text · picture · table · caption · title · section-header
page-header · page-footer · list-item · footnote · formula
```

Nel rendering `to_markdown`:
- `title` → `#`×livello;
- `section-header` → `#`×livello;
- `list-item` → `- ` con indentazione per livello;
- `footnote` → blockquote `> `;
- `picture`/`formula` → immagine base64 (+ eventuale testo interno);
- `table` → markdown/html;
- **tutto il resto** (incluso `caption`) → `text_to_md`.

`page-header`/`page-footer`: inclusi se `header/footer=True` (default), esclusi altrimenti.

### 3.3 Euristiche di rendering (mappa classe → markdown)

| funzione | euristica |
|---|---|
| `get_styled_text` | **bold** `**`, _italic_ `_`, ~~strikeout~~, `<u>`, `<mark>`, `<sup>`, `` ` `` mono; risolve **sillabazione** a fine riga/blocco; gestisce spaziatura apici |
| `text_to_md` | se la prima riga è **apice** → trattata come footnote; se **tutte monospaziate** → code block; altrimenti styled text |
| `list_item_to_md` | starter `- `; se la voce inizia con `N.`/`N)` → niente starter; più item nello stesso box separati quando una riga parte a sinistra (`x0 < x0-2`); bullet PUA rimosso; livello = indentazione |
| `footnote_to_md` | `> `; nuova nota quando riga **superscript** |
| `title_to_md` / `section_hdr_to_md` | `#`×`header_level` |
| `code_block_to_md` | recinto ``` |
| `picture_text_to_md` | marker `<!-- Start of picture text -->` … `<br>` |
| `fallback_text_to_md` | tabella "best effort" via `tabulate` (colonne = max span per riga) |
| `create_list_item_levels` | segmenta item contigui; **+1 livello** se `x0` aumenta di **>10 pt**; spezza il segmento al cambio colonna |

### 3.4 Serializzazione
- `ParsedDocument.to_markdown(header, footer, write_images, embed_images, ignore_code,
  page_separators, page_chunks)`.
- `to_json()` → JSON con `page_boxes` (`index`, `class`, `bbox`, `pos`) e dettagli.
- `to_text(table_format="grid", table_max_width, table_min_col_width)`.
- `page_chunks=True` → lista di dict per pagina: `metadata`, `toc_items`, `text`,
  `page_boxes` (+ `tables`/`images`/`graphics`/`words` solo nel legacy).

---

## 4. Il modello GNN (`pymupdf/layout`)

- Modello: **`BoxRFDGNN`**, feature set `rf+imf`, `table_grid_model_ver='V4'`,
  `use_gpu=False`, `n_workers=1` (di default).
- Modelli ONNX in `pymupdf/layout/resources/onnx/`:
  `layout_rf2.4.1.onnx`, `layout_imf1.onnx`, `layout_rf2.4.1+imf1.onnx`,
  `feature_imf1.onnx`, `feature_imf2.onnx`, e la famiglia
  `table_grid_model_v1|v1a|v1t|v2|v2c|v2_grid|v2_conn|v3|v4|v4_ep|v4_do`.
- Input per elemento: tipi `text`, `image`, `picture_clusters`, `vec_line`, `seg-image`;
  feature `rf` (raw features), `imf` (image features), `yf`, `jf`.
- Output: box classificati + **griglia tabella** (`h_lines`, `v_lines`) + classi.
- `pymupdf.layout.activate()` è idempotente (salta se `_get_layout` già callable).
- Multi-processo disponibile via `MultiProcessWrapper` (`n_workers≥2`).

---

## 5. Reading order (`utils.py`) — l'euristica chiave

`find_reading_order(page_rect, blocks, boxes, vertical_gap=12)`:

1. `filter_contained` → rimuove i box **contenuti** in altri.
2. Separa `page-header` e `page-footer` dal **body**.
3. `compute_reading_order(body)`:
   - **`cluster_stripes`**: se il layout è "multi-colonna pulito" → **una sola stripe**
     (si processa colonna per colonna); altrimenti divide la pagina in **strisce
     orizzontali** sui gap verticali (default `12` scalato per `page_height/800`).
     Un divisore è valido se **non interseca alcun box** ed è contenuto in **≤1**
     striscia di vettori.
   - **`cluster_columns_in_stripe`**: i box "solitari" (full-width) spezzano la stripe
     in sotto-strisce; poi raggruppa in colonne con gap orizzontale **1 pt**.
4. Ordine finale = header + body ordinato + footer.

Punti deboli noti (dai commenti del sorgente):
- non gestisce **blocchi di testo sovrapposti**;
- le **didascalie delle immagini** non sono riconosciute come tali;
- i "float" (box/tabelle/figure in mezzo a colonne) possono rompere l'ordine.

> **Implicazione strategica**: questo è **il punto n.1 dei difetti E2E** (ordine, 16 casi)
> ed è **euristica pura** → possiamo sostituirla/migliorarla nel nostro `ir_layout.py`
> **senza toccare il GNN**.

---

## 6. Tabelle

- **Percorso layout**: la griglia viene dalla **GNN** (`table_grid`). `get_table_details`
  costruisce `row_count`/`col_count`/`cells`/`extract`/`markdown`.
  `utils.extract_cells` estrae il testo per cella includendo i caratteri con **overlap ≥ 50%**
  (con styling markdown); `utils.table_to_markdown` genera la tabella GitHub.
  **Tabella senza griglia → scartata** in `parse_document`.
- **Percorso legacy**: `page.find_tables(strategy=...)`, default **`lines_strict`**
  (ignora i colori di sfondo); scarta tabelle con `<2` righe o colonne;
  `t.to_markdown(clean=False)`.
- **Modo HTML (opt-in `table_output="html"`)**: `helpers.table_html.page_html_tables`
  ricostruisce le tabelle (con repair via `find_tables`), `normalize_layout_boxes`
  assegna/spezza i box testo attorno alle tabelle.
- `to_text`: `tabulate` con `table_format` (default `grid`) e wrapping
  (`wrap_table_for_tabulate`, `max_width=100`, `min_col_width=10`).

---

## 7. OCR

### 7.1 Decisione (`ocr/analyze_page.py`)
Modello ONNX **`ocr_decision_model.onnx`**, soglia **0.93**, 22 feature:

```
Text:  num_spans, text_area, text_density, avg_span_height, avg_span_width
Layout:num_blocks, num_images, image_area, image_density,
       num_small_oblique_vectors, log_vector_density,
       page_sobel_energy, page_sobel_entropy, page_sobel_var, page_white_ratio
Pixel: img_fft_energy, img_fft_ratio, img_black_ratio, img_white_ratio,
       img_sobel_energy, img_sobel_orientation_entropy, img_sobel_local_variance
```

Motivi di attivazione (`reason`):
- `chars_bad` — >**10%** di caratteri illeggibili (Replacement U+FFFD);
- `ocr_spans` — pagina già OCR (render mode 3 / `GlyphLessFont` / `alpha=0`);
- `vec_text` — glifi "sospetti" disegnati come vettori;
- `img_text` — immagine che probabilmente contiene testo (prob ≥ 0.93).

### 7.2 Plugin (strategia ibrida)
| plugin | detection | recognition |
|---|---|---|
| `rapidocr_api` | RapidOCR | RapidOCR |
| `paddleocr_api` | PaddleOCR | PaddleOCR |
| `tesseract_api` | Tesseract | Tesseract |
| `rapidtess_api` | RapidOCR | Tesseract |
| `paddletess_api` | PaddleOCR | Tesseract |

Ordine di scelta: `rapidtess` → `paddletess` → `rapidocr` → `paddleocr` → `tesseract`.
**Nel nostro ambiente è selezionato Tesseract** (`"Using Tesseract for OCR processing."`).
La strategia **ibrida** pulisce il testo esistente, renderizza solo le zone mancanti,
OCR e reinserisce il testo (≈-50% tempo).

### 7.3 Modalità (`OCRMode`)
```
NEVER=0 · SELECT_DROP_OLD=1 · SELECT_KEEP_OLD=2 · FORCE_DROP_OLD=3 · FORCE_KEEP_OLD=4
```
Percorso layout: default `SELECT_KEEP_OLD`; `force_ocr=True` → `FORCE_KEEP_OLD`.
`ocr_dpi` default **150** nel layout (300 nel legacy).

---

## 8. Percorso LEGACY (fallback, `use_layout(False)`)

Attivabile con `pymupdf4llm.use_layout(False)`. Componenti:

- **`pymupdf_rag.to_markdown`**: pipeline euristica per pagina:
  - `IdentifyHeaders` (header per **font-size**: body = size più frequente, fino a 6 livelli)
    o `TocHeaders` (header dal **TOC**);
  - background color (4 angoli), immagini (`get_image_info`, dedup per contenimento, max 30,
    `image_size_limit=0.05`), tabelle (`find_tables`), grafica vettoriale
    (`cluster_drawings` + `is_significant`), testo (`column_boxes`), `write_text` (styling);
  - `write_text` gestisce header, code, liste (`startswith_bullet`), link (`resolve_links`).
- **`multi_column.column_boxes`**: rileva colonne dai **blocchi di testo**; separa sfondi
  diversi (box colorati); 3 fasi di join; **sorting custom** `(y,x)` con "left block".
- **`get_text_lines.get_raw_lines`**: sintetizza righe dagli span (tolleranza 3 pt),
  unisce span spezzati (gap ≤ 10% font), ordina L→R. **Usato anche dal percorso layout.**

Parametri **solo legacy** (ignorati **silenziosamente** nel layout, assorbiti da `**kwargs`): `hdr_info`, `table_strategy`,
`margins`, `graphics_limit`, `ignore_graphics`, `ignore_images`, `detect_bg_color`,
`image_size_limit`, `fontsize_limit`, `ignore_alpha`, `use_glyphs`, `extract_words`.

---

## 9. API pubblica

| API | note |
|---|---|
| `to_markdown(doc, **kw)` | Markdown; `page_chunks=True` → lista dict |
| `to_json(doc, **kw)` | **solo layout**; JSON con `page_boxes` |
| `to_text(doc, **kw)` | **solo layout**; `table_format="grid"` |
| `use_layout(yes=True)` | accende/spegne il GNN |
| `get_key_values(doc)` | campi **form** (AcroForm) con valore e pagine |
| `LlamaMarkdownReader()` | integrazione LlamaIndex (richiede `llama-index`) |
| `version` / `__version__` | 1.28.2 |
| CLI `python -m pymupdf4llm INPUT --out DIR [--format md|json|txt] …` | conversione file/dir |
| `batch_converter.convert_batch(...)` | batch multi-processo (strategie sequential / process-pool / streaming / persistent) |
| `worker_sizing.auto_workers(...)` | n. worker da CPU/RAM/OCR |

### Parametri principali di `to_markdown`
`pages`, `header`, `footer`, `write_images`, `embed_images`, `image_path`, `image_format`,
`dpi`, `ocr_dpi`, `ocr_language`, `ocr_function`, `use_ocr`, `force_ocr`, `force_text`,
`ignore_code`, `ignore_images`, `ignore_graphics`, `margins`, `page_chunks`,
`page_separators`, `show_progress`, `table_strategy`*, `table_output`, `graphics_limit`*,
`image_size_limit`*, `hdr_info`*, `fontsize_limit`*, `detect_bg_color`*, `ignore_alpha`*,
`use_glyphs`*, `extract_words`*, `page_width`, `page_height`.
(* = solo percorso legacy.)

---

## 10. Come lo usa il nostro progetto

- **`main.py::_extract_pymupdf4llm`** (pipeline `current`):
  `pymupdf4llm.to_markdown(path, pages=[page], ocr_language=lang)` con pre-fix di
  **rotazione sideways** (`layout_engine.detect_sideways_rotation`).
- **`ir_layout.py`** (pipeline `ir`):
  `pymupdf4llm.to_markdown(doc, pages=[i], page_chunks=True)[0]` — **una sola passata** —
  e lavora sui **`page_boxes`** (`class`, `bbox`, `pos`) prodotti dal percorso layout.
  Quindi l'IR è costruito **sopra il GNN**, non sulle euristiche classiche.
- Il commento in `ir_layout.py` ("modello di layout di PyMuPDF (`page_boxes`)") è coerente
  con quanto sopra.

---

## 11. Mappa: difetti E2E (campione 60 pag.) → meccanismo del motore

| difetto E2E | dove nasce nel motore | leva |
|---|---|---|
| **ordine di lettura** (16) | `utils.find_reading_order` (euristica sui box GNN) | riscrivibile nel nostro IR |
| **contenuto tabelle** (7) | griglia GNN assente/errata → tabella scartata; `extract_cells` overlap 50% | re-derivare griglia; fallback `find_tables`/`find_virtual_lines` |
| **indici** (4) | classe/ordine dei box; fallback `current` | percorso dedicato indice |
| **figure mancanti** (4) | classificazione `picture` + `clean_pictures`/`add_image_orphans` + `image_size_limit` | rilevare immagini nei box |
| **figure duplicate/bleed** | `clean_pictures` estende i box con testo intersecante | dedup geometrica nel nostro IR (già fatto) |
| **titoli/header box** (2) | `title`/`section-header` vs `text` (classificazione GNN) | post-fix nel nostro IR |
| **equazioni** | classe `formula` → immagine | già gestito (embed) |
| **marginalia** | `page-header`/`page-footer` inclusi di default | filtrare nel nostro IR (già fatto) |
| **item di elenco vuoti** | `list_item_to_md` con span vuoti | fix cosmetico |

---

## 12. Riferimenti

**Sorgente locale** (`.venv/lib/python3.14/site-packages/`):
- `pymupdf4llm/__init__.py`, `helpers/document_layout.py`, `helpers/pymupdf_rag.py`,
  `helpers/multi_column.py`, `helpers/get_text_lines.py`, `helpers/utils.py`,
  `ocr/analyze_page.py`, `ocr/compute_ocr_features.py`, `ocr/__init__.py`,
  `batch_converter.py`, `worker_sizing.py`, `__main__.py`
- `pymupdf/layout/{__init__.py,DocumentLayoutAnalyzer.py,pymupdf_util.py,resources/onnx/}`

**Documentazione**:
- PyMuPDF4LLM: https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/
- API: https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/api.html
- OCR plugins: https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/ocr-plugins.html
- PyMuPDF Layout (PyPI): https://pypi.org/project/pymupdf-layout/
- `find_tables` / strategie: https://pymupdf.readthedocs.io/en/latest/page.html#Page.find_tables
- Appendix text extraction: https://pymupdf.readthedocs.io/en/latest/app1.html

---

## 13. Verifiche runtime (evidenza empirica, non solo lettura del codice)

Ambiente: `.venv` (Python 3.14), pymupdf 1.28.2, pymupdf4llm 1.28.2.

| verifica | comando/strumento | esito |
|---|---|---|
| Percorso attivo | monkeypatch di `document_layout.parse_document` e `pymupdf_rag.to_markdown` + chiamata a `pymupdf4llm.to_markdown` | **`layout=1, legacy=0`** → percorso GNN confermato |
| Flag | `pymupdf4llm._use_layout` | `True`; `IdentifyHeaders` **assente** |
| Classi GNN | `to_markdown(..., page_chunks=True)["page_boxes"]` su `su18 p100` | `page-header:1, list-item:38` |
| Proprietà layout | `page.get_layout(return_raw=True)` (dopo `import pymupdf4llm`) | ritorna `None` ma popola `page.layout_information` (46 box) |
| Chiavi di un box GNN | `layout_information[0]` | `bboxes, class_name, group_bbox, group_class, indicies, tie_class` |
| Modelli ONNX bundled | `du -sh pymupdf/layout/resources/onnx` | **49 MB**, 19 file `.onnx` |
| Tabelle | `ha22 p230` | classe `table:1`, grid `(h=37, v=1)` → **1 colonna** (degrado) |
| Tabelle | `ce24 p2480` | `table:2` ma grid `(h=0, v=0)` → **griglia degenere** |
| OCR | messaggio del motore | `Using Tesseract for OCR processing.` |

**Nota correttiva**: il primo test diretto di `page.get_layout()` era **viziato** (importavo
solo `pymupdf`, senza `pymupdf4llm`, quindi il modello non era attivato). Ripetuto dopo
`import pymupdf4llm`, il layout funziona e popola `layout_information`.

**Conseguenza**: la griglia tabella del GNN **può essere presente ma degenere**
(1 colonna, o 0 righe/colonne). Non basta "c'è/non c'è": la leva è **validare e
ricostruire** la griglia.
