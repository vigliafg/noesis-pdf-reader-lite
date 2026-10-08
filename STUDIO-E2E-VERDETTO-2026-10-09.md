# VERDETTO E2E — 2026-10-09 (invarianti strutturali, 500 pagine)

Aggiorna `STUDIO-E2E-VERDETTO-2026-10-08.md` dopo la chiusura della classe
"box a tutta larghezza" (falso negativo trovato dall'utente su `su19 p383`).

Riferimenti: `STUDIO-INVARIANTI-2026-10-09.md`, `DIARIO-2026-10-04.md` §12.

## 1. Cosa è stato chiuso

- **Cecità correlata del verdetto**: motore e riferimento condividevano lo stesso
  modello di layout. Risolto affiancando **invarianti indipendenti**
  (`tools/invariants.py`): **I1** contiguità dei box a tutta larghezza, **I3**
  monotonia di banda, integrati come check `structure` (bloccante).
- **Fix motore** (`ir_layout.py`): i box a tutta larghezza sono una **banda
  contigua**; la **figura in basso** non a tutta larghezza (+ didascalia) è una
  **banda finale**; il riferimento è reso **box-aware**.

## 2. Validazione

| | valore |
|---|---|
| `su19 p383` (caso guida) | **pulita** (order 0, flow 1.0, `structure` ok, recall 1.0) |
| su19 "Key Points" (54 pagine) | **0 KO di struttura** dopo il fix (45/54 prima) |
| falsi positivi invarianti | **0** su 100 pagine casuali (calibrazione) |
| **RUN L6** (250 casuali, seed 20261017) | **250/250 pulite, 0 difetti** |
| **RUN M4** (250 stratificate held-out, seed 20261018) | **250/250 pulite, 0 difetti** |
| **invarianti I1/I3** (500 pagine L6+M4) | **0 violazioni** |
| regressioni vs L4/M3 | **0** (0 cambi engine) |
| differenze md vs L4 | 15 pagine, **riordino** (figura in basso spostata a fine) |
| suite | **470 OK** (17 skipped) |
| determinismo | md byte-identico (L4=L5; il fix non introduce non determinismo) |

## 3. Criteri di accettazione

| criterio | soglia | esito |
|---|---|---|
| difetti reali (arbitro) | 0 su 500 pagine | **0** ✓ |
| invarianti strutturali I1/I3 | 0 violazioni | **0** ✓ |
| ordine stringente | 0 | **0** ✓ |
| `flow` | ≥ 0.95, nessun intreccio | **1.0000** ✓ |
| FP/FN del verdetto automatico | 0 / 0 | **0 / 0** ✓ |
| figure mancanti | 0 | **0** ✓ |
| suite | verde | **470 OK** ✓ |

## 4. VERDETTO

**Il motore IR soddisfa i criteri su 500 pagine nuove, ora inclusa la classe dei
box a tutta larghezza con sotto-colonne (che il verdetto automatico non
catturava).** Il metro non condivide più i buchi del motore: gli invarianti sono
un segnale indipendente.

## 5. Residui e prossimi passi

- **Non ancora formalizzati**: I2 (adiacenza figura↔didascalia), I4 (no
  duplicazione). Il fix della banda finale copre il caso osservato.
- **Fase 2 del piano**: zona indipendente (pdf2zh v2) per box senza `fill`,
  sidebar e combinazioni non coperte → la divergenza col motore è un rilevatore.
- **Fase 3**: VLM nel loop di sviluppo + corpus golden (ogni conferma → test +
  regola deterministica).
- **Fase 4**: editor zone live (human-in-the-loop).
- **Merge**: resta all'utente (arbitro), dopo verifica manuale. Commit motore/app
  candidati: `5bdff6f` (+ `319f82f`, `05d0fce`, `9b5a8b6`, `b1ea0cc`).
