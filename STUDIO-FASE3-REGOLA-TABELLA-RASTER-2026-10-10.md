# Fase 3 — Regola deterministica: tabella-raster (pannello-immagine) — 2026-10-10

Riferimenti: piano `piano-implementazione-rilevamento-2026-10-09` (§Fase 3),
`STUDIO-FASE2-ORACOLO-INDIPENDENTE-2026-10-10.md`,
`STUDIO-INVARIANTI-2026-10-09.md`.

Principio (piano §1.4): **ogni caso confermato → test di regressione + regola
deterministica**. Qui si chiude il primo candidato confermato dalla Fase 2.

## 1. Candidati della Fase 2, riletti sull'**output**

L'arbitraggio della Fase 2 era sugli **elementi** del motore. Ricontrollando
l'**output** della pipeline (`main._apply_ir_on_page_full`), quasi tutti i
candidati risultano **già gestiti**:

| pagina | elemento (Fase 2) | output attuale | esito |
|---|---|---|---|
| `co23 p743` | table vs figure | lista + immagine (nessuna tabella) | **ok** |
| `co23 p1428` | table vs plain text | prosa (nessuna tabella) | **ok** |
| `cu25 p28` | oracle table non corrisposta | tabella emessa correttamente | **ok** |
| `arxiv_2609.38133 p16` | formula vs plain text | fallback `current` (gate recall 0.87) | residuo noto (pagine formula) |
| `ha22 p3355` | table vs figure | **tabella-spazzatura + immagine** | **DIFETTO REALE** |

Quindi l'unico difetto d'output è `ha22 p3355`.

## 2. Il difetto (`ha22 p3355`, idx 3354)

Pagina con la figura EEG (`FIGURE 425-3`, **immagine raster**). PyMuPDF4LLM
classifica l'intero pannello come `table` e ne produce una tabella di
**OCR-spazzatura**:

```
|---|---|
| ti eee | yarUte |
| S eamecenia all Ceci i hh ah F3-A1 | A WW |
...
```

`_link_figures` la rimuoverebbe solo se i token della tabella fossero ⊂ del testo
interno della figura (`btokens <= itokens`). Qui **fallisce**: il content map
contiene token OCR (`apelen`, `yarUte`, `PWADDRAV`) assenti nel testo vettoriale
di pagina → resta **doppio contenuto** (tabella-spazzatura + immagine).

## 3. Regola deterministica

Discriminante **geometrico**, indipendente dall'OCR:

> Una `table` della content map **dentro una regione-figura** e il cui bbox è
> **coperto ≥50% da un'immagine raster incorporata** (`page.get_image_info`) è il
> contenuto OCR di quel pannello: **non va emessa come tabella** (il pannello è
> già reso come immagine dalla figura).

Contro-caso (perché serve il vincolo raster): `fe22 p207` FIG. E2 è una tabella
**vettoriale** reale, coperta da figura ma **senza** immagine raster → va
**tenuta**. `ce24 p68` (modulo ESAS, vettoriale) → tenuta.

Implementazione (`main.py`):

- `_raster_figure_table_rects(page, elements)`: tra le
  `_figure_covered_table_rects` (già esistenti), quelle con copertura raster ≥0.5.
- `_strip_table_blocks(md, elements, rects)`: rimuove i blocchi `|...|` del md
  che corrispondono a quegli elementi per **sovrapposizione di token** (soglia
  0.3, il md riformatta la tabella); robusto ai token OCR.
- chiamate in `_apply_ir_on_page_full` **prima** di `_link_figures` (che poi
  inserisce l'immagine alla didascalia).

Nessuna dipendenza nuova; deterministico; nessun effetto sul runtime di pagine
senza tabelle-raster.

## 4. Verifica

- `ha22 p3355`: tabella-spazzatura **rimossa**, immagine + `FIGURE 425-3`
  presenti; gate ok.
- `fe22 p207`: tabella reale **conservata** (nessuna regressione).
- `ce24 p68`: invariato.
- Test: `tests/test_figure_raster_tables.py` (3 unit + 2 corpus, CI-safe).
- Misura d'impatto (L6+M4, 500 pagine): **0 pagine toccate** (la regola è
  inerte sul corpus validato: nessuna pagina è un pannello-raster dentro una
  figura). Agisce solo su `ha22 p3355`. Nessuna regressione su L6/M4.

## 5. Resta per i prossimi passi (Fase 3)

- **Pagine formula** (`arxiv p16`): il gate fa fallback a `current` (prosa/recall
  0.87). Serve una regola deterministica sul recupero prosa/formule (candidato
  dall'oracolo: più blocchi di prosa).
- **Invarianti residui**: formalizzare **I2** (adiacenza figura↔didascalia) e
  **I4** (no duplicazione/copertura) e I5 (purezza di colonna); calibrare i FP →
  bloccanti.
- `ha22 p3355` mostra anche che il metro escludeva la tabella-figura: la
  regola chiude il **gap tra metro e output** (il difetto non era nel verdetto).
