# Studio E2E + advisor — 2 libri: timing e difetti con attribuzione

- **Data**: 2026-10-03
- **Branch**: `harness-e2e`
- **Harness**: `tools/e2e.py --via-app --mode advisor --pipelines ir`
  (`--advisor-retries 5 --advisor-delay 3`)
- **Modello visivo**: OpenRouter `meta/muse-spark-1.3-contributor` (remoto)
- **Attribuzione engine**: dal commit `8bba4b8` il report registra quale pipeline
  ha prodotto il testo (`ir` | `current` = fallback del gate).
- **Campioni** (10 pagine casuali ciascuno):
  - `ha22.pdf` (4132 pag.) — seed `20261003`
    → `40,230,416,875,1174,2362,2442,2775,3172,4077`
  - `ce24.pdf` (4451 pag.) — seed `20261004`
    → `443,1038,1186,1430,1562,2248,2480,3218,3899,4285`

## 1. Timing di riferimento

| libro | pagine | totale | estrazione (somma) | advisor (somma) | advisor/chiamata |
|---|---|---|---|---|---|
| **ha22** | 10 | **483.5 s** | 41.9 s (4.2 s/pag) | 362.6 s | 36.3 s |
| **ce24** | 10 | **332.2 s** | 38.3 s (3.8 s/pag) | 221.1 s | 22.1 s |

- La pipeline deterministica è **~3.8–4.2 s/pagina** (via-app, inclusi gate +
  figure + cache + attribuzione).
- L'**advisor domina (~70–75%)**: 22–36 s/chiamata (varianza alta, dipende dal
  carico upstream).
- 20 pagine ≈ **13.6 min**; l'advisor va usato su campioni/blocchi, non su libri.

## 2. Difetti con attribuzione engine

### ha22 (seed 20261003)
| pag | engine | auto | VLM text/ord/fig/tbl | difetto |
|---|---|---|---|---|
| 41 | **ir** | – | KO/OK/OK/OK | header di contenuto perso + **falso marcatore figura** |
| 231 | **current** | tables 0.42 | OK/OK/OK/**KO** | struttura Tabella 29-1 (header 1 col, voci compresse) |
| 417 | ir | – | OK | – |
| 876 | **ir** | – | **KO/KO/KO/KO** | **numeri degli assi fusi nel testo**; **doppio marcatore** per 1 figura; "No. AT Risk" non tabella |
| 1175 | **ir** | – | **KO**/OK/OK/**KO** | Tabella 142-2 righe unite |
| 2363 | ir | – | OK | – |
| 2443 | **ir** | – | **KO/KO/KO**/OK | **ordine rotto dalle figure** (8 marcatori), etichette A-D |
| 2776 | ir | – | OK | – |
| 3173 | ir | – | OK | – |
| 4078 | **current** | indice | **KO**/OK/OK/OK | indice: ultima voce troncata |

### ce24 (seed 20261004)
| pag | engine | auto | VLM text/ord/fig/tbl | difetto |
|---|---|---|---|---|
| 444 | ir | – | **KO**/OK/OK/OK | solo header/numero pagina (marginalia) |
| 1039 | **current** | tables 0.54 | **KO/KO**/OK/OK | **equazioni garbled** + ordine (fallback) |
| 1187 | **ir** | – | **KO**/OK/**KO**/OK | **figura duplicata** (2 marcatori per 1) + refuso `pTh` |
| 1431 | ir | – | OK | – |
| 1563 | **ir** | – | **KO/KO**/OK/OK | **riferimento 9 prima di GENERAL REFERENCES**, refs interlacciati |
| 2249 | ir | – | **KO**/OK/OK/OK | solo header/numero pagina (marginalia) |
| 2481 | **ir** | – | OK/OK/OK/**KO** | struttura Tabella 210-8 (colonne interlacciate, righe unite, duplicati) |
| 3219 | ir | – | OK | – |
| 3900 | ir | – | (404) | advisor non disponibile (transitorio) |
| 4286 | ir | – | **KO**/OK/OK/OK | solo header/numero pagina (marginalia) |

## 3. Cosa sta funzionando / cosa no (sintesi)

**Funziona**: testo di corpo e **ordine a colonne** (11/20 pagine pulite);
figure+didascalie su pagine normali; contenuto tabelle (non perso); velocità;
fallback del gate su indici/tabelle.

**Non funziona (per attribuzione)**:
- **IR — figure/ordine** (il problema n.1): numeri di assi fusi nel testo
  (ha22 p876), ordine rotto dalle figure (ha22 p2443), **figura duplicata**
  (ha22 p876, ce24 p1187), ordine riferimenti (ce24 p1563).
- **IR — struttura tabelle**: ha22 p1175, ce24 p2481.
- **IR — header di contenuto** perso + **falso positivo** di figura (ha22 p41).
- **current (fallback) — struttura tabelle** (ha22 p231), **indice troncato**
  (ha22 p4078), **equazioni** (ce24 p1039).

**Sistematico (non-difetto)**: testate correnti e numero di pagina **sempre**
omessi → il VLM li conta come `text_ok: False` e **gonfia i difetti** (5 delle
10 pagine ce24!). Da istruire nel prompt.

## 4. Affidabilità dell'advisor
- **HTTP 429** (rate limit upstream): senza retry un run perdeva **6/10**
  verdetti. Risolto con **retry/backoff** + delay (`7cdfa97`): secondo run ha22
  **10/10**.
- **HTTP 404 `model_not_found`** transitorio (ce24 p3900): ora incluso nei
  retry (`404`).

## 5. Prossimi passi
1. **Prompt advisor**: escludere dai difetti la **marginalia** (numero pagina,
   testate correnti, fasce "PART n") → riduce il rumore e isola i difetti veri.
2. **Motore, in ordine di priorità**:
   (a) **figure/ordine + duplicati** (ha22 p876/p2443, ce24 p1187/p1563);
   (b) **struttura tabelle** (ha22 p1175, ce24 p2481, fallback p231/p1039);
   (c) **indici** (ha22 p4078); (d) **header di contenuto** (ha22 p41).
3. Ripetere il campione dopo i fix per misurare il delta (baseline in
   `tests/data/e2e_baseline.json`).
