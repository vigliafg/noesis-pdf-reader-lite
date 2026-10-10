# Migliorie del motore — indice e cronologia

> **Scopo.** Un unico posto che raccoglie *cosa* è stato migliorato nel motore di
> estrazione, *quando*, *con quale esito* e *dove* sta la documentazione di
> dettaglio. I documenti `STUDIO-*`/`HANDOFF-*` restano la fonte completa; qui
> c'è la mappa.
>
> **Come usarlo.** Per orientarsi: §1 (cos'è il motore), §2 (cronologia per
> area), §3 (esperimenti parcheggiati), §4 (metodo di validazione), §5 (strumenti
> dev), §6 (indice di tutta la documentazione).
>
> La fonte **autoritativa** delle singole modifiche resta `git log` (i messaggi
> di commit sono dettagliati e collegati per area).
>
> **Percorsi.** Tutti i documenti `STUDIO-*`, `HANDOFF-*`, `DIARIO-*`, `PIANO-*`
> e i riferimenti `REGOLE-TEST.md` e `PYMUPDF4LLM-KNOWLEDGE-BASE.md` risiedono in
> **`docs/`** (la guida utente in `docs/help/`). I nomi di file citati qui sotto
> sono da intendersi relativi a `docs/`.

---

## 1. Il motore in breve

`noesis-pdf-reader-lite` mostra, affiancati, la pagina renderizzata (**PyMuPDF**)
e il markdown estratto. L'estrazione è **PyMuPDF4LLM** (content map) + il nostro
layer `ir_layout` (ordine, figure, tabelle, indici, cosmetica) + `layout_engine`
(i "pack" di fix adattativi, sempre attivi). Nessun dropdown a runtime.

Pipeline IR: `page_chunk` (una passata) → `ir_layout.build_markdown` → fix in
`main._apply_ir_on_page_full` (tabelle raster, figura unite, cosmetica, integrità
testo) → gate/chooser IR vs `current`.

I fix sono raggruppati in **Pack 1–6** (vedi `README.md`): pulizia markdown,
tabelle, figure, leggibilità, layout difficili.

---

## 2. Cronologia delle migliorie (per area)

### 2.1 Pipeline IR e scelta di default (2026-10-02)
- Confronto sistematico **IR vs `current`**: `STUDIO-IR-2026-10-02.md`,
  `STUDIO-IR-150-2026-10-02.md`, `STUDIO-IR-150-T5-2026-10-02.md`
  (`tools/ir150.sh`, `tools/ir_study_*`).
- Esito: l'IR diventa il percorso di riferimento, con **gate d'integrità** e
  fallback a `current` pagina per pagina.

### 2.2 Ordine di lettura — Golden Rule #1 (2026-10-06 → 10-07)
- **Golden Rule #1**: il **flusso di lettura** è difetto primario quanto il
  contenuto (`STUDIO-REGOLA-ORDINE-2026-10-06.md`, `REGOLE-TEST.md`, commit
  `df00b15`, `4fbd89a`).
- Fix colonne robuste ai blocchi "a ponte" e riferimento a bande (`67354ad`,
  `b12a86e`), ordine **per banda** per layout misti 2/3 colonne (`4ab8893`),
  niente figura singolo-pannello a tutta larghezza (`8e83a7d`).
- **Ordine stringente** a blocchi (inversioni = 0) come metrica (`a3731e7`,
  `0f9e1d8`); run G/H e `STUDIO-E2E-VERDETTO-2026-10-07.md`.
- Prova d'ordine dedicata: `PROVA-ORDINE-ha22-p1977-2026-10-04.md`.

### 2.3 Tabelle (2026-10-04 → 10-10)
- Fix D1/D2 (spazio nelle celle, header a 2 livelli) — `4a43a7a`,
  `STUDIO-TABELLE-2026-10-04.md`.
- Tabelle **ruotate** rese come immagine (gate) — `42a6cdc`.
- **Pannello-figura (raster) classificato come tabella NON è una tabella** —
  `05d0fce`/`7eee3a8`, `STUDIO-FASE3-REGOLA-TABELLA-RASTER-2026-10-10.md`.

### 2.4 Figure (2026-10-01 → 10-08)
- Embedding figure/flowchart (`HANDOFF-pack7-figure-2026-10-01.md`),
  `STUDIO-FIGURE-2026-10-04.md`.
- Fix figura/box nell'ordine (`5bdff6f`).
- **Fix picture interamente fuori pagina** (evita figura vuota: `su19 p1050`) —
  commit `9db71dd`.
- Icona dell'app a runtime + bundle nei build — `fb28a96`.

### 2.5 Indici e rumore (2026-10-04)
- Percorso dedicato per le pagine d'**indice** (`index_markdown`) —
  `STUDIO-INDICI-2026-10-04.md`.
- Riduzione del **rumore** (header/footer, bullet decorativi, titoli spezzati) —
  `STUDIO-RUMORE-2026-10-04.md`, Pack 4.

### 2.6 Determinismo e robustezza harness (2026-10-07 → 10-08)
- Serializzazione dell'OCR (Leptonica non thread-safe) — `b1ea0cc`.
- `_norm` gestisce soft-hyphen/trattino (`b690160`); niente falso difetto testo
  su pagine vuote (`bd4c7f3`); chrome escluso dal recall (`3547381`).
- Pipeline **osservata** + fallback esplicito + FP metrica (`319f82f`).

### 2.7 Chooser IR/`current` (2026-10-05)
- Recupera figure e prosa quando il gate fallisce (`1fecf0f`), verificato su 250
  pagine (`1b46bde`).

### 2.8 Invarianti strutturali (2026-10-08 → 10-09)
- **I1** (box a tutta larghezza contigui) e **I3** (monotonia di banda) come
  controlli indipendenti dal motore (`5bdff6f`, `tools/invariants.py`,
  `STUDIO-INVARIANTI-2026-10-09.md`). Al gate: I1/I3 = 0/500 (`9e98789`).

### 2.9 Riferimento indipendente — Fase 2 (2026-10-10)
- **Oracolo DocLayout-YOLO/ONNX**, **dev-only** (licenza AGPL): misura le
  divergenze di classificazione senza dipendere dal motore —
  `STUDIO-FASE2-ORACOLO-INDIPENDENTE-2026-10-10.md`, `f0e2b85`, `a3599c3`.

### 2.10 Page model — schema Docling (2026-10-11) → **parcheggiato**
- Rappresentazione logica gerarchica di pagina (colonne, nesting, tabelle con
  span, sezioni, invarianti S1–S5), scheletro geometrico deterministico —
  `STUDIO-PAGE-MODEL-2026-10-11.md`, `page_model.py`.
- Usato come **riferimento diagnostico indipendente**, non come produttore
  (esperimenti in §3).

### 2.11 Diagnostica e tipologie di difetto (2026-10-11)
- **Hook `order_log`** opt-in non distruttivo in `ir_layout.build_markdown`;
  `tools/measure_order.py` (confronto ordine su pagina **pristina**).
- **Vettore diagnostico** per pagina + tipologie: `figure_blank` (figura
  piatta/bianca decodificata), `table_misalign`, `duplicate_lines`,
  **`text_integrity`** (token del md assenti dalla pagina) — `tools/e2e.py`,
  `tools/detectors.py`, commit `9db71dd`, `4730df7`.
- **Registro difetti** con `role` (gate/fix) e `arbitration`.

### 2.12 Fix integrità testo (2026-10-08 → 10-09)
- **Bold-initial grounded**: `**E** sophageal` → `**E**sophageal` **solo** se la
  parola unita esiste nella pagina (non tocca «vitamin **D** deficiency») —
  `6d696c2`, `main._fix_bold_initials`, `tests/test_ir_text_fix.py`.
- Detector `glued` corretto (i marker di enfasi non introducono spazi) —
  `64b57c8`.
- *de-glue/de-split* generico tentato e **parcheggiato** (regrediva) — `4610331`.

---

## 3. Esperimenti parcheggiati (e perché)

| esperimento | esito | dove |
|---|---|---|
| **Table emission** dalla griglia del page model | **regredisce** dove il motore è corretto (`plos…0059363 p8` 0→18 inversioni); su `ce24 p480` migliora solo marginalmente | `STUDIO-PAGE-MODEL-2026-10-11.md` §8, `tools/measure_table_emit.py` |
| **Page model come produttore d'ordine** | non giustificato: l'ordine del motore è già quasi sempre corretto | `STUDIO-PAGE-MODEL-2026-10-11.md`, `HANDOFF §6` |
| **de-glue/de-split** grounded | **regredisce** (37→56 token extra): il vocabolario da `get_text` contiene frammenti di sillabazione e parole di figure/chrome | `HANDOFF §7`, commit `4610331` |

---

## 4. Metodo di validazione

- **Golden Rule #1** (`REGOLE-TEST.md`): fedeltà = contenuto **e** flusso di
  lettura. Un ordine intrecciato è difetto grave anche con recall 1.0.
- **Campioni held-out grandi**: run E2E da **250 pagine** (run A–M),
  stratificati per classe di layout; **nessun tuning sulla pagina bersaglio**.
- **Arbitraggio severo**: tutte le pagine flaggate + campione delle pulite;
  rubric di severità (0 pulito … 3 grave, 4 critico).
- **Riferimenti indipendenti** dal motore: invarianti geometrici (I1/I3),
  oracolo DocLayout-YOLO, page model — per non dipendere dal motore stesso.
- **Campagna finale (2026-10-08/09)**: 2 generali ×250 + 3 settoriali
  (figure 100 · tabelle 92 · box 100) → **nessun errore grave su 792 pagine**;
  residuo word-level di `pymupdf4llm`, entità trascurabile–minore. Dettaglio in
  `HANDOFF-2026-10-11.md` §8.
- **Verdetti per run**: `STUDIO-E2E-VERDETTO-2026-10-07/08/09/11.md`.

---

## 5. Strumenti dev (non a runtime)

- **Diagnostica ordine**: `tools/measure_order.py`, `tools/measure_page_model.py`.
- **Invarianti**: `tools/invariants.py`, `tools/measure_invariants.py`.
- **Classificazione/rumore**: `tools/measure_classification.py`,
  `tools/measure_independent.py`, `tools/noise_census.py`, `tools/audit.py`,
  `tools/table_diag.py`, `tools/diag_figs.py`.
- **Campagna**: `tools/scan_objects.py`, `tools/build_campaign.py`,
  `tools/run_campaign.py`, `tools/campaign_report.py`, `tools/mk_review.py`.
- **Harness E2E**: `tools/e2e.py`, `tools/e2e_sample.py`, `tools/e2e_blocks.py`,
  `tools/verify_pages.py`, `tools/detectors.py`, `tools/layout_proxies`*.
- **Oracolo**: `tools/golden_build.py` (dev-only).

\* `layout_proxies.py` è modulo **root** (usato anche dal runtime per colonne/indice).

---

## 6. Indice della documentazione

**Entry point**: `README.md`, `REGOLE-TEST.md`,
`PYMUPDF4LLM-KNOWLEDGE-BASE.md`, `docs/help/` (guida utente, multilingua).

**Handoff/resume**: `HANDOFF-2026-10-03/04/08/09/10/11.md`,
`HANDOFF-pack4-leggibilita-2026-09-30.md`,
`HANDOFF-pack7-figure-2026-10-01.md`, `DIARIO-2026-10-04.md`.

**Piani**: `PIANO-fix-layout-2026-09-29.md`,
`PIANO-gnn-ordine-tabelle-2026-10-04.md`, `NEXT_STEPS-cattura-manuale.md`.
(Il piano `piano-implementazione-rilevamento-2026-10-09.md` è fuori dal repo, in
`~/.opencode/plan`.)

**Studi per area**: `STUDIO-REGOLA-ORDINE-2026-10-06.md`,
`PROVA-ORDINE-ha22-p1977-2026-10-04.md`, `STUDIO-TABELLE-2026-10-04.md`,
`STUDIO-FASE3-REGOLA-TABELLA-RASTER-2026-10-10.md`,
`STUDIO-FIGURE-2026-10-04.md`, `STUDIO-INDICI-2026-10-04.md`,
`STUDIO-RUMORE-2026-10-04.md`, `STUDIO-INVARIANTI-2026-10-09.md`,
`STUDIO-FASE2-ORACOLO-INDIPENDENTE-2026-10-10.md`,
`STUDIO-PAGE-MODEL-2026-10-11.md`.

**Riferimenti esterni**: `STUDIO-PAPERO-PDF-EXTRACTOR-2026-10-10.md` (analisi
completa del progetto `papero`, CPU-only no-ML); `STUDIO-MOTORI-GEOMETRICI-ESTERNI-2026-10-10.md`
(consolidato dei motori geometrici esterni — papero, OpenDataLoader XY-Cut++,
PdfPig, pdfplumber/pdfminer — con le idee mappate sulle nostre classi di difetto).
I **cloni locali** di questi motori stanno in `other-engines/` (indice:
`other-engines/README.md`; popolare/aggiornare con `tools/fetch-other-engines.sh`;
i cloni **non** sono versionati, solo l'indice).

**Pipeline IR**: `STUDIO-IR-2026-10-02.md`, `STUDIO-IR-150-2026-10-02.md`,
`STUDIO-IR-150-T5-2026-10-02.md`, `STUDIO-IR-150-notes-per-blocco.md`.

**Validazione E2E**: `STUDIO-E2E-250{a…m}-2026-10-0*.md`,
`STUDIO-E2E-60-PARZIALE-2026-10-03.md`, `STUDIO-E2E-80-2026-10-04.md`,
`STUDIO-E2E-10-BLOCKS-2026-10-04.md`,
`STUDIO-E2E-ADVISOR-2026-10-03.md`,
`STUDIO-E2E-ADVISOR-2CORPORA-2026-10-03.md`,
`STUDIO-E2E-VERDETTO-2026-10-07/08/09/11.md`.
