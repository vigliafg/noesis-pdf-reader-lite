# Studio — Motori di estrazione geometrici esterni (idee per il nostro engine)

> **Scopo.** Consolidare i **dati interessanti e dettagliati** di quattro progetti
> open-source di estrazione/struttura **basata sulla geometria** (regole +
> coordinate, CPU-only), **correlati ai nostri interessi di miglioramento
> dell'engine** (`ir_layout` + `main._apply_ir_on_page`).
>
> **Natura.** Ricognizione/brainstorming. Nessuna proposta di merge: elenca
> algoritmi, soglie, scelte di design e li mappa sulle nostre classi di difetto.
>
> **Fonti e commit analizzati** (clone shallow, `main`):
> - **papero** `beatrizalmeidaf/papero-pdf-text-extractor` — commit
>   `1b076dad96be246f9bca58cf69c59c77651ea694`, v3.1.1, **MIT**. Analisi
>   completa in `STUDIO-PAPERO-PDF-EXTRACTOR-2026-10-10.md`.
> - **OpenDataLoader PDF** `opendataloader-project/opendataloader-pdf` — commit
>   `3dfb3b7`, **Apache-2.0** (Java + binding Py/Node).
> - **PdfPig** `UglyToad/PdfPig` — commit `91ddd23`, **Apache-2.0** (C#/.NET).
> - **pdfplumber** `jsvine/pdfplumber` — commit `4c64b92`, **MIT**.
> - **pdfminer.six** `pdfminer/pdfminer.six` — commit `a18de2a`, **MIT**.
>
> **Rimandi interni.** `STUDIO-E2E-VERDETTO-2026-10-11.md` (difetti aperti),
> `HANDOFF-2026-10-11.md` §7 (residuo word-level), `MIGLIORIE-MOTORE.md`
> (§3 esperimenti parcheggiati), `STUDIO-PAGE-MODEL-2026-10-11.md`,
> `PYMUPDF4LLM-KNOWLEDGE-BASE.md`.

---

## 0. Sintesi esecutiva

Quattro progetti, stessa filosofia del nostro motore (recuperare *struttura*
dalla geometria, niente ML a runtime), con idee complementari:

- **papero** → il **gemello** più vicino: ricostruzione **glifo-level**,
  **matematica tipografica**, **tabelle ruled/unruled**, **immagine come
  artefatto parallelo**, **fidelity report**.
- **OpenDataLoader (XY-Cut++)** → **ordine**: cross-layout masking, **densità**
  che sceglie l'asse, **narrow-outlier retry** (page/eq numbers che fanno da
  ponte tra colonne).
- **PdfPig** → **catalogo di algoritmi d'ordine**: XY-Cut a soglie
  font-relative, **Docstrum**, **Allen-relations graph** (max out-degree),
  **NearestNeighbourWordExtractor**.
- **pdfplumber + pdfminer** → **tabelle** (`text` strategy, snap/join,
  intersections) e **grouping** (`boxes_flow`, LAParams relativi al font,
  albero agglomerativo).

Il valore maggiore per noi: **quattro algoritmi d'ordine indipendenti** da usare
come *metro/riferimento* (§6, §7) e una **cassetta di soglie adattive**
(font-relative, da distribuzione) che sostituiscono le nostre costanti in pt.

---

## 1. Panoramica comparata

| progetto | stack | licenza | approccio | punto forte per noi |
|---|---|---|---|---|
| **papero** | `pypdfium2` + Tika | MIT | geometria pura + OCR/heading Tika | glifo-level, accenti, math tipografica, tabelle ruled/unruled, fid report |
| **OpenDataLoader** | Java (PDFBox) + bind Py/Node | Apache-2.0 | XY-Cut++ (4 fasi) + pipeline di processor | **ordine** adattivo, cross-layout, outlier retry |
| **PdfPig** | C#/.NET | Apache-2.0 | 3 algoritmi layout + Docstrum + word extractor | **3 secondi pareri d'ordine**, soglie font-relative, parole |
| **pdfplumber** | Python (su pdfminer) | MIT | modelli char/line/rect/curve + TableFinder | **tabelle senza bordi**, snap/join, parametri |
| **pdfminer.six** | Python puro | MIT | LTLayoutContainer + `boxes_flow` | **soglie relative**, albero agglomerativo, `boxes_flow` |

**Nessuna dipendenza ML** in tutti e quattro (papero, ODL, PdfPig, pdfplumber/
pdfminer): coerente con il nostro vincolo "niente nuove dipendenze".

---

## 2. papero — recap (dettaglio in `STUDIO-PAPERO-PDF-EXTRACTOR-2026-10-10.md`)

Idee già documentate, qui ricordate perché **correlano** con i tre candidati.
(`layout.py` = `src/papero_extract/layout.py`.)

- **Ricostruzione glifo-level** (`read_chars` 575, `build_spans` 847,
  `_segments` 209, `words_of` 1417): span/parole/segmenti dalla geometria
  (tracking, giustificata, super/subscript, rotazioni). → *vocabolario pulito*
  per il de-glue.
- **Accenti disegnati come glifi** (`_compose_accents` 921,
  `symbols.compose_accent` 183): `Computa¸ca˜o` → `Computação`.
- **Matematica tipografica** (`_typeset_math` 303): frazioni/radici **disegnate**
  → testo + LaTeX; **rimuove le righe-frazione** dalle regole (non diventano
  bordi di tabella).
- **Ordine XY-cut column-aware** (`reading_order` 1244, `_split_columns` 1194):
  non taglia le righe di una tabella; guarda due bande avanti; gutter a
  **profilo di copertura** (`columns.gutters` 18).
- **Tabelle ruled/unruled/booktabs** (`ruled_regions` 1314, `_logical_rows`
  1503, `_projection_columns` 1431, `unruled_table` 1576).
- **Figure raster + grafici vettoriali** (`detect_figures` 1741,
  `chart_regions` 1677), etichette assorbite.
- **Immagine come artefatto parallelo** (`_attach_images` 2491;
  `render.block_markdown` 197): la **tabella resta markdown**, il crop va nel
  JSON/ZIP.
- **Fidelity report** (`fidelity.py`): copertura **senza separatori** + `_fold`,
  check d'ordine (`_backwards`) e overlap (`_overlap`).
- **Vista di confronto** (`compare.js`): blocchi posizionati, testo **mancante**
  cerchiato, blocchi flaggati.

---

## 3. OpenDataLoader PDF — XY-Cut++ (`XYCutPlusPlusSorter.java`, 651 righe)

> **File letto**: `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/processors/readingorder/XYCutPlusPlusSorter.java`.
> I processor di tabella (`TableBorderProcessor`, `ClusterTableProcessor`,
> `SpecialTableProcessor`, `TableStructureNormalizer`, `CaptionProcessor`,
> `HeaderFooterProcessor`, …) sono **elencati ma non letti in dettaglio**.

### 3.1 Algoritmo in 4 fasi

`sort(objects)` (`82`) → `sort(objects, beta=DEFAULT_BETA=2.0, densityThreshold=0.9)`.

**Fase 1 — pre-masking cross-layout** (`identifyCrossLayoutElements` 146):
- servono **≥3** oggetti;
- `maxWidth` = larghezza massima; soglia `threshold = beta * maxWidth`;
- un oggetto è cross-layout se `width ≥ threshold` **e** `hasMinimumOverlaps(obj, objects, 2)`,
  cioè si sovrappone orizzontalmente (rapporto ≥ `OVERLAP_THRESHOLD=0.1` rispetto
  al più stretto) ad **almeno 2** altri oggetti.
- Gli elementi cross-layout sono **estratti** dal flusso principale e
  **reinseriti** alla fine per Y.

**Fase 2 — densità sceglie l'asse** (`computeDensityRatio` 260):
`density = sum(area oggetti) / area(regione)` (clamp 1.0); `preferHorizontalFirst = density > 0.9`.

**Fase 3 — segmentazione ricorsiva** (`recursiveSegment` 331):
- calcola il miglior taglio **orizzontale** e **verticale** a proiezione
  (`findBest*CutWithProjection`), con `MIN_GAP_THRESHOLD=5.0`;
- sceglie il taglio col **gap maggiore**; se uno solo è valido, usa quello;
- se nessuno è valido → `sortByYThenX`;
- guardia: se il taglio produce un solo gruppo → `sortByYThenX` (anti-ricorsione).

**Fase 4 — merge** (`mergeCrossLayoutElements` 590): fusione per `topY`
(PDF: Y crescente = su).

### 3.2 Il pezzo d'oro: narrow-outlier retry (`findBestVerticalCutWithProjection` 406)

Se il miglior gap **verticale** è `< MIN_GAP_THRESHOLD`, si **scartano** gli
oggetti con `width < 10%` della larghezza di regione (`NARROW_ELEMENT_WIDTH_RATIO`)
e si ripete il taglio. Commento nel codice: *"page numbers, footnote markers may
bridge an otherwise clear column gap"*.

### 3.3 Parametri utili (default)

| nome | valore | significato |
|---|---|---|
| `DEFAULT_BETA` | 2.0 | soglia cross-layout (`2.0` ≈ disattivato) |
| `DEFAULT_DENSITY_THRESHOLD` | 0.9 | switch asse orizzontale/verticale |
| `OVERLAP_THRESHOLD` | 0.1 | overlap orizzontale minimo |
| `MIN_OVERLAP_COUNT` | 2 | overlap minimi per cross-layout |
| `MIN_GAP_THRESHOLD` | 5.0 pt | gap minimo per un taglio |
| `NARROW_ELEMENT_WIDTH_RATIO` | 0.1 | filtro outlier stretti |

### 3.4 Idee per noi

1. **Cross-layout = larghezza + overlap ≥2** (non solo larghezza): criterio più
   selettivo per i nostri box/banda a tutta larghezza (evita falsi positivi).
2. **Densità decide l'asse** di taglio: idea non presente da noi.
3. **Narrow-outlier retry**: fix locale sulle colonne `fe22 p1038`/marker che
   fanno da ponte.
4. **Ordine come algoritmo di riferimento**: implementabile ~100 righe, buon
   **metro indipendente** (come page model / I1/I3).

### 3.5 Architettura (idea di design)

ODL struttura il lavoro come **pipeline di processor** (`processors/`):
`DocumentProcessor`, `HeaderFooterProcessor`, `HeadingProcessor`,
`CaptionProcessor`, `ListProcessor`, `ParagraphProcessor`, `TableBorderProcessor`,
`ClusterTableProcessor`, `SpecialTableProcessor`, `TableStructureNormalizer`,
`TableOfContentsProcessor`, `HiddenTextProcessor`, `TextDecorationProcessor`,
`AutoTaggingProcessor`, `TaggedDocumentProcessor`, `HybridDocumentProcessor`,
`LevelProcessor`, `ContentFilterProcessor`, `TextLineProcessor`. → Modello di
"catena di passate nominate" (noi abbiamo i Pack con i fix-flag; ODL ha un
processor per funzione).

### 3.6 Caveat

Dichiaratamente *"simplified geometric implementation without semantic type
priorities"*; non tratta le tabelle in modo speciale; Y nativa del PDF. Da noi:
**riferimento d'ordine**, non sostituto.

---

## 4. PdfPig — catalogo di algoritmi d'ordine (`UglyToad.PdfPig.DocumentLayoutAnalysis`)

> **File letti integralmente**: `PageSegmenter/RecursiveXYCut.cs` (401),
> `PageSegmenter/DocstrumBoundingBoxes.cs` (**solo testa**, 150/761),
> `ReadingOrderDetector/UnsupervisedReadingOrderDetector.cs` (263),
> `ReadingOrderDetector/IntervalRelationHelper.cs` (279),
> `WordExtractor/NearestNeighbourWordExtractor.cs` (239),
> `ReadingOrderDetector/DefaultReadingOrderDetector.cs` (25).
> **Elencati**: `WhitespaceCoverExtractor.cs`, `TextEdgesExtractor.cs`,
> `Clustering.cs`, `KdTree.cs`, `DecorationTextBlockClassifier.cs`,
> `DuplicateOverlappingTextProcessor.cs`, `TextBlock.cs`, `TextLine.cs`.

### 4.1 RecursiveXYCut (`RecursiveXYCut.cs`)

- Cut **alternati**: `VerticalCut` (108) → `HorizontalCut` (214) → …
- Soglia di gap **relativa al font**:
  - `DominantFontWidthFunc` = **moda** delle larghezze dei glifi (fallback media);
  - `DominantFontHeightFunc` = **moda** delle altezze **× 1.5**.
- `VerticalCut`: ordina le parole per `Left`; costruisce un **projection profile**;
  taglia se `gap > dominantFontWidth` **e** la proiezione corrente è ≥ `MinimumWidth` (=1).
- `HorizontalCut` ha un guard `level`: se produce una sola proiezione e
  `level >= 1`, si ferma.
- Le parole "perse" tra i tagli diventano **foglie singole** (`lost`).
- `MinimumWidth` default **1** pt; separatori `WordSeparator=" "`, `LineSeparator="\n"`.

### 4.2 Docstrum (`DocstrumBoundingBoxes.cs`)

Tre passi (docstring + `GetBlocks` 92):
1. **Stima delle spaziature** `withinLineDistance` e `betweenLineDistance` da
   **istogrammi delle distanze nearest-neighbour** tra parole, con **bounds
   angolari** (`AngleBounds`) e `BinSize` → distanza = picco medio della
   distribuzione.
2. **Determinazione delle righe** con `maxWithinLineDistance = wlMultiplier *
   withinLineDistance`.
3. **Blocchi strutturali** con `maxBetweenLineDistance = blMultiplier *
   betweenLineDistance` e `angularDifferenceBounds` (parallelismo tra righe).

→ Soglie **derivate dalla distribuzione della pagina**, niente costanti.

### 4.3 UnsupervisedReadingOrderDetector (`UnsupervisedReadingOrderDetector.cs`)

- Costruisce un **grafo diretto** completo: edge `i→j` se "i prima di j"
  (`BuildGraph` 145) secondo una regola spaziale (**Allen's interval relations**,
  x e y) e opzionalmente il **rendering order** (`useRenderingOrder`, default true).
- **Tre regole** (`SpatialReasoningRules` 18): `Basic` (L→R, T→B),
  `RowWise` (righe), `ColumnWise` (colonne, default).
- Tolleranza `T = 5` (due coordinate più vicine di T sono "uguali").
- **Estrazione** (`Get` 120): greedy a **max out-degree**:
  ```csharp
  var maxCount = graph.Max(kvp => kvp.Value.Count);
  var current = graph.First(kvp => kvp.Value.Count == maxCount);
  graph.Remove(current.Key);
  foreach (var g in graph) g.Value.Remove(index);
  ```
- `GetBeforeInReadingVertical` (208) / `GetBeforeInReadingHorizontal` (238)
  combinano le relazioni X/Y ammesse; il rendering order (`GetBeforeInRendering`,
  `avg TextSequence`) è un OR.

### 4.4 IntervalRelationHelper (`IntervalRelationHelper.cs`)

Implementa le **13 relazioni di Allen** (`IntervalRelations` enum 193):
`Precedes, Meets, Overlaps, Starts, During, Finishes` + inverse +
`Equals` (con `Unknown`), per X (`GetRelationX` 27) e Y (`GetRelationY` 112),
con tolleranza `T`. È una **libreria pronta** da riusare come riferimento.

### 4.5 NearestNeighbourWordExtractor (`NearestNeighbourWordExtractor.cs`)

- Raggruppa i glifi in parole via **clustering nearest-neighbour** sui punti di
  **baseline** (`StartBaseLine`/`EndBaseLine`, `157`).
- Distanza: **Manhattan** per orientamento axis-aligned, **Euclidea** per gli
  altri (`GetDistanceMeasure` 127, `Distances`).
- **Distanza massima** default = `20% di max(width, pointSize)`; **doppia** per
  orientamento `Other` (189).
- **5 bucket di orientamento** (`OrientationBucketsCount=5`: H, Rotate270,
  Rotate180, Rotate90, Other) elaborati in ordine deterministico.
- Filtri: pivot/filter scartano gli spazi; opzione `GroupByOrientation` (default true).

### 4.6 Idee per noi

1. **Allen-relations graph + max out-degree** → **secondo parere d'ordine**
   indipendente, ortogonale a XY-cut; utilissimo come invariante.
2. **Tolleranza `T`** per "coordinate uguali" (5 pt) — noi confrontiamo con
   tolleranze sparse.
3. **Soglie font-relative** (mode width, mode height×1.5) → sostituiscono pt fissi.
4. **Docstrum**: soglie da **istogrammi** di distanze.
5. **NearestNeighbourWordExtractor** → vocabolario **glifo-level** con dimensione
   derivata dal font e orientamento separato (per il residuo word-level).
6. `DuplicateOverlappingTextProcessor` → i nostri bug di duplicazione.

### 4.7 Caveat

Coordinate normalizzate; il greedy non è un topo-sort garantito; Docstrum
"non replica esattamente" l'originale.

---

## 5. pdfplumber + pdfminer.six — tabelle e grouping

> **File letti integralmente**: pdfplumber `table.py` (707), `page.py` (731).
> **Non letti in dettaglio**: pdfplumber `utils/text.py` (819),
> `utils/geometry.py` (279), `structure.py` (510); pdfminer `layout.py` (**letto
> integralmente**, 991), `converter.py` (parziale, path→LTLine/LTRect/LTCurve,
> `render_char`→LTChar).

### 5.1 pdfplumber — `TableFinder` (`table.py`)

**Strategia per asse** (`TABLE_STRATEGIES = ["lines","lines_strict","text","explicit"]`, 460):
- `lines`: tutte le linee (incluse le `rect`);
- `lines_strict`: solo oggetti di tipo `line`;
- **`text`**: linee **immaginarie** da parole allineate;
- `explicit`: linee fornite dall'utente.

**Pipeline** (`TableFinder.__init__` 588):
`get_edges()` → `edges_to_intersections()` → `intersections_to_cells()` →
`cells_to_tables()` → `Table`.

**Il pezzo d'oro — `words_to_edges_h` (101) e `words_to_edges_v` (144)**
(strategia `text`, tabelle **senza bordi**):
- orizzontali: cluster di `top` di **≥ `min_words_horizontal`** parole
  (default **1**) → righe; si aggiunge anche la linea inferiore di ogni riga
  (per l'ultima riga);
- verticali: cluster di `x0`, `x1` **e centro** di **≥ `min_words_vertical`**
  parole (default **3**) → colonne; si aggiunge il bordo destro.
- I bbox sono **condensati** rimuovendo le sovrapposizioni.

**Robustezza griglia**:
- `snap_edges` (21): **snappa** le linee entro `snap_tolerance` (default 3) alla
  loro **media posizionale`;
- `join_edge_group` (39): **giunge** le linee collineari entro `join_tolerance`
  (default 3);
- `intersections_to_cells` (234): cella solo se le linee **si connettono davvero**
  ai 4 angoli (`edge_connects`, 244) → **niente celle fantasma**;
- `cells_to_tables` (297): raggruppa per **angoli condivisi**; scarta le tabelle
  con **≤1 cella** (`filtered`, 354).

**`TableSettings`** (485): `snap/join/intersection_*_tolerance`, `edge_min_length`,
`edge_min_length_prefilter`, `min_words_vertical`, `min_words_horizontal`,
`vertical_strategy`, `horizontal_strategy`, `explicit_*_lines`, `text_settings`.
→ un **modello di parametrizzazione geometrica** delle tabelle.

**Altro** (`page.py`):
- `dedupe_chars` (573): rimuove char duplicati (testo+posizione entro tolleranza);
- `doctop` (409-419): **Y cumulativa tra pagine** (utile per l'ordine multi-pagina);
- `structure_tree` (248): struct tree del **tagged PDF** (autorevole quando c'è).

### 5.2 pdfminer.six — `LAParams` e grouping (`layout.py`)

**`LAParams`** (48) — **tutte le soglie relative alle metriche del font**:
- `line_overlap=0.5` — overlap per "stessa riga" (relativo all'altezza min);
- `char_margin=2.0` — distanza perché due char siano "stessa riga" (relativo
  alla larghezza del carattere);
- `word_margin=0.1` — gap oltre cui separare le parole (relativo alla larghezza);
- `line_margin=0.5` — gap perché due righe siano "stesso paragrafo" (relativo
  all'altezza riga);
- **`boxes_flow=0.5`** — ∈ [−1,1] o `None` (vedi sotto);
- `detect_vertical=False`, `all_texts=False`.

**Grouping a cascata**:
- char → righe: `group_objects` (703) con `halign`/`valign` (overlap + margini
  **relativi**); `LTTextLineHorizontal.add` (505) inserisce `LTAnno(" ")` se il
  gap > `word_margin * max(w,h)`;
- righe → box: `group_textlines` (780) via `find_neighbors` (513): vicini con
  **stessa altezza ±d** e left/right/**center**-aligned, `d = line_margin *
  height`;
- box → **albero**: `group_textboxes` (814) con **clustering agglomerativo**
  (heap, closest-first) e distanza = **area dell'unione − aree** (839), con
  guardia *"nessun oggetto in mezzo"* (`isany`, 861).

**`boxes_flow`** — il gioiello (`LTTextGroupLRTB.analyze` 674):
```python
key = (1 - boxes_flow) * obj.x0 - (1 + boxes_flow) * (obj.y0 + obj.y1)
```
`boxes_flow = +1` → ordine per **righe** (solo Y); `boxes_flow = −1` → per
**colonne** (solo X); `None` → sort semplice per `(-y0, x0)` (o `-x1` per
verticale, 924).

### 5.3 Idee per noi

1. **`text` strategy** (`words_to_edges_h/v`, asse **centro** incluso) →
   formalizza le tabelle **senza bordi** (nostra classe "tabella complessa").
2. **`snap` + `join`** → griglia robusta a linee imperfette.
3. **`intersections_to_cells`** → anti-celle-fantasma.
4. **`min_words_vertical/horizontal`** → soglie minime per righe/colonne.
5. **`dedupe_chars`** → duplicati.
6. **LAParams relativi + `boxes_flow`** → un **unico knob** riga/colonna e
   soglie font-relative al posto delle nostre costanti in pt.
7. **Albero agglomerativo** con guardia `isany` → gerarchia d'ordine naturale.
8. **`doctop`** → continuità d'ordine tra pagine.
9. **`structure_tree`** → struttura autorevole dal tagged PDF (come papero/ODL).

### 5.4 Caveat

pdfplumber `text` strategy è euristica sull'allineamento (risente di indenti/
centrature); pdfminer `boxes_flow` è **globale** (un solo valore per pagina) e
non gestisce tabelle.

---

## 6. Cross-map: nostre classi di difetto → idea → progetto

Riferimento classi: `STUDIO-E2E-VERDETTO-2026-10-11.md`, `HANDOFF-2026-10-11.md` §7.

| nostra classe (dove) | idea | progetto |
|---|---|---|
| **Residuo word-level**: glue/split/bold-init (**dominante**) | NearestNeighbourWordExtractor (baseline, 20% font, orientamento) | PdfPig |
| idem | ricostruzione glifo-level + `words_of` | papero |
| idem | `dedupe_chars`; `DuplicateOverlappingTextProcessor` | pdfplumber / PdfPig |
| **Colonne interlacciate** (`fe22 p1038`) | narrow-outlier retry; cross-layout masking | OpenDataLoader |
| idem | Allen-relations graph (secondo parere) | PdfPig |
| idem | `boxes_flow` (knob riga/colonna) | pdfminer |
| idem | XY-cut column-aware con gutter a copertura | papero |
| **Box/banda a tutta larghezza** | cross-layout = larghezza **+ overlap ≥2** | OpenDataLoader |
| **Tabella complessa / ordine** (`ce24 p480`, grave) | `text` strategy (`words_to_edges_h/v`) | pdfplumber |
| idem | ruled/unruled + `_logical_rows` | papero |
| idem | snap/join + `intersections_to_cells` | pdfplumber |
| **Figura vuota** (`su19 p1050`) | `_visible` (render) + detect raster/vettoriale | papero |
| **Prosa formula** (`arxiv p30`) | matematica tipografica (`_typeset_math`) | papero |
| **Furniture** (header/footer/logo) | frequenza+posizione+denominatore (0.3) | papero |
| **Soglie "magiche" in pt** | font-relative (mode width; height×1.5) | PdfPig |
| idem | LAParams relativi | pdfminer |
| idem | Docstrum (istogrammi di distanze) | PdfPig |
| **Metro d'ordine indipendente** | XY-Cut++; Allen-graph; albero `boxes_flow` | ODL / PdfPig / pdfminer |
| **Gerarchia/struttura** | albero agglomerativo + `isany`; struct tree | pdfminer / pdfplumber |
| **Vista "cosa manca e dove"** | compare view + fidelity report | papero |

---

## 7. Le idee più riusabili (dettaglio operativo)

1. **Quattro algoritmi d'ordine indipendenti** — il nostro "riferimento
   indipendente" (filosofia già in uso con page model e I1/I3) può attingere a:
   - **XY-Cut++** (ODL): parametrico, adattivo, ~100 righe;
   - **Allen-graph** (PdfPig): grafo spaziale + greedy max out-degree;
   - **agglomerative tree + `boxes_flow`** (pdfminer): gerarchia d'ordine;
   - **Docstrum** (PdfPig): segmentazione bottom-up da spaziatura stimata.
   → costruire 1–2 **invarianti** o un **ordine di riferimento** da confrontare
   con l'output del motore (per classe colonne/tabelle).
2. **Soglie adattive** al posto delle costanti:
   - font-relative (PdfPig mode width/height);
   - da distribuzione (Docstrum istogrammi);
   - relative al carattere (LAParams `char_margin`/`word_margin`/`line_margin`).
   → direttamente applicabili al nostro vocabolario glifo-level e al de-glue.
3. **Tabelle senza bordi** (pdfplumber `text`): righe = cluster di `top` (≥1
   parola), colonne = cluster di `x0/x1/**centro**` (≥3 parole). → regola
   deterministica per `ce24 p480`.
4. **Griglia robusta** (pdfplumber): `snap` (media entro tol.) + `join`
   (collineari entro tol.) + celle solo con **4 angoli connessi**.
5. **`boxes_flow`**: un solo numero per passare da colonne a righe
   (`key = (1-bf)x0 - (1+bf)(y0+y1)`), utile per un reorder leggibile.
6. **Vocabolario pulito** (PdfPig nn word extractor + papero glifo-level):
   baseline, distanza 20% font, orientamenti separati → sblocca il de-glue
   parcheggiato (`MIGLIORIE-MOTORE.md` §3, `HANDOFF §7`).
7. **Immagine come artefatto parallelo** (papero): markdown resta la griglia,
   crop nel JSON/ZIP per le tabelle difficili (cfr. brainstorm figure/tabelle).
8. **Vista di confronto** (papero `compare.js`): blocchi posizionati, mancanti
   cerchiati, flag evidenziati → UI.
9. **Pipeline di processor** (ODL): catena di passate nominate (noi: Pack +
   fix-flag); modello per organizzare i fix.

---

## 8. Priorità suggerita (allineata all'audit)

1. **Vocabolario glifo-level pulito** (PdfPig nn word extractor + papero
   glifo-level + Docstrum) → residuo **numericamente dominante**.
2. **`words_to_edges` (pdfplumber `text`)** → tabelle senza bordi / complesse.
3. **Metro d'ordine indipendente**: **Allen-graph** (PdfPig) e/o **XY-Cut++**
   (ODL); su `fe22 p1038` / `ce24 p480`.
4. **Narrow-outlier retry** (ODL) → marker che fanno da ponte tra colonne.
5. **Soglie adattive** (PdfPig font-relative, Docstrum, LAParams) → ridurre le
   costanti in pt.
6. **`snap`/`join`/`intersections_to_cells`** (pdfplumber) + **`boxes_flow`**
   (pdfminer) → robustezza e leggibilità.

---

## 9. Caveats e licenze

- **Licenze**: papero **MIT**, OpenDataLoader **Apache-2.0**, PdfPig
  **Apache-2.0**, pdfplumber **MIT**, pdfminer.six **MIT** → tutte permissive;
  il **codice** è riusabile, ma va **re-implementato** sul nostro stack
  (`pymupdf`), non incollato.
- **Stack diversi**: ODL Java (PDFBox), PdfPig C#/.NET, pdfplumber/pdfminer
  Python (pdfminer). Vincolo nostro: **nessuna nuova dipendenza** senza
  valutazione → al massimo uso **dev-only** come riferimento.
- **Limiti dichiarati**: ODL "simplified… without semantic type priorities";
  PdfPig greedy non garantito topologico; Docstrum "non esatto"; pdfplumber
  `text` dipende dagli allineamenti; pdfminer `boxes_flow` è globale.
- **Determinismo (R12)** e **niente tuning sulla pagina bersaglio**: le soglie
  vanno ritarate su corpus held-out.

---

## 10. Appendice — mappa file → funzione

### OpenDataLoader
`processors/readingorder/XYCutPlusPlusSorter.java`: `sort` 94, cross-layout 146,
`hasMinimumOverlaps` 196, `computeDensityRatio` 260, `recursiveSegment` 331,
`findBestVerticalCutWithProjection` 406, `findVerticalCutByEdges` 450,
`findBestHorizontalCutWithProjection` 484, `mergeCrossLayoutElements` 590.
Pipeline: `processors/*Processor.java` (elenco in §3.5).

### PdfPig (`src/UglyToad.PdfPig.DocumentLayoutAnalysis/`)
`PageSegmenter/RecursiveXYCut.cs`: `VerticalCut` 103, `HorizontalCut` 214.
`PageSegmenter/DocstrumBoundingBoxes.cs`: `GetBlocks` 92, `GetSpacingEstimation` 148.
`ReadingOrderDetector/UnsupervisedReadingOrderDetector.cs`: `Get` 120,
`BuildGraph` 145, `GetBeforeInReadingVertical` 208.
`ReadingOrderDetector/IntervalRelationHelper.cs`: `GetRelationX` 27, `GetRelationY` 112.
`WordExtractor/NearestNeighbourWordExtractor.cs`: `GetWords` 44, distanza 189.

### pdfplumber + pdfminer
pdfplumber `table.py`: `snap_edges` 21, `join_edge_group` 39, `merge_edges` 68,
`words_to_edges_h` 101, `words_to_edges_v` 144, `edges_to_intersections` 207,
`intersections_to_cells` 234, `cells_to_tables` 297, `TableFinder` 577,
`TableSettings` 485.
pdfplumber `page.py`: `dedupe_chars` 573, `find_tables` 458, `structure_tree` 248.
pdfminer `layout.py`: `LAParams` 48, `group_objects` 703, `group_textlines` 780,
`group_textboxes` 814, `LTTextGroupLRTB.analyze` 674, `find_neighbors` 513.

---

*Fine analisi. Documento di sola ricognizione: nessuna modifica al motore.*
