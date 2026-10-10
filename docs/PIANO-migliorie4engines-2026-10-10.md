# PIANO — `migliorie4engines`

> **Nome.** `migliorie4engines` — migliorie al nostro engine ispirate dai quattro
> motori geometrici esterni (papero, OpenDataLoader, PdfPig, pdfplumber/pdfminer).
>
> **Stato.** Piano (backlog ordinato + roadmap + criteri di accettazione).
> Non implementato.
>
> **Riferimenti.** `STUDIO-IDEE-MOTORI-ESTERNI-2026-10-10.md` (**master** delle
> idee, con soglie e `file:riga`), `STUDIO-MOTORI-GEOMETRICI-ESTERNI-2026-10-10.md`,
> `STUDIO-PAPERO-PDF-EXTRACTOR-2026-10-10.md`.
> KB locale dei motori: `other-engines/` (`other-engines/README.md`,
> `tools/fetch-other-engines.sh`).
> Contesto: `STUDIO-E2E-VERDETTO-2026-10-11.md`, `HANDOFF-2026-10-11.md` §7,
> `MIGLIORIE-MOTORE.md`, `REGOLE-TEST.md`, `STUDIO-PAGE-MODEL-2026-10-11.md`.

---

## 0. Scopo in una riga

Aggiungere al nostro engine (`ir_layout` + `main._apply_ir_on_page` +
`layout_engine`) le idee **additive** dei quattro motori e costruire **riferimenti
indipendenti** d'ordine/tabelle, **senza** sostituire `pymupdf4llm` (GNN), con
criterio di successo ancorato all'**esito** (difetti reali su held-out).

---

## 1. Principi e vincoli (non negoziabili)

1. **Motore singolo** `PyMuPDF4LLM` + GNN: **nessuna dipendenza nuova** senza
   valutazione. Le idee si **re-implementano** su `pymupdf`
   (`get_text("rawdict")`, `get_drawings`, `get_image_info`), non si incollano.
2. **Determinismo (R12)**: nessun fix non deterministico a runtime.
3. **Due strati separati**: scheletro geometrico ≠ etichette (anti-cecità
   correlata), come già per page model/oracolo.
4. **Validazione su classi e pagine held-out**; **mai** tuning sulla pagina
   bersaglio.
5. **Additivo prima di sostitutivo**: si preferisce un fix/invariante che non
   cambia il produttore; le sostituzioni solo con **trigger + guardia "migliora"**.
6. **Ruoli separati**: **gate** (precisione, non far cadere pagine buone) vs
   **fix** (recall, candidati). Ogni voce dichiara il suo ruolo.

---

## 2. Criteri e metrica di successo

**Classificazione delle idee** (dal master, §1):

| tipo | definizione | rischio |
|---|---|---|
| **A — additivo** | fix/post-filtro/invariante che non cambia il produttore | basso |
| **R — riferimento** | algoritmo indipendente usato come *metro* (non produttore) | basso |
| **S — sostitutivo selettivo** | rimpiazza un passaggio **solo** con trigger + guardia | medio |
| **N — no** | sostituzione totale del produttore | alto |

**North-star**: **#difetti reali su 2×N pagine held-out**, per strato/classe.
Se aggiungiamo una voce e la north-star non scende → ci si ferma.
Per il **gate** conta la **precisione**; per i **fix** conta la **recall**.

---

## 3. Backlog ordinato

Convenzione voci: `M4E-x.y`. Ogni voce: *Obiettivo · Sorgente · Dove · Approccio ·
Test · Accettazione · Sforzo · Ruolo · Rischi*.

### Fase 1 — Quick wins additivi (basso costo, alto valore)

#### M4E-1.1 — Metrica "senza separatori" + folding
- **Obiettivo**: ridurre i **falsi positivi** del detector `text_integrity`
  (che sovra-segnala: bold-initial, composti sillabati) → tasso grezzo più
  affidabile nel registry/gate.
- **Sorgente**: papero `fidelity._coverage`/`_fold` (master §H).
- **Dove**: `tools/detectors.py` (`extra_tokens`/`analyze`), `tools/e2e.py`
  (`_checks`/`diag`); eventuale colonna nel `defects.jsonl`.
- **Approccio**: confronto token **senza separatori** + `NFKD`/rimozione
  combining (come `_fold`); i numeri <3 cifre non contano.
- **Test**: `tests/test_detectors.py` (unit: `**E**sophageal`, sillabazione,
  `10⁹`).
- **Accettazione**: FP del detector su campione arbitrato ↓; nessun difetto
  reale mancato.
- **Sforzo**: basso · **Ruolo**: metro · **Rischi**: nessuno (non tocca l'engine).

#### M4E-1.2 — Dedup char/word sovrapposti
- **Obiettivo**: rimuovere testo duplicato (prosa+immagine, "faux bold",
  doppio layer).
- **Sorgente**: PdfPig `DuplicateOverlappingTextProcessor` (chiave `(testo,font)`,
  tol `width/len/3`), ODL `removeSameTextChunks` (intersezione > 0.5),
  pdfplumber `dedupe_chars`.
- **Dove**: `ir_layout.build_markdown` (fase di pulizia elementi) o
  `main._cosmetic_ir`; nuovo fix-flag.
- **Approccio**: dedup a livello glifo/span, con la parte mantenuta marcata
  **bold** (come PdfPig) → recupera grassetto.
- **Test**: `tests/test_ir_text_fix.py` (o nuovo `test_dedupe`).
- **Accettazione**: `duplicate_lines` (diag) ↓; nessuna perdita di contenuto.
- **Sforzo**: basso · **Ruolo**: fix · **Rischi**: falsi tagli → guardia "solo
  se stessa bbox±tol".

#### M4E-1.3 — Furniture: masking numeri + ripetizione cross-page + posizione
- **Obiettivo**: header/footer/logo più robusti (header alternati pari/dispari,
  footer con numerazione).
- **Sorgente**: PdfPig `DecorationTextBlockClassifier` (masking numeri `"@"`,
  similarità contenuto×geometria 0.25, n=5), ODL `HeaderFooterProcessor`
  (posizione 2/3–1/3, gap ≤30, ripetizione a distanza 1 e 2), papero
  `_mark_furniture` (frequenza+denominatore 0.3).
- **Dove**: `layout_engine` Pack 1 (header/footer), `ir_layout.build_markdown`
  (drop chrome).
- **Approccio**: candidato per posizione → conferma per ripetizione con
  **masking dei numeri** e chiave di signature (cifre→`#`).
- **Test**: `tests/test_pack4_readability.py`, `tests/test_ir_flow.py`.
- **Accettazione**: `header_content` recall/chrome escluse ↓; nessun body perso.
- **Sforzo**: basso · **Ruolo**: fix · **Rischi**: soglia ripetizione.

#### M4E-1.4 — Didascalie: probabilità + offset proporzionale al font
- **Obiettivo**: agganciare didascalie sopra/sotto, con offset `1×fontSize`.
- **Sorgente**: ODL `CaptionProcessor` (`CAPTION_PROBABILITY=0.75`,
  offset `1×fontSize`, aspect `0.01`), papero `_link_captions`/`_is_figure_caption`.
- **Dove**: `ir_layout.build_markdown` (`_caption_text`, `_link_captions`),
  `main._link_figures`.
- **Approccio**: punteggio di probabilità (parola-chiave "fig/tab" + posizione +
  distanza ≤1×font + allineamento) e scelta della più probabile.
- **Test**: `tests/test_ir_figures.py`, `tests/test_figure_raster_tables.py`.
- **Accettazione**: caption orfane ↓; nessuna didascalia duplicata.
- **Sforzo**: basso · **Ruolo**: fix · **Rischi**: raddoppio caption (guardia
  geom.)

#### M4E-1.5 — Accenti disegnati + matematica tipografica
- **Obiettivo**: `Computa¸ca˜o`→`Computação`; frazioni/radici **disegnate** → testo
  (+LaTeX), e le righe-frazione **non** diventano bordi di tabella.
- **Sorgente**: papero `_compose_accents`/`compose_accent`, `_typeset_math`.
- **Dove**: `ir_layout` (pre-span), `layout_engine.cleanup_glyph_lines`.
- **Approccio**: ricomposizione accento→lettera per overlap; riconoscere
  numeratore/riga/denominatore per geometria.
- **Test**: nuovi unit in `tests/test_ir_text_fix.py`.
- **Accettazione**: righe LaTeX-TeX corrette; nessun bordo falso.
- **Sforzo**: basso/medio · **Ruolo**: fix · **Rischi**: confondere un bordo.

### Fase 2 — Classe dominante: vocabolario glifo-level → de-glue

#### M4E-2.1 — Vocabolario glifo-level pulito (sblocca de-glue/de-split)
- **Obiettivo**: ricostruire un **vocabolario di pagina pristino e pulito** (senza
  sillabazione/figure/chrome) da usare nel fix grounded di de-glue/de-split.
  È la **condizione per riprovare** annotata in `MIGLIORIE-MOTORE.md` §3 e
  `HANDOFF-2026-10-11.md` §7 (il tentativo precedente regrediva per vocabolario
  sporco).
- **Sorgente**: papero `read_chars`/`build_spans`/`_segments`/`words_of`;
  PdfPig `NearestNeighbourWordExtractor` (baseline, `20% di max(width,pointSize)`,
  5 orientamenti); pdfminer `group_objects` (soglie relative);
  pdfplumber `char_begins_new_word` (end→begin intra, top→top inter).
- **Dove**: nuovo helper `ir_layout` (es. `_clean_page_vocab(page, elements)`);
  consumato da `main._fix_bold_initials` (estensione) e da un nuovo
  `_fix_glued_tokens(md, vocab)`.
- **Approccio**: (a) vocabolario da `rawdict`, de-sillabato, escludendo figure/
  chrome; (b) de-glue/de-split **grounded** (una modifica è ammessa solo se la
  forma unita/spezzata **esiste** nel vocabolario e il token originale **non**);
  (c) determinismo e soglie relative al font.
- **Test**: `tests/test_ir_text_fix.py` (casi `byimagingor`, `Ab|sorption`,
  `**E** sophageal`); regressione sui 44 md del run §7.
- **Accettazione**: `glued_words` (diag) ↓ **senza** token extra; nessuna
  regressione su held-out; north-star ↓.
- **Sforzo**: medio · **Ruolo**: fix · **Rischi**: **alto** (il precedente era
  regredito) → guardia grounded stretta + validazione su held-out **prima** di
  promuovere.

### Fase 3 — Tabelle (classe severa)

#### M4E-3.1 — Tabella come immagine **parallela** (con trigger di qualità)
- **Obiettivo**: chiudere in modo "soft" l'unica classe **grave**
  (`ce24 p480`) senza perdere testo: il markdown resta la griglia, il **crop**
  va come allegato/galleria per le tabelle difficili.
- **Sorgente**: papero `_attach_images`/`render.block_markdown` (master §B.3).
- **Dove**: `ir_layout._emit_clip`/`_emit_image` (già esistenti per ruotate/
  raster), `main` (galleria/output); nuovo fix-flag.
- **Approccio**: **trigger** (content map rotta / inversioni / celle unite dal
  page model) → crop; altrimenti markdown. Nessuna duplicazione nel markdown.
- **Test**: `tests/test_figure_raster_tables.py`, `tests/test_ir_tables.py`.
- **Accettazione**: `ce24 p480` con immagine fedele **e** gate ok; 0 regressioni
  su tabelle già corrette.
- **Sforzo**: medio · **Ruolo**: fix · **Rischi**: metriche tabelle → vanno
  ridefinite (immagine = "gestita", come ruotata).

#### M4E-3.2 — Criterio "quality-improves" per la scelta griglia
- **Obiettivo**: decidere meglio **content-map vs griglia** ricostruita.
- **Sorgente**: ODL `TableStructureNormalizer.isReplacementQualityBetter`
  (più righe piene, non meno colonne, **ordine monotono**, meno celle oversize),
  pdfplumber `snap`/`join`/`intersections_to_cells`.
- **Dove**: `ir_layout.build_markdown` ramo `table` (oggi `base_r < 0.90`).
- **Approccio**: arricchire la condizione con i criteri di qualità; guardia:
  si accetta la ricostruzione **solo se migliora**.
- **Test**: `tests/test_ir_tables.py`, `tests/test_pack2_tables.py`.
- **Accettazione**: nessuna regressione dove la content map è corretta
  (`fe22 p1101`); miglioramento su tabelle sotto-segmentate.
- **Sforzo**: medio · **Ruolo**: fix · **Rischi**: medio (storicamente la griglia
  disallinea) → guardia monotonia.

### Fase 4 — Struttura (heading, liste, figure)

#### M4E-4.1 — Heading: probabilità + rarità font + livelli per TextStyle
- **Sorgente**: ODL `HeadingProcessor` (prob. 0.75, boost rarità, livelli per
  TextStyle; primo H1 = "Doctitle" da `LevelProcessor`), papero
  `_merge_heading_lines`.
- **Dove**: `layout_engine` (gerarchia titoli), `ir_layout` (`_heading_from_page`).
- **Test**: `tests/test_pack4_readability.py`, `tests/test_ir_order_log.py`.
- **Accettazione**: heading errati ↓; nessun titolo perso.
- **Sforzo**: basso/medio · **Ruolo**: fix.

#### M4E-4.2 — Liste: unione "neighbor" cross-blocco/pagina
- **Sorgente**: ODL `ListProcessor.checkNeighborLists`, tolleranza `fontSize×0.3`,
  ordered/unordered per "stesso left".
- **Dove**: `layout_engine` (liste), `ir_layout.build_markdown`.
- **Test**: `tests/test_pack4_readability.py`.
- **Accettazione**: liste spezzate da testo/salto pagina ↓.
- **Sforzo**: medio · **Ruolo**: fix.

#### M4E-4.3 — Grafici vettoriali + etichette
- **Sorgente**: papero `chart_regions`/`_plot_frames`/`_label_of`,
  `_compose_figures`.
- **Dove**: rilevazione figure in `ir_layout`/`main`.
- **Test**: `tests/test_ir_figures.py`, `tests/test_pack7_figures.py`.
- **Accettazione**: `figure_missing` ↓; etichette assorbite.
- **Sforzo**: medio · **Ruolo**: fix · **Rischi**: etichette di prosa assorbite.

### Fase 5 — Riferimenti indipendenti (invarianti/metro)

#### M4E-5.1 — Invarianti d'ordine da algoritmi indipendenti
- **Obiettivo**: **misurare** l'ordine con 2–3 algoritmi estranei al motore.
- **Sorgente**: Allen-graph (PdfPig `UnsupervisedReadingOrderDetector`),
  XY-Cut++ (ODL), `boxes_flow`+albero (pdfminer), XY-cut coverage (papero).
- **Dove**: `tools/measure_order.py` (esteso), `tools/invariants.py` (nuovo I6?),
  `tools/e2e.py` (`order` check).
- **Approccio**: implementazione **dev-only** (nessun runtime), come page
  model/oracolo; confronto con l'ordine emesso (`order_log`).
- **Test**: `tests/test_ir_order_regression.py`, `tests/test_page_model.py`.
- **Accettazione**: cattura la classe colonne/tabelle (`fe22 p1038`, `ce24 p480`)
  con **0 falsi positivi** su campione calibrato.
- **Sforzo**: medio/alto · **Ruolo**: metro (gate/registry).

#### M4E-5.2 — Invarianti di validazione (fidelity)
- **Sorgente**: papero `_backwards`/`_overlap`; ODL check overlap/blocchi.
- **Dove**: `tools/invariants.py`.
- **Accettazione**: cattura ordine/overlap con FP trascurabile.
- **Sforzo**: basso · **Ruolo**: metro.

#### M4E-5.3 — Struct tree del tagged PDF (riferimento autorevole)
- **Sorgente**: pdfplumber `structure.py` (ParentTree per-pagina vs root,
  `all_mcids`, `element_bbox`), ODL `TaggedDocumentProcessor`.
- **Dove**: dev-only, lettura xref via `pymupdf` `doc.xref_get_key`.
- **Accettazione**: confronto struttura su PDF taggati (reference).
- **Sforzo**: alto · **Ruolo**: metro.

---

## 4. Roadmap e dipendenze

```
Fase 1 (additiva, quick)  ─►  Fase 2 (vocabolario glifo-level)  ─►  Fase 3 (tabelle)
        ▲                                   │
        └────────── Fase 4 (struttura) ◄────┘        Fase 5 (riferimenti) in parallelo
```

- **Fase 1** non tocca il produttore: può iniziare subito; ogni voce è
  indipendente e piccola.
- **Fase 2** è ad **alto valore ma alto rischio**: richiede il vocabolario pulito
  (Fase 2) e validazione held-out **prima** di promuovere.
- **Fase 3** dipende dal criterio di qualità (M4E-3.2) e dalla definizione delle
  metriche tabella.
- **Fase 5** è parallela: costruisce i metri che servono ad arbitrare Fase 2–3.

Ogni fase: **stop** se la north-star non scende; si registra l'esito (positivo o
negativo) come per gli esperimenti parcheggiati in `MIGLIORIE-MOTORE.md` §3.

---

## 5. Ruoli gate/fix e registro

- **Gate (precisione)**: M4E-5.1, M4E-5.2, e i controlli di M4E-1.1.
- **Fix (recall)**: M4E-1.2…1.5, M4E-2.1, M4E-3.1/3.2, M4E-4.1…4.3.
- **Registro**: `defects.jsonl` con `role` (gate/fix) e `arbitration`; ogni voce
  del piano cita la classe e il commit.

---

## 6. Metodo di validazione (per ogni voce)

1. **Unit/sintetici** (`tests/test_ir_*.py`): sempre.
2. **Pinnati** sulle pagine confermate (regressione).
3. **Held-out** 2×N stratificato per classe: north-star `#difetti reali`.
4. **Arbitraggio** severo; mai tuning sulla pagina bersaglio.
5. **Determinismo**: md byte-identico a parità di input.

---

## 7. Rischi e mitigazioni

| voce | rischio | mitigazione |
|---|---|---|
| M4E-2.1 de-glue | regressione (già successo) | vocabolario pulito + guardia grounded + held-out |
| M4E-3.2 griglia | disallineamento dove content map è corretta | guardia "quality-improves" (monotonia righe) |
| M4E-3.1 tabella-immagine | metriche/galleria | ridefinire la metrica; bucket separato |
| M4E-4.3 grafici | assorbire prosa | soglie `_label_of`; `full_page` guard |
| M4E-5.1 ordine | falsi positivi | calibrazione a 0 FP prima di promuovere |
| tutte | nuove dipendenze | re-implementare su `pymupdf`; motori in `other-engines/` solo come riferimento |

---

## 8. Cosa NON facciamo (sostitutivi totali)

- **Non** sostituire `pymupdf4llm` (GNN) con pdfplumber/pdfminer/ODL/PdfPig.
- **Non** sostituire l'ordine GNN con XY-cut puro.
- **Non** sostituire la ricostruzione tabella in blocco (emissione dal page model
  **già regredita**: `MIGLIORIE-MOTORE.md` §3, `tools/measure_table_emit.py`).

Le uniche **sostituzioni** ammesse sono **selettive** (M4E-3.1/3.2) con trigger +
guardia.

---

## 9. Appendice — mappa sorgente → destinazione

| voce | sorgente (motore) | destinazione (nostro) |
|---|---|---|
| M4E-1.1 | papero `fidelity` | `tools/detectors.py`, `tools/e2e.py` |
| M4E-1.2 | PdfPig/ODL/pdfplumber dedup | `ir_layout.build_markdown`, `main._cosmetic_ir` |
| M4E-1.3 | PdfPig/ODL/papero furniture | `layout_engine` Pack 1 |
| M4E-1.4 | ODL/papero caption | `ir_layout._link_captions`, `main._link_figures` |
| M4E-1.5 | papero accenti/math | `ir_layout` (pre-span), `layout_engine` |
| M4E-2.1 | papero/PdfPig/pdfminer/pdfplumber parole | nuovo helper `ir_layout`, `main._fix_*` |
| M4E-3.1 | papero tabella-immagine | `ir_layout._emit_clip`, `main` (galleria) |
| M4E-3.2 | ODL/pdfplumber qualità griglia | `ir_layout.build_markdown` (ramo table) |
| M4E-4.1 | ODL/papero heading | `layout_engine`, `ir_layout._heading_from_page` |
| M4E-4.2 | ODL liste | `layout_engine`, `ir_layout.build_markdown` |
| M4E-4.3 | papero grafici | `ir_layout`/`main` figure |
| M4E-5.1 | PdfPig/ODL/pdfminer/papero ordine | `tools/measure_order.py`, `tools/invariants.py`, `tools/e2e.py` |
| M4E-5.2 | papero validazione | `tools/invariants.py` |
| M4E-5.3 | pdfplumber struct tree | dev-only |

**Flag `fix_rules.json`** (proposti): `dedupe_overlap_chars`,
`caption_probability`, `table_image_fallback`, `grid_quality_guard`,
`heading_probability`, `list_neighbor_merge`, `chart_vector_detect`,
`glyph_vocab_grounded`. (Ogni fix resta attivabile/disattivabile, come i Pack.)

---

*Fine piano. Documento di pianificazione: nessuna modifica al motore.*
