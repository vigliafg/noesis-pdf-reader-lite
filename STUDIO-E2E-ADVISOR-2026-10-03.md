# Studio E2E + advisor visivo — timing e difetti

- **Data**: 2026-10-03
- **Branch**: `harness-e2e`
- **Harness**: `tools/e2e.py --via-app --mode advisor --pipelines ir`
- **Modello visivo**: OpenRouter `meta/muse-spark-1.3-contributor` (remoto)
- **PDF**: `corpus1/ha22.pdf` (4132 pagine)
- **Campione**: 10 pagine casuali, seed `20261003`
  (0-based `40,230,416,875,1174,2362,2442,2775,3172,4077`)
- **Artefatti**: `/tmp/opencode/e2e_ha22_10b` (`summary.md`, `report.jsonl`,
  `defects.jsonl`, `meta.json`, `review/*_advisor.json`)

Flusso misurato: pagina → **UI reale** (`MainWindow` + `ExtractThread`) →
attesa del markdown → estrazione della finestra **Originale** → **VLM**
(immagine pagina + markdown, base64 rimosso).

## 1. Timing di riferimento

| fase | totale | media/pagina |
|---|---|---|
| **E2E completo** | **448.1 s** | 44.8 s |
| Estrazione (pipeline app, somma `elapsed`) | 38.0 s | **3.80 s** |
| Advisor VLM (10 chiamate) | 380.9 s | **38.1 s** |
| Overhead harness (apertura app, content map, render pagina) | ~29 s | ~2.9 s |

- L'**advisor domina (~85%)**: la pipeline deterministica è ~3.8 s/pagina, il
  VLM ~38 s/chiamata (range osservato **15–59 s**).
- Tempi estrazione alti su: p230 (11.5 s, tabella), p4077 (5.6 s, indice →
  fallback), p875 (5.0 s, grafico), p2442 (3.7 s, 8 figure).
- Con **20 pagine** il costo VLM stimato è ~13 min; con 150 ~1h40m → l'advisor
  va usato su campioni/blocchi, non su interi libri.

## 2. Difetti intercettati dal modello visivo

Il VLM ha giudicato **tutte le 10 pagine**; il gate automatico ne aveva
flaggate solo **2**. Difetti reali su **6/10** pagine (25 voci in
`defects.jsonl`, di cui 2 automatiche).

| pag. | esito VLM | difetto |
|---|---|---|
| **876** | text/order/figures/tables ✗ | **Il più grave**: numeri degli assi del grafico fusi nelle frasi (`100`, `trisomy 80 12`, `ther60 apies`, `lymphadenop20 athy`…); tabella "No. AT Risk" non estratta; frammenti anticipati; **doppio** marcatore figura per un'unica Figura 107-2 |
| **2443** | text/order/figures ✗ | corpo presente ma **spezzato dalle figure**; ordine errato (8 marcatori fuori posto); etichette pannello C-D mancanti/duplicate |
| **231** | tables ✗ | Tabella 29-1 presente ma **markdown invalido** (header 1 colonna, righe 2 colonne, voci "Less Common" compresse, apici attaccati, refuso) |
| **1175** | tables ✗ | Tabella 142-2 **righe unite** (2 coppie fuse), refuso `eventdirected` |
| **4078** | text ✗ | pagina **indice**: ultima voce **troncata** (numeri di pagina mancanti) |
| **41** | text/figures ✗ | testata "Related Harrison's Resources" omessa; **falso marcatore figura** su elemento decorativo |
| 417 / 2363 / 2776 / 3173 | ✓ (2776: righe tabella unite, nota) | nessun difetto bloccante |

**Pattern ricorrenti (non-difetti, attesi):** numero di pagina e fascia
laterale "PART n" **sempre** omessi (marginalia, non corpo).

## 3. Valore del test advisor su *tutte* le pagine
I due difetti peggiori (**p876**, **p2443**) **non** erano flaggati dal gate
automatico: recall testo 0.981/0.979 e order 1.00, ma figure/ordine corrotti.
È esattamente la classe di errori che le metriche automatiche **non** vedono e
che il VLM intercetta → conferma la scelta di giudicare tutte le pagine.

## 4. Correzione emersa durante lo studio
Il primo run inviava al VLM `md[:20000]`: sulle pagine con figure base64
all'inizio il testo veniva **troncato** e il modello vedeva solo l'immagine,
giudicando per errore che tutto mancasse (3 falsi negativi: p416/p2362/p2442).
**Fix** (commit `2aef5dd`): il base64 è sostituito da marcatori `[FIGURA: …]`
prima dell'invio → verdetti corretti.

## 5. Prossimi passi suggeriti
- **P1**: nuovo campione `corpus150_b` (seed diverso) ed esecuzione a blocchi
  con `--mode advisor`; raccogliere i difetti ricorrenti per tipo.
- Priorità emerse: (a) **figure/ordine** su pagine grafico (p876, p2443);
  (b) **struttura tabelle** (p231, p1175, p2776); (c) **indici** (p4078).
- Esporre chiave/modello dell'advisor in **UI** (P3).
