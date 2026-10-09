# Fase 2 — Riferimento indipendente (DocLayout-YOLO / ONNX) — 2026-10-10

Riferimenti: piano `piano-implementazione-rilevamento-2026-10-09` (§Fase 2),
`STUDIO-INVARIANTI-2026-10-09.md`, `STUDIO-E2E-VERDETTO-2026-10-09.md`.

Obiettivo della Fase 2: affiancare al motore un **secondo modello di layout**
(diverso per architettura e dati di training) e misurare **accordo/divergenza**;
la divergenza è un rilevatore indipendente ("sbaglia diversamente"). Domanda
guida 2.1: *il box a tutta larghezza ("Key Points") viene segmentato come zona
unica?*

## 1. Scelta del riferimento

| | scelto |
|---|---|
| modello | **DocLayout-YOLO** (YOLOv10m addestrato su DocStructBench), il modulo di layout di **pdf2zh v2** / PDF-Extract-Kit |
| runtime | **ONNX Runtime** (CPU) |
| artefatto | `doclayout_yolo_docstructbench_imgsz1024.onnx` (72 MB, sha256 `fece9af0…`) |
| fonte | `huggingface.co/wybxc/DocLayout-YOLO-DocStructBench-onnx` (conversione di `juliozhao/DocLayout-YOLO-DocStructBench`) |
| input | immagine pagina in letterbox 1024×1024, RGB, /255 |
| output | end2end `(1, 300, 6)` = `(x0,y0,x1,y1,conf,cls)`, **già NMS** |
| classi | `title, plain text, abandon, figure, figure_caption, table, table_caption, table_footnote, isolate_formula, formula_caption` |

Perché **non** è ridondante col motore: il motore attivo usa il GNN interno di
PyMuPDF (`pymupdf.layout`, `BoxRFDGNN`) + euristiche proprie
(`_column_splits_robust`). DocLayout-YOLO è un **YOLO visivo** con un dataset
diverso → modelli indipendenti.

Implementazione (nessuna modifica al motore):

- `tools/independent_zones.py` — oracolo: rende la pagina, letterbox, inferenza
  ONNX, decodifica e rimappa i box in **punti PDF**; helper `is_full_width` /
  `contains` / `iou`; CLI con `--overlay`.
- `tools/measure_independent.py` — misura accordo/divergenza su un campione
  (run E2E e/o le 54 pagine `su19` "Key Points").
- `tools/measure_classification.py` — misura sistematica delle
  **divergenze di classificazione** motore↔oracolo (§3.6), con cache.
- `tests/test_independent_zones.py` — test dei soli helper (CI-safe, nessun
  modello).

### Determinismo
Due inferenze consecutive sulla stessa pagina (`su19 p383`) danno **zone
identiche** (`run1 == run2`). L'oracolo è deterministico, ma resta **dev-only**.

## 2. Dipendenza, licenza, offline (vincoli 2.3)

| voce | esito |
|---|---|
| **dipendenza Python** | **nessuna nuova dichiarata**: `onnxruntime` + `numpy` sono già presenti come dipendenze transitive di `pymupdf_layout` (← `pymupdf4llm`) |
| **runtime** | CPU-only, `onnxruntime`, nessun `torch`/`cv2`/`doclayout_yolo` |
| **offline** | sì: si passa il file ONNX locale (`--model`); nessuna rete a runtime |
| **licenza codice** (`doclayout_yolo`) | **AGPL-3.0** |
| **licenza pesi (metadata ONNX)** | `license: AGPL-3.0 License (https://doclayout_yolo.com/license)` |
| **licenza model card HF** | dichiara `apache-2.0` → **contraddittoria** col metadata dell'artefatto |

> I pesi e il codice DocLayout-YOLO sono **AGPL-3.0**. Una licenza AGPL è
> copyleft: **non** va distribuita col prodotto senza una valutazione legale.
> La model card "apache-2.0" non è supportata dal metadata del file ONNX.

## 3. Misura (2.1 / 2.2)

### 3.1 Classe box a tutta larghezza — `su19` "Key Points" (54 pagine)

Per ogni **box del motore** (`ir_layout._full_width_boxes`): quante zone DocLayout
hanno il centro dentro il box.

| n. zone DocLayout nel box | box |
|---|---|
| 0 | **0** |
| 1 | **0** |
| 2 | 4 |
| 3+ | 53 |

- Totale box: **57**. Zone con `IoU ≥ 0.5` col box: **2 / 57**.
- **Conclusione 2.1:** DocLayout **non** segmenta il box come zona unica: lo
  divide sempre in **≥2** blocchi `plain text` (tipicamente uno per colonna
  interna), **ignorando il contenitore** (il rettangolo pieno). DocLayout **non**
  modella le "bande/contenitori" → **non è un oracolo per la classe I1**.

Esempio (`su19 p383`): box `(-21.4, 50.7, 557.5, 198.2)` → DocLayout emette due
`plain text` (colonna sx `53.7–289.6×65.9–193.2`, colonna dx
`310.9–547.2×67.0–181.1`), nessuna zona che copra il box.

### 3.2 Zone full-width non coperte (candidati "box senza fill"/sidebar)

Nel campione su19 (54 pagine): 10 zone full-width DocLayout, **7 non coperte** da
un box del motore. Analisi: sono per lo più `figure_caption` a tutta larghezza
(in basso) e una `figure` a tutta larghezza (`p2055`); su `p2073` tre blocchi
`plain text` a tutta larghezza appartengono a **una tabella** (`Table 47-1`), già
gestita dalla logica tabelle del motore. **Nessuno** è un box-contenitore reale
non rilevato.

### 3.3 Figure/didascalie (oracolo per I2)

Pagine su19 con figure (elemento `picture` del motore): **22**; di queste **22/22**
hanno in DocLayout una `figure_caption`. DocLayout conferma l'adiacenza
figura↔didascalia (utile come oracolo I2, ma coerente col motore).

### 3.4 Divergenza **utile** — classificazione (figure vs tabelle)

Il valore dell'oracolo emerge sulla **classificazione**, non sui contenitori:

| caso | motore | DocLayout | esito |
|---|---|---|---|
| `ha22 p3355` (figura multi-pannello) | `table` `(48,44,394,295)` | **`figure`** `(45.6,26.4,396.2,295.8)` + `figure_caption` | DocLayout **diverge e ha ragione** (IoU 0.92) |
| `arxiv_2609.38133 p16` (pagina formula) | `table×2`, `caption×2`, `text×6`, `formula×3` | `table×2`, `table_caption×3`, **`plain text×8`**, `isolate_formula×4` | DocLayout rileva **più prosa** → candidato "prosa persa" |
| `arxiv_2609.30742 p19` | `text×8`, `formula×6` | `plain text×9`, **`isolate_formula×6`** | concordanza su conteggio formule |
| `arxiv_2609.37412 p39` | `text×6`, `formula×1` | `plain text×6`, `isolate_formula×1` | concordanza |

La **divergenza di classificazione** (`table`↔`figure`, conteggio prosa) è il
segnale realmente utile: intercetta classi residue (figura multi-pannello;
prosa su pagina formula) che il verdetto automatico non vede.

### 3.5 Campione casuale (run L6, held-out)

60 pagine di RUN L6 (250 casuali, seed `20261017`), **38 box** del motore:

| metrica | valore |
|---|---|
| zone DocLayout con centro nel box | 0=**5**, 1=24, 2=4, 3+=5 |
| box con una zone `IoU ≥ 0.5` | 27 / 38 |
| zone full-width DocLayout | 46 |
| └ non coperte da un box motore | 26 (`figure`×8, `figure_caption`×4, `plain text`×11, `table_footnote`×2, `abandon`×1) |
| pagine con figure | 23 |
| └ con `figure_caption` (DocLayout) | 18 (78%; su `su19` 22/22) |

Lettura dei casi:

- **I 5 box "0 zone"** non sono difetti: sono **sotto-rettangoli di tabelle**.
  Il motore emette più rettangoli sovrapposti per la stessa tabella (bordi e
  shading, es. `co23 p511`: tre box dentro `(48,51…715.6)`); DocLayout vede
  **una** `table` con IoU 0.5–0.9 che, per il sotto-rettangolo specifico, non ha
  il centro dentro. Nessuna banda/contenitore mancante.
- **Zone full-width non coperte**: le `figure`/`figure_caption` (12) e le
  `plain text` (11) full-width **non** sono rettangoli pieni → atteso che
  `_full_width_boxes` non le veda; appartengono a figure e tabelle già gestite.
- **Figure/didascalie**: 18/23 (le figure senza didascalia o con didascalia fuori
  dai limiti non ne hanno una; nessun difetto).

**Conclusione:** su pagine casuali DocLayout **non** aggiunge informazione per la
classe box (accordo regionale su tabelle/figure, divergenze geometriche attese).
Il valore resta la **divergenza di classificazione** (§3.4).

### 3.6 Divergenza di classificazione — misura sistematica (124 pagine)

Per trasformare l'aneddoto di §3.4 in un **tasso**, `tools/measure_classification.py`
confronta gli elementi speciali del motore (`table`/`picture`/`formula`) con le
zone DocLayout sulla **stessa regione** (IoU ≥ 0.3) su 124 pagine held-out
(60 di L6 + 60 di M4 + 4 residue note).

| classe motore | tot | ok | conflitto | non-match |
|---|---|---|---|---|
| `table` | 43 | 32 | 7 | 4 |
| `picture` | 52 | 48 | 2 | 2 |
| `formula` | 10 | 9 | 1 | 0 |

Zone speciali dell'oracolo **senza corrispettivo** nel motore: `table` 5,
`figure` 0, `formula` 2. Pagine con gap di prosa ≥ 3: 5.

**Accordo grezzo: 89/105 = 85%.** Le divergenze (10 conflitti) sono state
**arbitrate** con overlay (zone oracolo + elementi motore):

| page | motore | oracolo | IoU | giudizio |
|---|---|---|---|---|
| `ha22 p3355` | table | figure | 0.92 | **VERO** figura multi-pannello |
| `co23 p743` | table | figure | 0.37 | **VERO** flowchart = figura |
| `co23 p1428` | table | plain text | 0.98 | **VERO** Box = lista, motore sovra-rileva tabella |
| `arxiv_2609.38133 p16` | formula | plain text | 0.39 | **VERO** prosa persa su pagina formula |
| `ce24 p68` | table | figure | 0.93 | ambiguo (modulo ESAS: figura nel libro, tabella accettabile) |
| `ce24 p368` | table | plain text | 0.49 | **FALSO** (Tabella 37-5 reale) |
| `ce24 p747` | table | plain text | 0.44 | **FALSO** (Tabella 64-3 reale) |
| `ce24 p4303` | table | plain text | 0.67 | **FALSO** (Tabella 407-1 reale) |
| `ce24 p2` ×2 | picture | abandon | 0.97 | rumore (logo/decorazione di copertina) |

Zone oracolo non corrisposte (arbitrate): `cu25 p28` tabelle reali che il motore
non emette (**VERO**), `arxiv p16` 2 formule separate dal motore fuso in una
(**VERO**), `co23 p1479` lista scambiata per tabella dall'oracolo (**FALSO**).

**Esito:** su 124 pagine l'oracolo segnala ~10 conflitti; dopo arbitraggio
**≈6 sono reali** e **≈6 sono falsi/rumore** → **precisione ~50%,
richiamo utile ma non esaustivo**. I veri riguardano classi residue note
(figura↔tabella, Box-lista, prosa su pagina formula). **Non è un gate**: è un
**generatore di candidati a bassa precisione** che richiede arbitraggio
(Advisor/umano).

## 4. Decisione (2.3)

**NON adottare** DocLayout-YOLO come dipendenza di **runtime** né come sorgente
di zone per l'emissione:

1. **Licenza AGPL-3.0**: copyleft; incompatibile con la distribuzione del
   prodotto senza valutazione legale (e il metadata ONNX contraddice la card).
2. **Non modella i contenitori**: 0/57 box "Key Points" come zona unica → non
   può sostituire/estendere il modello a bande (`_full_width_boxes`, I1).
3. **Motore singolo**: aggiungerebbe un secondo modello + asset 72 MB a runtime.

**SÌ adottare** DocLayout-YOLO come **oracolo indipendente di sviluppo**, opt-in
e offline (stessa categoria dell'Advisor/VLM del piano):

- **mai in CI**, **mai nel runtime deterministico** (R12);
- usato per **arbitrare i candidati** e **misurare la divergenza**:
  - classificazione `table`↔`figure` (figura multi-pannello);
  - conteggio prosa/formule (candidati "prosa persa" su pagine formula);
  - conferma figura↔didascalia (I2);
- ogni divergenza **confermata** → Fase 3 (golden test + regola deterministica
  nel motore). L'oracolo **non** entra mai nel prodotto.
- **Natura del segnale** (§3.6): generatore di **candidati a bassa precisione
  (~50%)**, orientato al richiamo; va sempre arbitrato, **non** è un gate.

## 5. Cosa resta / passaggio alla Fase 3

- **Fase 3**: usare l'oracolo per generare **candidati** (divergenze §3.4/§3.6),
  farli arbitrare (Advisor/umano), e per ogni conferma creare un **golden test**
  (`tests/data/golden/`) + **regola deterministica**.
- **I2** e **I4** restano invarianti da formalizzare; DocLayout fornisce la
  materia prima per I2 (figure/caption).
- **Merge**: invariato, resta all'utente (arbitro).

## 6. Comandi

```bash
# zone di una pagina (+ overlay con i box del motore in magenta)
.venv/bin/python tools/independent_zones.py --pdf corpus1/su19.pdf --page 383 \
    --model /path/doclayout_yolo_docstructbench_imgsz1024.onnx \
    --overlay /tmp/su19_p383.png

# misura su tutte le pagine su19 "Key Points"
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_independent.py \
    --model /path/doclayout_yolo_docstructbench_imgsz1024.onnx --su19-kp \
    --out /tmp/measure_su19kp.json

# misura su un campione di run (read-only)
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_independent.py \
    --model /path/doclayout_yolo_docstructbench_imgsz1024.onnx \
    --run ~/.local/share/opencode/e2e250l6 --limit 60 --out /tmp/measure_l6.json

# divergenze di classificazione motore<->oracolo (candidati Fase 3)
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/measure_classification.py \
    --model /path/doclayout_yolo_docstructbench_imgsz1024.onnx \
    --run ~/.local/share/opencode/e2e250l6 \
    --run ~/.local/share/opencode/e2e250m4 --limit 60 --known \
    --out /tmp/measure_class.json
```
