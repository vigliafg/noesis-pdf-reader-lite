# VERDETTO E2E — 2026-10-08 (RUN L + RUN M, 500 pagine)

Riferimenti: `STUDIO-E2E-250l-2026-10-08.md`, `STUDIO-E2E-250m-2026-10-08.md`,
`HANDOFF-2026-10-08.md`, `DIARIO-2026-10-04.md` §9–§11.

## 1. Cosa è stato chiuso in questa sessione

### Harness / app (sbloccano il gate automatico) — commit `319f82f`
1. **Attribuzione pipeline**: `ExtractThread` espone la pipeline **reale**
   (`ir`/`current`) nel segnale `result_ready` e in `self.engine`/`self.fallback`;
   `tools/e2e.py::_engine_used` la **osserva** dal record (non la ri-deriva).
   Niente più misattribuzioni `ir`↔`current`.
2. **Fallback non silenzioso**: il passaggio a `current` è registrato
   (`engine` nel record finalizzato della cache; `_CACHE_REVISION` 1→2).
3. **FP `text_missing` da chrome** (`mw15 p261`): `_page_text_no_figures`
   esclude testatine/piè/numero con tolleranza bbox dedicata (6 pt).
4. **FP `text_missing` da trattini**: `verify_pages._norm` riconcilia il
   trattino composto spaziato (`new - onset` ↔ `New-onset`).

### Rifiniture motore — commit `05d0fce`, `9b5a8b6`
5. **Tabelle-figura** (`fe22 p207`, `ha22 p3355`, `ce24 p68`): un pannello
   figura classificato `table` ma coperto da una regione-figura **non è una
   tabella** (è reso come immagine). Escluso dal riferimento e rimosso il
   blocco-tabella duplicato (`_figure_covered_table_rects`, `_link_figures`).
6. **Ordine/interleaving** (`su18 p279`): `_group_figure_blocks` non fonde più
   figure di colonne diverse a quote vicine (falso blocco a tutta larghezza).
7. **FP metrica**: `_mostly_inside` (banner-pubblicitario, `pa23 p935`);
   `_norm` separa gli apici incollati (`GenioglossusXII`, `ox2 p717`).

### Determinismo — commit `b1ea0cc`
8. **OCR serializzato** (`ir_layout._PDF_OCR_LOCK`): Leptonica non è
   thread-safe; due estrazioni concorrenti sollevavano `FzErrorArgument`
   ("Attempt to use Leptonica from 2 threads at once!") → fallback silenzioso →
   md non deterministico. Ora l'OCR è serializzato.

## 2. Esito dei due run di conferma

| run | campione | pagine | difetti reali | flag auto | flow min | inversioni max | figure mancanti |
|---|---|---|---|---|---|---|---|
| **L4** | casuale `20261017` | 250 | **0** | **0** | 1.000 | 0 | 0 |
| **M3** | stratificato `20261018` + fill `20261019` | 250 | **0** | **0** | 1.000 | 0 | 0 |
| **totale** | — | **500** | **0** | **0** | 1.000 | 0 | 0 |

- **Determinismo**: L4 vs L5 (stesso campione, codice finale) → **250/250 md
  byte-identici**.
- **Tabelle**: recall media 0.9888 (L4) / 0.9984 (M3); nessuna sotto soglia.
- **Copertura**: il run M tocca le classi fragili (equazioni, figure, tabelle,
  colonne, indici).

## 3. Criteri di accettazione

| criterio | soglia | esito |
|---|---|---|
| difetti reali (arbitro) | 0 su 500 pagine | **0** ✓ |
| ordine stringente (`inversioni` \| `fuori-ordine`) | 0 su tutte | **0** ✓ |
| `flow` (LIS) | ≥ 0.95 e nessun intreccio | **1.0000** ✓ |
| falsi positivi / falsi negativi | 0 / 0 | **0 / 0** ✓ |
| figure mancanti | 0 | **0** ✓ |
| determinismo | stesso input → md byte-identico | **sì** (L4=L5) ✓ |
| suite (`unittest discover`) | verde | **470 OK** (17 skipped) ✓ |

## 4. VERDETTO

**Il motore IR soddisfa i criteri di accettazione su 500 pagine nuove
(250 casuali + 250 stratificate held-out): 0 difetti reali, 0 FP/0 FN, flusso di
lettura perfetto, 0 figure mancanti, deterministico.**

La Golden Rule #1 (contenuto **e** flusso di lettura) è rispettata. L'harness
non produce più falsi positivi né falsi negativi.

## 5. Prossimo passo (Fase C) — merge

Come da piano, il **merge del motore su `main`** spetta all'**utente come
arbitro**, dopo la **verifica manuale**. I fix dell'harness restano su
`layout-order` (`tools/`). Nessuna auto-sostituzione del deterministico.

Commit del motore/app da portare su `main`:
`319f82f`, `05d0fce`, `9b5a8b6`, `b1ea0cc` (più `ir_layout.py`, `main.py`).
I fix harness (`tools/e2e.py`, `tools/verify_pages.py`) restano in
`layout-order`/`tools`.

### Verifica manuale suggerita
1. `./run-ir.sh` sul worktree e apertura di alcuni PDF (in particolare pagine a
   colonne, tabelle, figure multi-pannello, aperture capitolo).
2. Confronto visivo di `STUDIO-E2E-250l/m` (campioni e report in
   `~/.local/share/opencode/e2e250l4`, `.../e2e250m3`).
3. Conferma → merge su `main` → (Fase D) redesign UI.
