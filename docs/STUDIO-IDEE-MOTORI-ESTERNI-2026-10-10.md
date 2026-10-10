# Studio — Idee dai motori geometrici esterni per il nostro engine (master)

> **Scopo.** Documento **complesso e approfondito** che raccoglie *tutto il
> rilevante* dei quattro progetti esterni (papero, OpenDataLoader, PdfPig,
> pdfplumber/pdfminer), **classificato e ordinato per i nostri interessi di
> miglioramento dell'engine** (`ir_layout` + `main._apply_ir_on_page`), non per
> progetto.
>
> **Come si usa.** §1 è la tassonomia (le nostre classi di difetto ↔ le aree di
> idea). Le sezioni **A–I** sono le aree, ciascuna con: cosa fanno i motori,
> soglie esatte, `file:riga`, e "cosa portiamo". §J è la **tabella delle idee
> ordinate per priorità**. §K i caveat. §L l'appendice (mappa file → funzione).
>
> **Fonti** (KB locale in `other-engines/`, vedi `other-engines/README.md`):
> - **papero** `1b076dad`, MIT — analisi completa in
>   `STUDIO-PAPERO-PDF-EXTRACTOR-2026-10-10.md`.
> - **OpenDataLoader** `3dfb3b7`, Apache-2.0.
> - **PdfPig** `91ddd23`, Apache-2.0.
> - **pdfplumber** `4c64b92`, MIT; **pdfminer.six** `a18de2a`, MIT.
>
> **Rimandi interni.** `STUDIO-MOTORI-GEOMETRICI-ESTERNI-2026-10-10.md` (prima
> ricognizione), `STUDIO-E2E-VERDETTO-2026-10-11.md` (difetti aperti),
> `HANDOFF-2026-10-11.md` §7, `MIGLIORIE-MOTORE.md`, `STUDIO-PAGE-MODEL-2026-10-11.md`.

---

## 1. Tassonomia: nostre classi ↔ aree di idea

| # | nostra classe di difetto (dove) | area di idea (§) | motori |
|---|---|---|---|
| 1 | **Residuo word-level** glue/split/bold-init (dominante) | **C** Testo/glifo | papero, PdfPig, pdfplumber, pdfminer, ODL |
| 2 | **Colonne interlacciate** (`fe22 p1038`) | **A** Ordine | ODL, PdfPig, pdfminer, papero |
| 3 | **Tabella complessa**, ordine (`ce24 p480`, grave) | **B** Tabelle | pdfplumber, ODL, papero, PdfPig |
| 4 | **Box/banda a tutta larghezza** | **A** Ordine | ODL, papero |
| 5 | **Figura vuota** (`su19 p1050`), figure mancanti | **D** Figure | papero, ODL, PdfPig |
| 6 | **Prosa formula** (`arxiv p30`) | **C** (math) | papero |
| 7 | **Etichette/didascalie staccate** | **D** | papero, ODL |
| 8 | **Heading** sbagliati/spezzati | **E** Struttura | ODL, papero, pdfminer |
| 9 | **Liste** (bullet, proseguimenti, cross-page) | **E** | ODL, papero |
| 10 | **Header/footer/furniture**, rumore | **F** Furniture | papero, ODL, PdfPig |
| 11 | **Duplicati** (prosa+immagine, testo doppio) | **C**/**F** | PdfPig, ODL, pdfplumber, papero |
| 12 | **Falsi positivi del detector / metro** | **H** Validazione | papero, pdfplumber, ODL |
| 13 | **Spazi/giunzioni errate** (glue di span) | **C** | ODL, pdfplumber, pdfminer |
| 14 | **Testo ruotato** | **A**/**D** | PdfPig, papero |

---

## A. Ordine di lettura (colonne, bande, tabelle)

### A.1 OpenDataLoader — XY-Cut++ (`XYCutPlusPlusSorter.java`, 651 righe)

`sort(objects)` → `sort(objects, beta=2.0, densityThreshold=0.9)`. Quattro fasi:

- **Fase 1 — pre-masking cross-layout** (`identifyCrossLayoutElements:146`):
  servono ≥3 oggetti; `threshold = beta * maxWidth`; un oggetto è cross-layout
  se `width ≥ threshold` **e** `hasMinimumOverlaps(obj, objects, 2)` (overlap
  orizzontale ≥ `OVERLAP_THRESHOLD=0.1` con ≥2 elementi). Gli elementi
  cross-layout sono estratti e re-inseriti alla fine per Y.
- **Fase 2 — densità sceglie l'asse** (`computeDensityRatio:260`):
  `density = sum(area) / area(regione)`; `preferHorizontalFirst = density > 0.9`.
- **Fase 3 — segmentazione ricorsiva** (`recursiveSegment:331`): taglio H e V a
  proiezione; `MIN_GAP_THRESHOLD=5.0`; sceglie il gap maggiore; fallback
  `sortByYThenX`; guardia anti-ricorsione.
- **Fase 4 — merge** (`mergeCrossLayoutElements:590`): fusione per `topY`
  (PDF Y-up).
- **Narrow-outlier retry** (`findBestVerticalCutWithProjection:406`): se il gap
  verticale < 5, si scartano gli elementi `width < 10%` della regione
  (`NARROW_ELEMENT_WIDTH_RATIO`) e si ripete → *"page numbers, footnote markers
  may bridge an otherwise clear column gap"*.

Parametri: `beta=2.0`, `density=0.9`, `overlap=0.1`, `minOverlap=2`,
`minGap=5.0`, `narrowRatio=0.1`. *(Dichiarato "senza priorità semantiche".)*

### A.2 PdfPig — Allen-relations graph (`UnsupervisedReadingOrderDetector.cs`)

- Grafo diretto completo: edge `i→j` se "i prima di j" (`BuildGraph:145`)
  tramite le **13 relazioni di Allen** su X e Y (`IntervalRelationHelper.GetRelationX:27`
  / `GetRelationY:112`), tolleranza `T=5`.
- Tre regole (`SpatialReasoningRules:18`): `Basic` (L→R,T→B), `RowWise`,
  `ColumnWise` (default). Rendering order (`TextSequence`) come **tie-break**
  (`GetBeforeInRendering:176`).
- **Estrazione greedy a max out-degree** (`Get:120`): prende il nodo più "prima"
  di tutti, lo emette, lo rimuove dagli altri; ripete.
- Tolleranza `T=5` = "coordinate uguali" (isteresi nelle relazioni: `:30-31`).

### A.3 pdfminer — `boxes_flow` + albero agglomerativo (`layout.py`)

- **`boxes_flow` ∈ [-1,1]** (default 0.5): chiave di sort
  `(1 - boxes_flow)*x0 - (1 + boxes_flow)*(y0+y1)` (`LTTextGroupLRTB.analyze:674`).
  `+1` → righe (solo Y); `−1` → colonne (solo X). Con 0.5 la Y pesa 3× la X.
- **Albero**: `group_textboxes` (`:814`) con **clustering agglomerativo** (heap,
  closest-first), distanza = **area dell'unione − aree** (`dist:839`) e guardia
  **"nessun oggetto in mezzo"** (`isany:861`).
- **`IndexAssigner` (`:35`)** numera i textbox nell'ordine dei gruppi; sort
  finale per index (`analyze:907`). Con `boxes_flow=None`: sort geometrico
  `(1,-y0,x0)` per verticale/`(0,-x1,-y0)`.

### A.4 papero — XY-cut column-aware (`layout.py`)

- `reading_order:1244` + `_split_columns:1194`: non taglia le righe di una
  tabella (`cuts_table`), guarda **due bande avanti**, gutter noti.
- Gutter a **profilo di copertura** (`columns.gutters:18`): blocchi a tutta
  larghezza contati a parte; valle tra picchi = gutter.

### A.5 Ordine per orientamento e primitive di allineamento (PdfPig)

- `ReadingOrderHelper.OrderByReadingOrder` (word `:18`, line `:93`): switch sui
  4 orientamenti (H/180/90/270) + fallback a quadranti per rotazioni libere;
  word per `X`, righe per `Y` (`:115-125`).
- `TextEdgesExtractor` (vedi B.5): tre assi (left/mid/right) per colonne.
- `WhitespaceCoverExtractor` (vedi B.5): gutter come rettangoli vuoti massimali.

### A.6 Cosa portiamo (Ordine)

- **Quattro ordini indipendenti** = metro/riferimento: XY-Cut++ (ODL),
  Allen-graph (PdfPig), boxes_flow-tree (pdfminer), XY-cut coverage (papero).
  → 1–2 invarianti d'ordine (come page model / I1/I3) per le classi
  colonne/tabelle.
- **Cross-layout = larghezza + overlap ≥2** (ODL): criterio selettivo per i
  nostri box/banda a tutta larghezza.
- **Narrow-outlier retry** (ODL): fix locale su marker che fanno da ponte
  (`fe22 p1038`).
- **boxes_flow** (pdfminer): un solo knob riga/colonna.

---

## B. Tabelle (ruled / unruled / complesse)

### B.1 pdfplumber — `TableFinder` (`table.py`)

- **Strategia per asse** (`TABLE_STRATEGIES`, `:460`): `lines` (tutte le linee,
  incl. rect) / `lines_strict` (solo "line") / **`text`** / `explicit`.
- Pipeline (`TableFinder:577`): `get_edges` → `edges_to_intersections:207` →
  `intersections_to_cells:234` → `cells_to_tables:297`.
- **`text` strategy** — tabelle **senza bordi** (`words_to_edges_h:101`,
  `words_to_edges_v:144`): righe = cluster di `top` di ≥ `min_words_horizontal`
  (=1) parole (+ linea inferiore); colonne = cluster di `x0`, `x1` **e centro**
  di ≥ `min_words_vertical` (=3) parole.
- **Robustezza**: `snap_edges:21` (snap entro `snap_tolerance=3` alla media),
  `join_edge_group:39` (`join_tolerance=3`), `intersections_to_cells` crea la
  cella solo se le linee **si connettono ai 4 angoli** (`edge_connects:244`) →
  niente celle fantasma; `cells_to_tables` scarta tabelle ≤1 cella.
- **`TableSettings:485`**: `snap/join/intersection_*_tolerance`,
  `edge_min_length=3`, `edge_min_length_prefilter=1`, `min_words_vertical=3`,
  `min_words_horizontal=1`, `text_settings`.
- `dedupe_chars:573` (char duplicati), `doctop` (Y cumulativa multi-pagina),
  `structure_tree` (tagged).

### B.2 OpenDataLoader — processor di tabella

- **`TableBorderProcessor`**: mappa il contenuto sulle celle; **spezza il
  TextChunk a cavallo di più celle** (`:138-145`); LineArt con intersezione ≥90%
  (`LINE_ART_PERCENT=0.9`) consumata nella cella; profondità max annidata 10
  (`MAX_NESTED_TABLE_DEPTH`); unisce tabelle adiacenti con larghezze compatibili
  (`NEIGHBOUR_TABLE_EPSILON=0.2`, `checkNeighborTables:201`).
- **`ClusterTableProcessor`**: tabelle borderless via clustering; **spezza i
  text chunk sugli spazi** prima (`splitTextChunkByWhiteSpaces:61`); riga 0 =
  header (`:88`); è **whole-document e sequenziale** (`DocumentProcessor:356`).
- **`TableStructureNormalizer`**: ricostruisce tabelle **under-segmented**;
  soglie `MAX_UNDERSEGMENTED_ROWS=2`, `MIN_UNDERSEGMENTED_COLUMNS=3`,
  `MIN_UNDERSEGMENTED_TEXT_LINES=8`, `MIN_ROW_BAND_MISMATCH=2`,
  `OVERSIZED_CELL_LINE_COUNT=4`, epsilon banda `max(3.0, ...*0.6)`; accetta solo
  se **"quality improves"** (`isReplacementQualityBetter:258`: più righe piene,
  non meno colonne, ordine monotono).
- **`AbstractTableProcessor`**: pre-filtro "pagina con possibile tabella"
  (`areSuspiciousTextChunks:119`: overlap verticale o gap orizzontale
  > `3×altezza`); dedup tabelle con **intersezione > 1%** (`TABLE_INTERSECTION_PERCENT`).
- **`SpecialTableProcessor`**: pattern di etichette → tabelle **key-value** a 2
  colonne; riga senza `:` = cella full-width (span 2).

### B.3 papero — tabelle geometriche (`layout.py`)

- `ruled_regions:1314` (union-find sulle regole), `_projection_columns:1431`
  (canali di whitespace mai attraversati), `_logical_rows:1503` (righe di
  sezione/celle che vanno a capo con **bimodalità dei gap** e continuazione per
  minor numero di colonne), `unruled_table:1576`, `_grid_table_from_page:227`.
- Tabelle **ruotate** → immagine (`_emit_clip`); pannello-raster classificato
  come tabella → immagine + rimozione (`_raster_figure_table_rects`,
  `_strip_table_blocks`).
- **Tabella come artefatto parallelo** (`_attach_images:2491`): markdown la
  griglia, crop nel JSON/ZIP.

### B.4 pdfminer / PdfPig — primitive

- pdfminer riconosce i rettangoli griglia con `has_square_coordinates`
  (`converter.py:189-192`); merge con `dist`/`isany` (vedi A.3).
- **PdfPig `TextEdgesExtractor`** (`:35`): tre assi `Left/Mid/Right`, arrotonda
  a intero, `minimumElements=4`, spezza un edge sui **"cuttings"** (parole a
  tutta larghezza che lo attraversano) → ottimo per intestazioni di tabella.
- **PdfPig `WhitespaceCoverExtractor`** (`:95`): rettangoli vuoti massimali
  (ostacoli = word + immagini); `whitespaceFuzziness=0.15`; `minWidth/minHeight`
  = moda glifi × **1.25**; scoring `Area × Height/4` (preferisce gutter alti);
  `maxRectangleCount=40`.

### B.5 Cosa portiamo (Tabelle)

- **Trigger "tabella difficile"** (selettivo, non rimpiazzo): usa `text
  strategy` di pdfplumber (allineamenti), `_logical_rows` di papero (sezioni/
  celle che vanno a capo), `TextEdges`+`WhitespaceCover` di PdfPig (edge e
  gutter). Solo allora → ricostruzione o immagine.
- **Regole**: spezza i chunk sui bordi di cella (ODL), snap/join (pdfplumber),
  **accetta una ricostruzione solo se migliora** (ODL `quality better`),
  dedup per intersezione (ODL 1%).
- **Tabelle non-bordate**: `words_to_edges_h/v` (pdfplumber) e cluster sui
  chunk spaziati (ODL).

---

## C. Testo: glifo-level, glue/split, dedup, accenti, math

### C.1 papero — ricostruzione glifo-level (`layout.py`)

- `read_chars:575` (ogni glifo con loose box/font/hint spazio-a-capo),
  `build_spans:847`, `_segments:209` (super/subscript per **shift di baseline**;
  word-gap **relativo al tracking**; giustificata), `words_of:1417`.
- `_join_markers:1117` (bullett+testo = uno span). `_hard_break:1983` (a-capo
  voluto vs wrap). `_OPERATOR_GAP` (spaziatura operatori).

### C.2 PdfPig — parole e spaziature adattive

- `NearestNeighbourWordExtractor:44`: raggruppa i glifi sui punti di
  **baseline** (`StartBaseLine`/`EndBaseLine`), distanza **Manhattan**
  (axis-aligned) / **Euclidea** (altro), soglia = **20% di max(width,
  pointSize)** (×2 per orientamento `Other`), **5 bucket di orientamento**,
  filtri (spazi).
- `DocstrumBoundingBoxes`: stima **within-line** e **between-line** da
  **istogrammi** delle distanze nearest-neighbour (bounds angolari
  `[-30°,30°]`/`[45°,135°]`; moltiplicatori `×3.0`/`×1.3`; `binSize=10`).
- `DuplicateOverlappingTextProcessor:35`: dedup lettere per `(testo, font)` +
  prossimità `tolerance = width / len / 3`; la lettera mantenuta diventa
  **bold** (`:84`).

### C.3 pdfplumber — parole/righe (`utils/text.py`)

- Tolleranze `x_tolerance=3`, `y_tolerance=3` (o **relative** via
  `x_tolerance_ratio * size`, `:625-631`).
- `char_begins_new_word:516`: intra-linea **end→begin**, inter-linea **top→top**
  (`:542-591`).
- `collate_line:771` (spazio implicito se gap > tolerance), `dedupe_chars:794`
  (chiave `(upright,text,fontname,size)`, cluster y poi x, `tolerance=1`).
- `to_textmap`: modalità **layout monospace** (densità `x=7.25`, `y=13`).

### C.4 pdfminer — grouping e spazi relativi (`layout.py`)

- `LAParams` relativi al font: `line_overlap=0.5`, `char_margin=2.0`,
  `word_margin=0.1`, `line_margin=0.5`.
- `group_objects:703`: char→righe con `halign`/`valign` (overlap + margini
  relativi). `LTTextLineHorizontal.add:505`: inserisce `LTAnno(" ")` se il gap >
  `word_margin * max(w,h)`.
- `group_textlines:780` (vicini: stessa altezza ±d, left/right/**center**
  allineati, `d = line_margin*height`).

### C.5 OpenDataLoader — righe, pulizia, spazi

- `TextLineProcessor:43`: soglia ONE_LINE_PROBABILITY=0.75; spazio se gap >
  `fontSize * textLineSpaceRatio` (`:91`); segnala i chunk **preceduti da
  whitespace** nel flusso PDF (`:50`,`:63`) per reintrodurre spazi; **collega i
  bullet LineArt** alla riga (`linkTextLinesWithConnectedLineArtBullet:127`).
- `TextProcessor`: `filterTinyText` (height≤1, size≤5), `mergeCloseTextChunks`
  (stesso stile + stessa baseline + gap < 0.1×altezza), `removeSameTextChunks`
  (stesso testo, dimensioni vicine, **intersezione > 0.5**),
  `removeTextDecorationImages` (offset proporzionali all'altezza).
- `ContentFilterProcessor.getFilteredContents:53`: **ordine di pulizia**
  dedup → decoro → tiny → fuori-pagina → merge → trim → spazi → split →
  replacement → background; warning replacement char ≥ **0.3**.

### C.6 Accenti/ligature/apici (papero `symbols.py`)

- `_compose_accents:921` + `compose_accent:183`: accenti disegnati come glifi
  (`Computa¸ca˜o` → `Computação`); `LIGATURES` (`ﬁ`→`fi`); `to_superscript`/
  `to_subscript`; `_STATE` per `CO2(g)`.
- `tex_fonts` (OML/OMS/OMX): decodifica dei font TeX senza ToUnicode; `NEGATED`
  (`≠` come slash+`=`).
- `_typeset_math:303`: frazioni/radici **disegnate** → testo + LaTeX, **e
  rimuove le righe-frazione** dalle regole (non diventano bordi di tabella).

### C.7 Cosa portiamo (Testo)

- **Vocabolario glifo-level pulito**: nn-word extractor (PdfPig) + papero
  glifo-level → sblocca il de-glue/de-split parcheggiato (la "condizione per
  riprovare" era un vocabolario senza sillabazione/figure/chrome).
- **Dedup**: `DuplicateOverlappingTextProcessor` (PdfPig), `dedupe_chars`
  (pdfplumber), `removeSameTextChunks` (ODL).
- **Spazi/giunzioni**: fine→inizio intra, top→top inter (pdfplumber), spazi
  proporzionali al font (ODL), `word_margin` (pdfminer).
- **Accenti disegnati** e **math tipografica** (papero).
- **Ordine di pulizia** di ODL come checklist.

---

## D. Figure, grafici, didascalie

### D.1 papero — figure (`layout.py`)

- `detect_figures:1741` (raster + grafici vettoriali, etichette assorbite),
  `chart_regions:1677` + `_plot_frames:1648` (grafici senza bbox immagine),
  `without_rules_in:1704` (toglie le regole dei grafici), `_label_of:1720`
  (etichette di assi/legenda), `_compose_figures:2907` (pezzi + didascalie → un
  blocco), `_visible:2437` (bianco-su-bianco, fake-bold via render),
  `_link_captions:3036` (didascalia ↔ figura/tabella per prossimità).

### D.2 OpenDataLoader — didascalie (`CaptionProcessor.java`)

- `processCaptions:49`: linking caption↔figura con probabilità;
  `CAPTION_PROBABILITY=0.75`; offset = `1×fontSize` (`:137-146`); cerca **sopra
  e sotto** e sceglie la più probabile; `SUBTLE_IMAGE_RATIO_THRESHOLD=0.01`
  (scarta immagini "striscia"); caption anche per tabelle-anonimizzate-come-figura.

### D.3 PdfPig / pdfplumber

- PdfPig `TextBlock.GetBoundingBoxOther:260` (OBB per testo ruotato),
  `TextLine` fit lineare della baseline (`:255`).
- pdfplumber: `within_bbox`/`crop_to_bbox`/`clip_obj` per testo dentro/fuori una
  regione (`utils/geometry.py:76-126`).

### D.4 Cosa portiamo (Figure)

- Rilevare **grafici vettoriali** (non solo raster) e assorbire le etichette
  (papero) → `figure_missing`.
- Didascalie con **probabilità** e offset proporzionale al font (ODL).
- Filtro immagini "striscia" (aspect ratio) e bianco-su-bianco via render.

---

## E. Heading, liste, gerarchia

### E.1 OpenDataLoader

- `HeadingProcessor`: `HEADING_PROBABILITY=0.75` + boost di **rarità font
  size/peso** + bullet (`:72-80`); livelli raggruppando per **TextStyle**
  (`:201-214`); gestisce heading dentro celle e liste-degradate-a-heading.
- `ListProcessor`: `LIST_ITEM_PROBABILITY=0.7`; `maxXGap = fontSize*0.3`;
  `MAX_LIST_INTERVAL_LOOKBACK=500`; **`checkNeighborLists:498`** unisce liste
  spezzate da testo/pagina/colonna; ordered vs unordered per "stesso left/altezza".
- `LevelProcessor`: **stack di livelli con pop dei più profondi** (`:101-106`);
  **primo H1 = "Doctitle"** (`:118-120`); livelli ereditati da liste/tabelle.
- `ParagraphProcessor`: `DIFFERENT_LINES_PROBABILITY=0.75`; casi espliciti
  **prima/ultima riga di paragrafo giustificato** (`:132-157`,`isFirstLineOfBlock`);
  non fonde le righe bullet.

### E.2 papero

- `_classify_headings:2761` (dimensione/formato + hint tagged + `_front_matter`
  per autori), `_merge_heading_lines:2709` (titolo su due righe),
  `_bold_section:2748`, `_section_number:2617`, `_absorb_formula_bits:2834`.

### E.3 pdfminer

- `boxes_flow`/gruppi e `IndexAssigner` (vedi A.3) danno la gerarchia d'ordine;
  `get_writing_mode` per testo verticale.

### E.4 Cosa portiamo (Struttura)

- **Probability-based** per heading (ODL): somma di segnali + soglia, meglio
  della sola taglia-font.
- **Rarità del font** come segnale di titolo.
- **Liste**: unione "neighbor" cross-blocco/pagina (ODL); tolleranza
  `fontSize*0.3`.
- **Gerarchia**: stack di livelli (ODL), titolo su due righe (papero).

---

## F. Header/footer, furniture e rumore

### F.1 papero

- `_mark_furniture:2565`: frequenza+posizione, `zone` (margin/header/footer),
  chiave con **denominatore** per i logo, soglia **0.3** (header alternati
  pari/dispari); `_furniture_key:2558` normalizza le cifre.
- `_normalize_sizes:2637` (dimensioni da `pt`, non dalle metriche dei glifi).

### F.2 OpenDataLoader

- `HeaderFooterProcessor`: **posizione** (header: `bottomY ≥ 2/3 h`; footer:
  `topY ≤ 1/3 h`) + **ripetizione** su pagine a distanza **1 e 2** + gap
  verticale `MAX_HEADER_FOOTER_GAP=30`; riconosce **numerazione progressiva**
  (Alfa/Roman/Arabic con increment 1 o 2).
- `HiddenTextProcessor`: **contrasto < 1.2** = testo nascosto (richiede render,
  quindi sequenziale).
- `TextDecorationProcessor`: strike/underline distinguendo dal bordo tabella
  (rapporto larghezza ≤1.5; spessore ≤25% altezza; underline sotto baseline,
  strike al centro).

### F.3 PdfPig

- `DecorationTextBlockClassifier` (`:123`): confronto **cross-page** di
  similarità **contenuto × geometria** contro pagina precedente e successiva
  (media); soglia **0.25**, `n=5` blocchi per pagina; **masking dei numeri/romani
  con `"@"`** prima del confronto; edit distance normalizzata; salto pagina
  pari/dispari se `pages>3`.
- `OrderedSet` (dedup ordinato).

### F.4 Cosa portiamo (Furniture)

- **Candidato per posizione + conferma per ripetizione** (ODL/PdfPig/papero),
  con **masking dei numeri** (PdfPig) e numerazione progressiva (ODL).
- Testo nascosto per contrasto (ODL) e per colore (papero `_visible`).
- Strike/underline da `get_drawings` (ODL) senza confonderli con bordi tabella.

---

## G. Pulizia/rumore generico

- **papero** `cleaning.py` (solo righe ai bordi; de-sillabazione PDFium
  `\x02`/`U+FFFE`), `symbols.py` (ligature, PUA, spazi anomali, invisibili).
- **ODL** `ContentFilterProcessor` (ordine di pulizia, C.5) + `TextProcessor`
  (tiny/decorazioni/duplicati).
- **pdfplumber** `dedupe_chars`.
- **PdfPig** `DuplicateOverlappingTextProcessor`.

---

## H. Validazione, struttura, qualità (riferimenti indipendenti)

- **papero `fidelity.py`**: 5 segnali pesati (text 0.4, order 0.2, tables 0.15,
  figures 0.1, formulas 0.15); **`_coverage:148` senza separatori** + `_fold:132`
  (NFKD, ignora combining) → tollera sillabazione/accenti/`10⁹`; `_backwards:174`
  (ordine) e `_overlap:183` (blocchi sovrapposti); check "caption senza tabella/
  figura". Antidoto ai **falsi positivi** del nostro `text_integrity`.
- **pdfplumber `structure.py`**: albero taggato (`PDFStructTree`), doppio
  percorso **ParentTree per-pagina** vs root documentale (`:184-215`);
  `all_mcids:118` e `element_bbox:466` (tag↔MCID↔bbox); `role_map`/`class_map`.
- **ODL**: `TaggedDocumentProcessor`, `AutoTaggingProcessor`,
  `HybridDocumentProcessor`, `ContentSanitizer`, `DocumentProcessor.sortContents`
  (default `off`, opzionale xycut).
- **papero `compare.js`**: vista posizionata pagina↔output, **mancanti
  cerchiati**, blocchi flaggati; pannello fidelity con salto pagina.

**Cosa portiamo**: metrica **senza separatori** + folding; **riferimento
indipendente d'ordine** (i quattro algoritmi di §A); self-report "quali pagine
rivedere"; struct tree del tagged PDF quando c'è.

---

## I. Metodologia e architettura

- **ODL** — pipeline di **processor nominati** (`DocumentProcessor`): 3 loop
  paralleli per pagina (filtri; tabelle-bordi + text lines; paragrafi + liste +
  heading) intervallati da passi **sequenziali cross-page** (hidden, cluster
  tables, header/footer, neighbor, livelli) → caption dopo assegnazione ID.
  Modello per organizzare i nostri Pack/fix.
- **PdfPig** — **interfacce intercambiabili**: `IPageSegmenter` (XY-cut, Docstrum,
  default), `IWordExtractor` (nearest neighbour), `IReadingOrderDetector`
  (unsupervised). Opzioni tipizzate. Separazione segmentazione ↔ ordinamento
  (`TextBlock.SetReadingOrder`).
- **papero** — due motori (PDFium + Tika) con timeout/fallback; **motore JS
  gemello** con test di parità Python↔JS; fidelity report.
- **pdfminer** — separazione **`analyze` (ordine) ↔ `receive_layout`
  (serializzazione)**; `PDFPageAggregator` come pattern "result object".

**Cosa portiamo**: due/tre implementazioni a confronto (parità) come già
facciamo con page model; pipeline a fasi nominati; separazione
estrazione/ordinamento/serializzazione.

---

## J. Tabella delle idee, ordinate per priorità

| # | idea | motori | dove | classe nostra | sforzo |
|---|---|---|---|---|---|
| 1 | Vocabolario glifo-level pulito | papero, PdfPig, pdfplumber, pdfminer | C.1–C.4 | glue/split (dominante) | medio |
| 2 | Dedup char/word (overlap, (testo,font)) | PdfPig, ODL, pdfplumber | C.2/C.5/C.3 | duplicati | basso |
| 3 | Metrica senza separatori + folding | papero | H (fidelity) | falsi positivi | basso |
| 4 | Tabella come immagine **parallela** | papero | B.3 | tabella complessa | medio |
| 5 | `text` strategy (words→edges) | pdfplumber | B.1 | tabella senza bordi | medio |
| 6 | Trigger "tabella difficile" + quality-improves | ODL + pdfplumber | B.2/B.1 | tabella complessa | medio |
| 7 | Allen-graph come ordine indipendente | PdfPig | A.2 | colonne | medio |
| 8 | XY-Cut++ (cross-layout + narrow-outlier) | ODL | A.1 | colonne/box | medio |
| 9 | boxes_flow (un knob riga/colonna) | pdfminer | A.3 | ordine | basso |
| 10 | Header/footer: masking numeri + ripetizione | PdfPig, ODL, papero | F | furniture | basso |
| 11 | Heading probability + rarità font | ODL | E.1 | heading | basso/medio |
| 12 | Liste: unione neighbor cross-page | ODL | E.1 | liste | medio |
| 13 | Accenti disegnati + math tipografica | papero | C.6 | glifi/math | basso |
| 14 | Didascalie per probabilità + offset font | ODL, papero | D | caption | basso |
| 15 | Grafici vettoriali + etichette | papero | D.1 | figure mancanti | medio |
| 16 | Struct tree tagged (ParentTree) | pdfplumber | H | struttura | alto |
| 17 | Vista di confronto + fidelity panel | papero | H | UX/metro | medio |
| 18 | Soglie adattive (Docstrum, font-relative) | PdfPig, pdfminer | C.2/C.4 | soglie | medio |

---

## K. Caveats, ambito, licenze

- **Licenze**: papero MIT, OpenDataLoader Apache-2.0, PdfPig Apache-2.0,
  pdfplumber MIT, pdfminer MIT → codice riusabile **con attribuzione**; va
  **re-implementato** su `pymupdf`, non incollato.
- **Nessuna nuova dipendenza** senza valutazione: i motori sono **riferimento
  di lettura** (KB in `other-engines/`), non runtime.
- **Determinismo (R12)** e **niente tuning sulla pagina bersaglio**: le soglie
  vanno ritarate su corpus held-out.
- **Limiti dichiarati**: ODL "senza priorità semantiche"; PdfPig greedy non
  topologico; Docstrum "non esatto"; pdfplumber `text` dipende dagli
  allineamenti; `boxes_flow` globale per pagina.

---

## L. Appendice — mappa file → funzione

**papero** (`src/papero_extract/`): vedi `STUDIO-PAPERO-PDF-EXTRACTOR-2026-10-10.md` §9.2.

**OpenDataLoader** (`java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/processors/`):
`readingorder/XYCutPlusPlusSorter.java` (`sort` 94, cross-layout 146,
density 260, narrow-outlier 406, merge 590); `DocumentProcessor.java` (pipeline
179-435, sortContents 997); `HeaderFooterProcessor.java` (51, 156, 211, 274);
`HeadingProcessor.java` (53, 72, 192); `CaptionProcessor.java` (49, 148);
`ParagraphProcessor.java` (36, 132, 506); `ListProcessor.java` (105, 191, 498);
`TableBorderProcessor.java` (54, 124, 201); `ClusterTableProcessor.java` (53);
`SpecialTableProcessor.java` (39, 66); `TableStructureNormalizer.java` (52, 157,
227, 258); `AbstractTableProcessor.java` (44, 98, 119); `TextLineProcessor.java`
(43, 99, 127); `TextProcessor.java` (81, 106, 123, 146); `LevelProcessor.java`
(45, 117); `TableOfContentsProcessor.java`; `ContentFilterProcessor.java` (53);
`HiddenTextProcessor.java` (44); `TextDecorationProcessor.java` (59, 166, 215).

**PdfPig** (`src/UglyToad.PdfPig.DocumentLayoutAnalysis/`):
`PageSegmenter/RecursiveXYCut.cs` (VerticalCut 103, HorizontalCut 214, options 341);
`PageSegmenter/DocstrumBoundingBoxes.cs` (GetBlocks 92, GetSpacingEstimation 148,
GetPeakAverageDistance 238, GetLines 334, GetStructuralBlocks 397, options 688);
`PageSegmenter/XYLeaf.cs` (GetLines 42); `PageSegmenter/XYNode.cs` (GetLeaves 74);
`TextBlock.cs` (GetBoundingBoxOther 260, SetReadingOrder 311); `TextLine.cs`
(box Other 253); `Clustering.cs` (NearestNeighbourGroups 53, GroupByLinks 384);
`WhitespaceCoverExtractor.cs` (GetMaximalRectangles 95, options 24/47);
`TextEdgesExtractor.cs` (GetEdges 35); `DuplicateOverlappingTextProcessor.cs`
(30, 84); `DecorationTextBlockClassifier.cs` (123, options 42);
`ReadingOrderDetector/UnsupervisedReadingOrderDetector.cs` (Get 120, BuildGraph 145);
`ReadingOrderDetector/IntervalRelationHelper.cs` (GetRelationX 27, GetRelationY 112);
`ReadingOrderDetector/ReadingOrderHelper.cs` (18, 93);
`WordExtractor/NearestNeighbourWordExtractor.cs` (44, options 176); `Distances.cs`.

**pdfplumber** (`pdfplumber/`): `table.py` (snap_edges 21, join_edge_group 39,
words_to_edges_h 101, words_to_edges_v 144, intersections_to_cells 234,
cells_to_tables 297, TableFinder 577, TableSettings 485); `page.py`
(find_tables 458, dedupe_chars 573, structure_tree 248); `utils/text.py`
(textmap 241, WordExtractor 423, char_begins_new_word 516, collate_line 771,
dedupe_chars 794); `utils/clustering.py` (cluster_list 9, cluster_objects 42);
`utils/geometry.py` (rect_to_edges 208, filter_edges 264, snap_objects 151);
`structure.py` (PDFStructTree 151, all_mcids 118, element_bbox 466).

**pdfminer.six** (`pdfminer/`): `layout.py` (IndexAssigner 35, LAParams 48,
group_objects 703, group_textlines 780, group_textboxes 814, LTTextGroupLRTB 673,
LTTextLineHorizontal.add 505, find_neighbors 513, analyze 907);
`converter.py` (PDFLayoutAnalyzer 61, paint_path 108, TextConverter 326,
HTMLConverter 383, XMLConverter 684).

---

*Fine analisi. Documento di sola ricognizione: nessuna modifica al motore.*
