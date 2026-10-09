# Test E2E a campione (60 pagine) — report PARZIALE e verdetto

- **Data**: 2026-10-03 · **branch**: `main` (dopo merge `e1f09de`)
- **Harness**: `tools/e2e_sample.py` → `tools/e2e.py --via-app --mode advisor`
- **Modello visivo**: OpenRouter `meta/muse-spark-1.3-contributor`
- **Stato**: **interrotto su richiesta** → dati parziali su **53/60 pagine**
  (corpus1 20/20, corpus2 20/20, corpus3 13/20; 24 PDF completati su ~34)

## 1. Campione (seed 20261005)
- **corpus1** (20, 8 libri): ce24×5, ha22×6, pa19×3, co23×2, cu25/pa23/su18/su19×1
- **corpus2** (20, 7 libri): fe22×8, ox2×4, co26/mo21/ox16×2, al17/na25×1
- **corpus3** (20, ~18 paper): arxiv/biorxiv/plos/zenodo

## 2. Timing (parziale)
| voce | valore |
|---|---|
| advisor | 53 chiamate · **2225 s** (≈42 s/chiamata) |
| estrazione (pipeline app) | 119 s (≈2.2 s/pagina) |
| pagine | 53 |

→ l'advisor resta **~95%** del tempo; la pipeline deterministica è ~2 s/pagina.

## 3. Esito complessivo (53 pagine)
- **Pagine senza difetti reali: 22/53 (41.5%)**
- **Pagine con ≥1 difetto reale: 31/53 (58.5%)**
- Difetti reali: **53** · marginalia (escluse): **48**
- Engine: **IR 48**, fallback `current` **5**

| corpus | pagine | engine | text ok | fig ok | tbl ok | flag auto | difetti reali |
|---|---|---|---|---|---|---|---|
| corpus1 | 20 | ir=18, current=2 | 18/20 | 20/20 | 18/20 | 4 | 19 |
| corpus2 | 20 | ir=18, current=2 | 18/20 | 19/20 | 17/20 | 6 | 27 |
| corpus3 | 13 | ir=12, current=1 | 13/13 | 13/13 | 12/13 | 1 | 7 |

## 4. Difetti per tipo (reali)
| tipo | n | famiglia |
|---|---|---|
| `text_order` | 11 | **ordine** |
| `text_missing` | 11 | **completezza** |
| `table_content` | 7 | **completezza** |
| `other` | 6 | vari (refusi, item vuoti, duplicazioni) |
| `table_structure` | 4 | tabelle |
| `figure_missing` | 4 | figure |
| `figure_order` | 3 | ordine |
| `header_content` | 2 | completezza |
| `ref_order` | 2 | ordine |
| `figure_duplicate` | 1 | figure |
| `figure_text_bleed` | 1 | figure |
| `figure_caption` | 1 | figure |

### Cluster dominanti
1. **Ordine di lettura = 16** (`text_order` 11 + `ref_order` 2 + `figure_order` 3).
   Esempi: `pa19 p1025` (fusione tra colonne), `fe22 p894/p1101/p1806/p2171/p2410`
   (blocchi/tabelle inseriti nel mezzo di liste), `co23 p612` (BOX 2 fuori posto),
   `pa23 p54` (titolo tabella tra figura e didascalia), `su19 p2210` (riferimenti
   interlacciati), `fe22 p1598/p1806` (figure fuori posto).
2. **Completezza = 24** (`text_missing` 11 + `table_content` 7 + `figure_missing` 4
   + `header_content` 2).
   - **indici** → fallback `current` perde voci/header (`ha22 p4077/p4102`,
     `al17 p873`, `na25 p859`);
   - **note a piè di pagina** omesse (`fe22 p894`, `co26 p1575`);
   - **tabelle** con celle perse (`cu25 p1362` 0.52, `ha22 p1376` 0.53,
     `co26 p1575` 0.52, `fe22 p1101` 0.49, `ox16 p506` **0.14**, `arxiv_2606 p15` 0.56);
   - **figure** (ritratti nei box) non rilevate (`pa19 p366/p1025/p1224`,
     `ox2 p647` "attese 2, nessuna embedded");
   - **titoli di riquadro** mancanti (`co23 p612`, `co26 p1672`).
3. **Tabelle struttura = 4** (colonne/righe fuse: `cu25 p1362`, `fe22 p1101`,
   `arxiv_2609.37195 p16`).
4. **`other` = 6**: item di elenco **vuoti spuri** `-` (`ox2 p647/p822/p828`),
   duplicazione (`pa19 p1025`), refusi (`pa23 p54`, `arxiv`).

## 5. Cosa ci dice (onesto)
- **L'harness funziona**: difetti categorizzati, per corpus, con timing e
  attribuzione engine.
- **I fix mirati (figure/tabelle/equazioni) NON generalizzano**: erano validati su
  pagine specifiche; su un campione **casuale** la qualità complessiva resta
  **bassa** (solo ~42% pagine pulite).
- **Il gate automatico vede poco**: 11 pagine flaggate vs **31** con difetti reali
  → l'advisor resta indispensabile.
- **Differenze per corpus**: `corpus2`/`fe22` è il più critico (box, liste,
  tabelle, colonne); `corpus3` (arXiv) ha meno difetti ma tabelle/equazioni.

## 6. Verdetto
**Non ancora pronto come default "silenzioso".** Su un campione casuale, ~6
pagine su 10 presentano almeno un difetto reale; i due cluster dominanti sono
**ordine di lettura** (16) e **completezza** (24). I fix già fatti chiudono
problemi **specifici** ma non le classi generali.

**Priorità per il prossimo ciclo** (per impatto):
1. **Ordine di lettura** tra blocchi/colonne/liste/riferimenti (16 difetti).
2. **Contenuto delle tabelle** complesse (multi-colonna, celle fuse) (7+4).
3. **Indici** nel fallback `current` (4).
4. **Rilevazione figure** (ritratti/box) (4) e **titoli di riquadro** (2).
5. **Cosmetica**: item di elenco vuoti spuri (3).

## 7. Note
- Report parziale: `corpus3` fermo a 13/20 (interrotto).
- Artefatti: `/tmp/opencode/e2e60/` (`summary_all.md`, `defects_all.jsonl`,
  `verdict.json`, `sample.json`, `<corpus>/<pdf>/report.jsonl`).
