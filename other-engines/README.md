# other-engines — KB dei motori di estrazione esterni

Questa cartella raccoglie i **cloni dei motori di estrazione geometrici
esterni** che abbiamo studiato, da tenere "sempre sottomano" per leggere il
codice.

> **Importante.** I **cloni non sono versionati** (vedi `.gitignore`): solo
> questo indice è committato. Così non gonfiamo la history di git (i repo
> completi pesano centinaia di MB — PdfPig da solo ~675 MB), ma la KB resta
> disponibile in locale e **riproducibile** a commit fissi.

## Come popolarla / aggiornarla

```bash
tools/fetch-other-engines.sh            # scarica/verifica i commit PINNATI (analizzati da noi)
tools/fetch-other-engines.sh --latest   # aggiorna ogni repo a origin di default
tools/fetch-other-engines.sh --refresh  # ri-scarica da zero (commit pinnati)
tools/fetch-other-engines.sh --lean     # KB leggera (consigliata)
```

I cloni finiscono in `other-engines/<nome>/`. La prima esecuzione scarica
shallow il **commit esatto** analizzato nei nostri documenti (così il codice che
leggi è quello di cui parlano i `STUDIO-*`).

**`--lean`** (consigliato): il grosso dei repo sono **fixture di test** (PDF/
immagini), inutili per leggere il codice.
- **PdfPig** usa un clone **blobless + sparse**: materializza **tutti i
  progetti di codice** (`UglyToad.PdfPig`, `.Core`, `.Fonts`, `.Tokens`,
  `.Tokenization`, `.DocumentLayoutAnalysis`) tranne `.Tests` (i 342 MB di
  PDF/immagini) → **~676 MB → ~14 MB**;
- gli altri repo vedono rimosse le fixture binarie sotto `*test*`/`*benchmark*`/`*specific*`/`samples`.

Pesi tipici con `--lean`: PdfPig ~14 MB, pdfplumber ~16 MB, papero ~18 MB,
pdfminer ~30 MB, OpenDataLoader ~34 MB → **totale ~110 MB** (senza `--lean`:
~826 MB). Reversibile con `--refresh`.

## Portabilità su più macchine

La KB **non si copia**: si **riproduce** dalla ricetta (questo script + i commit
pinnati). Così ogni macchina ha esattamente lo stesso codice.

**Con rete (default).** Su una macchina nuova:

```bash
git clone <nostro-repo>
cd <nostro-repo>
tools/fetch-other-engines.sh --lean
```

**Senza rete (USB/cloud).** Crea uno snapshot su una macchina con i cloni e lo
ripristini dove serve:

```bash
# macchina A (con rete)
tools/fetch-other-engines.sh --archive            # -> other-engines-kb.tar.gz (~21 MB)
# copia il file (USB/cloud) sulla macchina B, poi:
tools/fetch-other-engines.sh --restore other-engines-kb.tar.gz
```

Lo snapshot **non contiene `.git`** (è solo per lettura): è ~21 MB. Per renderlo
di nuovo aggiornabile, sull'altra macchina `rm -rf other-engines/<nome>` e
rilancia con `--lean` (o `--latest`).

> In breve: la ricetta vive nel nostro repo (script + README + `STUDIO-*`); su
> ogni macchina basta il nostro repo (o lo snapshot) per ricostruire la KB.

## I motori

| nome | repo | commit pinnato | licenza |
|---|---|---|---|
| `papero` | `beatrizalmeidaf/papero-pdf-text-extractor` | `1b076dad` | MIT |
| `opendataloader` | `opendataloader-project/opendataloader-pdf` | `3dfb3b7c` | Apache-2.0 |
| `pdfpig` | `UglyToad/PdfPig` | `91ddd23f` | Apache-2.0 |
| `pdfplumber` | `jsvine/pdfplumber` | `4c64b92d` | MIT |
| `pdfminer` | `pdfminer/pdfminer.six` | `a18de2a9` | MIT |

> Sono **cinque repo** per **quattro progetti/candidati**: `pdfplumber` sta su
> `pdfminer.six`, quindi li teniamo entrambi.

## Cosa guardare (file chiave, con l'idea per noi)

### `papero` (pypdfium2 + Tika)
- `src/papero_extract/layout.py` — cuore geometrico:
  - `read_chars` (575), `build_spans` (847), `_segments` (209) → ricostruzione **glifo-level**;
  - `_compose_accents` (921) → accenti disegnati (`Computa¸ca˜o` → `Computação`);
  - `_typeset_math` (303) → frazioni/radici **disegnate** → testo + LaTeX;
  - `ruled_regions` (1314), `_logical_rows` (1503), `unruled_table` (1576) → **tabelle** ruled/unruled/booktabs;
  - `reading_order` (1244), `_split_columns` (1194) → ordine XY-cut column-aware;
  - `detect_figures` (1741), `chart_regions` (1677) → figure raster **+ grafici vettoriali**;
  - `_attach_images` (2491) → crop di figura/tabella/formula come **artefatto parallelo**.
- `src/papero_extract/fidelity.py` → **fidelity report** (copertura senza separatori).
- `web/assets/compare.js` → **vista di confronto** (mancanti cerchiati, flag evidenziati).

### `opendataloader` — XY-Cut++ (Java)
- `java/opendataloader-pdf-core/src/main/java/org/opendataloader/pdf/processors/readingorder/XYCutPlusPlusSorter.java`
  → ordine adattivo: cross-layout masking (146), densità (260), **narrow-outlier retry** (406), merge (590).
- `.../processors/*Processor.java` → pipeline di passate nominate (modello per organizzare i fix).

### `pdfpig` — catalogo di algoritmi d'ordine (C#)
- `src/UglyToad.PdfPig.DocumentLayoutAnalysis/PageSegmenter/RecursiveXYCut.cs` → XY-cut a soglie **font-relative**.
- `.../PageSegmenter/DocstrumBoundingBoxes.cs` → spaziature da **istogrammi**.
- `.../ReadingOrderDetector/UnsupervisedReadingOrderDetector.cs` → grafo **Allen-relations** + greedy max out-degree.
- `.../ReadingOrderDetector/IntervalRelationHelper.cs` → le 13 relazioni di Allen.
- `.../WordExtractor/NearestNeighbourWordExtractor.cs` → parole glifo-level (baseline, 20% font, orientamenti).

### `pdfplumber` (Python, su pdfminer)
- `pdfplumber/table.py` → `TableFinder`: strategia `text` (`words_to_edges_h` 101, `words_to_edges_v` 144),
  `snap_edges`/`join_edge_group`, `intersections_to_cells` (234), `cells_to_tables` (297), `TableSettings` (485).
- `pdfplumber/page.py` → modello oggetti, `dedupe_chars` (573), `doctop`, `structure_tree` (tagged PDF).

### `pdfminer` (Python puro)
- `pdfminer/layout.py` → `LAParams` (48, soglie **relative al font**), `group_objects` (703),
  `group_textlines` (780), `group_textboxes` (814, albero agglomerativo), **`boxes_flow`** (674).

## Dettaglio e mappatura sui nostri difetti

- `docs/STUDIO-MOTORI-GEOMETRICI-ESTERNI-2026-10-10.md` — consolidato dei
  quattro progetti, con **cross-map** sulle nostre classi di difetto e priorità.
- `docs/STUDIO-PAPERO-PDF-EXTRACTOR-2026-10-10.md` — analisi completa di papero.

## Nota sulle licenze

MIT e Apache-2.0: codice riutilizzabile **con attribuzione**. I cloni qui servono
solo come **riferimento di lettura**; ogni idea va **re-implementata** sul nostro
stack (`pymupdf`), non incollata (vincolo: nessuna nuova dipendenza senza
valutazione).
