# PIANO — `migliorie4engines`

> **Nome.** `migliorie4engines` — migliorie al nostro engine ispirate dai quattro
> motori geometrici esterni (papero, OpenDataLoader, PdfPig, pdfplumber/pdfminer).
>
> **Stato.** Piano (backlog ordinato + roadmap + criteri di accettazione).
> Non implementato.
>
> **Riferimenti.** `STUDIO-IDEE-MOTORI-ESTERNI-2026-10-10.md` (**master** delle
> idee, con soglie e `file:riga`), `STUDIO-MOTORI-GEOMETRICI-ESTERNI-2026-10-10.md`,
> `STUDIO-PAPERO-PDF-EXTRACTOR-2026-10-10.md`,
> `STUDIO-PDFPIG-STATISTICA-2026-10-10.md` (approfondimento PdfPig sulle soglie
> auto-calibrate).
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

### Fase 6 — Soglie auto-calibrate (statistica di pagina)

#### M4E-6.1 — Soglie derivate dalla distribuzione della pagina
- **Obiettivo**: sostituire le soglie "magiche" in pt con **soglie auto-calibrate**
  dalla geometria della pagina (self-calibration, non supervisionata): stima della
  **spaziatura within-line** (istogramma delle distanze → picco → media del picco),
  della **dimensione di carattere dominante** (moda di larghezze/altezze) e della
  **tolleranza "uguale"**. È il substrato condiviso dei fix che oggi usano costanti
  assolute (glue, heading, furniture, contiguità).
- **Sorgente**: PdfPig `DocstrumBoundingBoxes.GetSpacingEstimation`/`GetPeakAverageDistance`
  (`:148,:238`), `Mode()` (`MathExtensions:16`), XY-cut `DominantFontWidth/HeightFunc`
  (`RecursiveXYCut:372,389`), `WhitespaceCover` `mode×1.25` (`:28`); dettagli in
  `STUDIO-PDFPIG-STATISTICA-2026-10-10.md`.
- **Dove**: nuovo helper **deterministico** `ir_layout._page_metrics(page)`
  (scheletro geometrico); consumato da M4E-2.1 (de-glue), M4E-4.1 (heading),
  M4E-1.3 (gap furniture) e come metro (`T`) per M4E-5.1.
- **Approccio**: (a) distanze NN dai glifi/baseline con ordinamento (no kd-tree,
  no dipendenze); (b) filtro **angolare** (within/between) come PdfPig; (c) istogramma
  → bin modale → **media del picco**; (d) **moda** con **tie-break esplicito**
  (niente `NaN`); (e) **guardie**: pochi glifi / distribuzione piatta / stima `0`/`NaN`
  → **default conservativo** (nessuna modifica), mai "soglia 0"; (f) single-thread,
  input ordinato → **determinismo (R12)**.
- **Test**: unit su pagine sintetiche a spaziatura/font noti; `tests/test_ir_text_fix.py`,
  `tests/test_ir_order_regression.py`; regressione (flag OFF ⇒ output invariato).
- **Accettazione**: north-star ↓ sulle classi con soglie fisse problematiche;
  **0** regressioni su held-out; nessun percorso con soglia degenere.
- **Sforzo**: medio · **Ruolo**: fix (fornisce soglie) + metro (spaziature, `T`) ·
  **Rischi**: distribuzione **sparsa/bimodale** → guardie + fallback conservativo;
  determinismo da garantire (i bug di PdfPig nascono dal parallelismo: `review.md`).

---

## 4. Roadmap e dipendenze

```
Fase 6 (soglie auto-calibrate) ─► Fase 1 (additiva, quick) ─► Fase 2 (vocabolario glifo-level) ─► Fase 3 (tabelle)
        │                                   │
        └────────── Fase 4 (struttura) ◄────┘        Fase 5 (riferimenti) in parallelo
```

- **Fase 6** fornisce le **soglie** (spaziature, font dominante, `T`) usate da
  Fase 2/4; additiva e gated (flag), può iniziare in parallelo alla Fase 1.
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
- **Fix (recall)**: M4E-1.2…1.5, M4E-2.1, M4E-3.1/3.2, M4E-4.1…4.3; M4E-6.1
  (fornisce le **soglie** auto-calibrate a Fase 2/4, oltre a un metro).
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
| M4E-6.1 soglie | stima degenere (pagina sparsa/bimodale) | guardie (n < min, distribuzione piatta) + fallback conservativo; moda con tie-break deterministico |
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

## 9. Tecnica di toggling (feature on/off)

> Obiettivo: ogni voce del piano è attivabile/disattivabile **a runtime** e
> testabile **con/senza**, senza reinventare il sistema esistente.

### 9.1 Meccanismo esistente (da riusare)
- `layout_engine.Fix(id, description, order, when, apply)` + `FIX_REGISTRY` +
  `plan_fixes()` + `apply_plan()`.
- Override utente `fix_rules.json` (accanto a `layout_engine.py`, opzionale):
  `{"disable": [...], "rules": [...]}`; letto da `_load_overrides` (lru-cached)
  e da `is_fix_disabled(id)`.
- Due tipi di toggle: **fix di registro** (entrano nel piano) e **check dirette**
  `is_fix_disabled("link_figures")` in `main.py`/`ir_layout`.

**Lacuna**: il file è **opt-out** (tutto ON). Le nuove feature devono essere
**opt-in** (OFF di default) e testabili con/senza.

### 9.2 Registro `FEATURES` + accessor

```python
@dataclass(frozen=True)
class Feature:
    id: str
    default: bool        # False per le nuove (opt-in)
    stage: str           # pre_span|order|table|text|furniture|heading|list|figure|caption|metric|ref
    role: str            # fix | gate | metric | ref
    params: dict         # nome -> default (soglie)
```

Accessor unici (accanto a `is_fix_disabled`):

```python
features.enabled(id) -> bool           # default -> file -> override ambientale
features.param(id, name) -> value      # soglia configurabile
```

### 9.3 Schema `fix_rules.json` esteso

```json
{
  "disable": [ "...fix esistenti..." ],
  "enable":  [ "dedupe_overlap_chars", "glyph_vocab_grounded" ],
  "params":  { "caption_probability": {"threshold": 0.75, "offset_font": 1.0},
               "grid_quality_guard": {"min_rows": 3, "row_order_eps": 1.5} },
  "rules":   [ "...regole custom esistenti..." ]
}
```

Precedenza: `default` → `enable`/`disable` (**disable vince**) → **override
ambientale** (test/CLI).

### 9.4 Override ambientale (test con/senza, senza toccare file)
- **Context manager**: `with layout_engine.feature_override({"glyph_vocab_grounded": True}): ...`
- **Env**: `NOESIS_FEATURES="+dedupe_overlap_chars,-table_image_fallback"` (letto all'avvio).
- Bypassa la cache `lru_cache` di `_load_overrides` (evita il "file modificato, cache vecchia").

### 9.5 Uso al punto d'applicazione

```python
if layout_engine.enabled("dedupe_overlap_chars"):
    md = _dedupe_overlap_chars(md, page)
# o come fix di registro: when=lambda p, b: enabled("grid_quality_guard")
```

### 9.6 Due scope
- **Runtime** (in `layout_engine`/`main`/`ir_layout`): cambiano l'output; default
  OFF; **non** esposti nel menu Impostazioni (sperimentali → solo
  `fix_rules.json`/env).
- **Tool/dev** (in `tools/`): invarianti/metriche/riferimenti; attivati da
  CLI/env, **mai** a runtime.

### 9.7 Toggle per ciascuna voce

| voce | flag id | scope | default | dove | param | metrica A/B |
|---|---|---|---|---|---|---|
| M4E-1.1 | `metric_sans_separators` | tool | off | `tools/detectors.py`,`tools/e2e.py` | — | FP detector |
| M4E-1.2 | `dedupe_overlap_chars` | runtime | off | `ir_layout`/`_cosmetic_ir` | `tol_ratio=1/3` | `diag.duplicate_lines` |
| M4E-1.3 | `furniture_crosspage` | runtime | off | `layout_engine` Pack1 | `repeat_need=2`,`gap=30` | `header_content` |
| M4E-1.4 | `caption_probability` | runtime | off | `ir_layout._link_captions` | `threshold=0.75`,`offset_font=1.0` | caption orfane/dup |
| M4E-1.5 | `glyph_accents_compose`,`typeset_math` | runtime | off | `ir_layout` pre-span | — | md + unit |
| M4E-2.1 | `glyph_vocab_grounded` | runtime | off | `main._fix_*` | `minlen=3`,`slack=1` | `diag.glued_words` |
| M4E-3.1 | `table_image_fallback` | runtime | off | `main._apply_ir_on_page_full` | `trigger=quality` | `ce24 p480` |
| M4E-3.2 | `grid_quality_guard` | runtime | off | `ir_layout` ramo table | `min_rows=3`,`row_order_eps=1.5` | `base_r/grid_r` |
| M4E-4.1 | `heading_probability` | runtime | off | `layout_engine` heading | `threshold=0.75` | heading ok/ko |
| M4E-4.2 | `list_neighbor_merge` | runtime | off | `layout_engine` liste | `xgap_ratio=0.3`,`lookback=500` | liste ok/ko |
| M4E-4.3 | `chart_vector_detect` | runtime | off | `ir_layout` figure | `fuzziness=0.15` | `figure_missing` |
| M4E-5.1 | `ref_order_allen` | tool | off | `tools/measure_order.py` | `T=5` | confronto ordine |
| M4E-5.2 | `ref_validation` | tool | off | `tools/invariants.py` | — | FP invarianti |
| M4E-5.3 | `ref_struct_tree` | tool | off | `tools/` | — | confronto struttura |
| M4E-6.1 | `adaptive_thresholds` | runtime | off | `ir_layout._page_metrics` | `bin_size=10`,`wl_mult=3.0`,`bl_mult=1.3`,`height_f=1.5` | north-star soglie + determinismo |

### 9.8 Harness A/B ("con/senza")

```bash
tools/e2e.py --sample <held-out.json> --mode auto --ab dedupe_overlap_chars
```

Riporta: (1) **byte-diff** del markdown (vuoto se OFF, non-vuoto se ON → prova
che il flag collega la feature); (2) **delta `diag`** (`glued_words`,
`duplicate_lines`, `figure_blank`, `table_misalign`, `order`, `flow`);
(3) **delta north-star** (#difetti reali); (4) **determinismo** (OFF = baseline
byte-identico). `tools/campaign_report.py` aggrega una **tabella per-flag**.

### 9.9 Garanzie
1. **OFF = baseline**: senza file/env l'output è **byte-identico a oggi**.
2. Nessun flag ON di default prima della validazione held-out.
3. `disable` vince sempre.
4. Determinismo (letture statiche di stato).
5. Tuning **solo su held-out**.

### 9.10 Pitfall
- **Gate/chooser**: `table_image_fallback`/`grid_quality_guard` cambiano la
  metrica → vanno letti **anche** in `_ir_gate`/`_choose_output`.
- **Galleria**: tabelle-come-immagine → bucket/flag separato.
- **Cache** `_load_overrides` (lru): per l'A/B usare l'override ambientale.
- **Interazioni**: un flag alla volta nell'A/B; ordine via `Fix.order`.
- **`when(profile,backend)`**: il flag si **combina** col predicato, non lo
  sostituisce.

### 9.11 Promozione
`default=False` + test `off=baseline` → A/B held-out → se la north-star scende,
**flip di `default` a `True`** (una riga, code review); resta disattivabile via
`disable`. Esito negativo → resta `False` e l'esito si registra (come gli
esperimenti parcheggiati in `MIGLIORIE-MOTORE.md` §3).

---

## 10. Appendice — mappa sorgente → destinazione

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
| M4E-6.1 | PdfPig Docstrum/Mode | nuovo helper `ir_layout._page_metrics` |

**Flag `fix_rules.json`** (proposti): `dedupe_overlap_chars`,
`caption_probability`, `table_image_fallback`, `grid_quality_guard`,
`heading_probability`, `list_neighbor_merge`, `chart_vector_detect`,
`glyph_vocab_grounded`, `adaptive_thresholds`. (Ogni fix resta
attivabile/disattivabile, come i Pack; la **tecnica completa** di toggling e il
test con/senza sono in **§9**.)

---

*Fine piano. Documento di pianificazione: nessuna modifica al motore.*
