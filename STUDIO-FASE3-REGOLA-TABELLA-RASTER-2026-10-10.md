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

## 6. Invarianti residui I2/I4 — indagine (report)

Prima di promuovere nuovi invarianti a **bloccanti**, li si misura in report mode.

**I2 — adiacenza figura↔didascalia.** Sui 500 md salvati (L6+M4): **306**
immagini, **139** didascalie-figura; solo **15** risultano a >3 righe da ogni
immagine. La distanza minima è **4–6 righe** per la quasi totalità (adiacenza
sana); solo 3 hanno distanza ≥8, di cui una è un **riferimento** nel testo
(`Fig 8 shows…`), non una didascalia. → **Nessuna classe reale di didascalie
staccate** emerge dal motore attuale; l'invariante non è promosso (sarebbe rumore
di falsi positivi).

**I4 — no duplicazione.** La duplicazione reale (testo **+** immagine) è quella
chiusa in §2–3 (tabella-**raster** dentro una figura). Una tabella **vettoriale**
dentro una regione-figura (`fe22 p207`) è invece contenuto legittimo da tenere:
senza geometria non esiste un invariante **md-only** che non generi falsi
positivi. Resta coperta dalla regola deterministica §3 (caso reale), non da un
nuovo bloccante.

**Esito:** il linkage figura/didascalia del motore è **solido** su 500 pagine;
nessun invariante bloccante aggiuntivo. I residui della Fase 3 restano le
**pagine formula**.

## 7. Gate di conferma (Fase 5) — NON superato

Due run nuovi **held-out** da **250 pagine** (seed `20261022`, `20261023`),
`--mode auto --via-app`. Esito: **~4 difetti reali su 500** — ordine su tabella
complessa (`ce24 p480`), colonne interlacciate (`fe22 p1038`), figura vuota
(`su19 p1050`), pagina formula (`arxiv p30`). **Merge non pronto.** Dettaglio in
`STUDIO-E2E-VERDETTO-2026-10-11.md`.
