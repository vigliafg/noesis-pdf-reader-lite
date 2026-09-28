# Piano — Motori di estrazione alternativi (Xberg, MinerU, Marker) e uso mirato di un LLM vision

Redatto il **2026-09-28**. Documento di `noesis-pdf-reader-lite`, salvato nella
radice del repo (`PIANO-motori-estrazione-vlm.md`).
Contesto di riferimento: repo `noesis-pdf-reader` (gemello, stessa architettura di
`layout_engine.py`).

Stato: **discussione / da decidere**. Nessuna implementazione avviata.
Questa bozza vive sul branch `experimental`; per abbandonarla e tornare a un
repository pulito vedi `experimental/README.md`
(`python experimental/abort_experiment.py --yes`).

---

## 0. Scopo del piano

1. Valutare l'integrazione dei tre motori di estrazione alternativi **Xberg**,
   **MinerU** e **Marker**.
2. Documentare l'aumento di footprint atteso (RAM e disco) **con il vincolo che i
   PDF in ingresso sono nativamente digitali** (text layer affidabile, nessuna
   pagina scansionata).
3. Definire **quando** ha senso aggiungere un LLM vision (VLM) e con quale pattern.
4. Valutare la **sinergia PyMuPDF4LLM ↔ Xberg** (output che si perfezionano a vicenda).
5. Fissare una sequenza di fasi e le domande aperte.

---

## 1. Architettura attuale (punti di aggancio)

Nel repo `noesis-pdf-reader`:

- `main.py` — `MainWindow.BACKENDS` (lista di stringhe) e `_extract_text(page_num)`
  che fa dispatch con `if/elif` sul nome del backend; ogni backend è un metodo
  `_extract_*`.
- Import pesanti protetti da `try/except` (Docling, pdf_oxide, pymupdf, QtPdf):
  un backend non installato semplicemente non è disponibile.
- Docling ha un trattamento speciale: `DoclingExtractThread` (worker in background
  con coda prioritaria + prefetch + cache per pagina e `generation` per scartare
  risultati stantii).
- `layout_engine.py` — engine adattativo **Profilo → Piano → Pipeline**, puro
  (nessun PyQt, solo pymupdf + tipi), testabile:
  - `LayoutProfile` (campi: `columns`, `splits`, `columns_overlap`, `has_tables`,
    `full_width_tables`, `has_small_text`, `has_references`, `has_index`,
    `body_blocks`).
  - `profile_page(page)` misura la pagina **una volta**.
  - `Fix(id, description, order, when(profile, backend), apply(md, page, profile))`
    e `FIX_REGISTRY`.
  - `plan_fixes(profile, backend, mode="auto", overrides=...)` con override utente
    da `fix_rules.json` (`disable` + `rules` con `when` limitato a
    `columns`/`backend`/`has_tables`/`has_index` e operatori `eq/gte/lte`).
  - `apply_plan(md, page, profile, plan)`.
- In `main.py` `_apply_fix` instrada le voci del combo `FIXES`; la voce
  `"Engine adattativo"` chiama `_apply_engine` (profilo→piano→pipeline).
- PyMuPDF è **sempre caricato** (rendering, estrazione immagini, `layout_engine`),
  quindi il suo costo marginale è ~0.

Implicazione: il punto naturale per aggiungere motori e fix è il registro di
`layout_engine.py`, mentre i motori lenti vanno eseguiti con il pattern worker di
Docling. I backend vanno aggiunti con **file requirements separati** (come già
`requirements-docling.txt` / `requirements-cuda.txt`), mai nel tier leggero.

Nota sul repo `noesis-pdf-reader-lite`: ha già `layout_engine.py`, `main.py`,
`i18n.py`, `installer.nsi`, `tests/`. Prima di implementare va verificato che la
copia locale di `layout_engine.py`/`main.py` sia allineata a quella del gemello.

---

## 2. Vincolo di progetto: PDF nativamente digitali

Assunzione: i PDF hanno text layer affidabile, nessuna scansione. Conseguenze:

- **OCR e VLM full-page sono inutili** in tutti e tre i motori.
- Spariscono i pesi più grandi e **ogni requisito GPU**.
- Restano da pagare solo: (a) estrazione testo, (b) layout/reading order,
  (c) struttura tabelle. I punti (b) e (c) sono esattamente ciò che il progetto
  già affronta con `layout_engine.py` sul text layer PyMuPDF.

Il tier CUDA/"full" (~6 GB) **non ha più ragione di esistere** per questo caso d'uso.

---

## 3. I tre motori: cosa sono

| Motore | Cos'è | Runtime | Licenza |
|---|---|---|---|
| **Xberg** | Successore rinominato di **Kreuzberg** (v1.2.9). Core **Rust**, `pip install xberg`, singolo `.so` con ONNX Runtime CPU incluso. Modelli ML opzionali in `~/.cache/xberg`. | in-process (async), CPU | MIT (codice e modelli) |
| **MinerU 4.x** | Small models ONNX/Torch + **VLM MinerU2.5 1.2B**. Modelli in `~/.mineru/models`. Tier `flash` (nativo) / `basic` (small) / `standard`+ (VLM). | ONNX CPU o llama.cpp/vLLM | Apache-2.0 **con condizioni aggiuntive** |
| **Marker 2.0** | Pipeline su **Surya 2** (VLM ~650M) + torch; VLM in un **server di inferenza locale** (llama.cpp su CPU, vLLM su GPU). Modelli in cache HF. | torch + server VLM | Codice Apache-2.0, **pesi OpenRAIL-M** (limite commerciale 5M$) |

Vincolo pratico: **Xberg è l'unico in-process**. Marker e MinerU avviano (o possono
avviare) un server di inferenza → vanno in worker/subprocess, non nel processo GUI.

---

## 4. Footprint — baseline misurata

Dal repo `noesis-pdf-reader` (`NEXT_STEPS-installazione.md`, misure già fatte):

| Tier | Contenuto | Bundle |
|---|---|---|
| light | backend veloci, nessun torch | **359 MB** |
| medium | + Docling + torch CPU | **~1.6 GB** |
| full | + torch CUDA | **~6 GB** |

Marker/Docling condividono torch: se il tier `medium` esiste già, il costo
incrementale di un motore torch-based è molto minore (torch è già pagato).

---

## 5. Footprint con vincolo born-digital

Numeri da PyPI e HuggingFace, raccolti il 2026-09-28. "Misurato" = verificato
direttamente; "stima" = derivato da wheel/dipendenze.

### 5.1 Xberg

| Voce | Misura |
|---|---|
| Codice: wheel Linux x86_64 59 MB → **installato 155 MB** (`_xberg.abi3.so` 94 MB, ONNX Runtime incluso, **0 dipendenze Python**) — *misurato* | +155 MB, ~0.2–0.4 GB RAM |
| Layout opzionale: TATR 29 MB / PP-DocLayoutV3 125.6 MB / RT-DETR 161.3 MB | +29–161 MB, +0.3–0.5 GB RAM |
| Tabelle: SLANet+ 7.4 MB (o SLANeXt 348 MB / RT-DETR-L cell 123 MB) | +7–348 MB |
| OCR PaddleOCR det 84 MB + rec per-script 7.5–16 MB + classifier 6.5 MB | **non necessario** |
| PaddleOCR-VL 1.6 (candele VLM): 1.83 GB | **non necessario** |

**Totale born-digital: +0.15 GB (senza layout) / +0.19–0.32 GB (con layout);
RAM ~0.2–0.8 GB.** Il layout è opzionale: il riordino colonne si può fare con
l'euristica PyMuPDF già presente (`layout_engine` / `_column_aware_markdown`).
Attenzione: le feature ONNX richiedono CPU **AVX2**.

### 5.2 MinerU 4.0.8

| Componente | Born-digital |
|---|---|
| Package base + deps: onnxruntime 22 MB, opencv 68 MB, gradio 30 MB, `mineru-llama-cpp` 21.5 MB, transformers, fastapi… (senza torch) — *stima* | **~1–1.5 GB** |
| Tier `flash` (parsing nativo) | **0 modelli** |
| Tier `basic`: bundle `MinerU-4_models_onnx` **818.5 MB** = layout 204.1 + formula 563.9 + OCR 20.2 + tabelle ~22 | serve layout+tabelle (~226 MB); OCR e formula non necessari, ma il bundle è monolitico |
| VLM MinerU2.5-Pro-2605-1.2B bf16 2.22 GB / GGUF Q8 1.18 GB | **non necessario** |

| Config | Disco | RAM |
|---|---|---|
| flash | +1–1.5 GB | ~0.5–1 GB |
| basic | +1.8–2.3 GB | ~2–3 GB (il modello formula da 564 MB è il peso in RAM; senza formula ~1–1.5 GB) |

Docs MinerU: minimo 16 GB RAM, 32 consigliati, GPU ≥8 GB VRAM solo come punto di
partenza; PyPI legacy dichiara 20 GB+ di disco.

### 5.3 Marker 2.0.0

| Componente | Born-digital |
|---|---|
| Deps CPU-only: torch≥2.7 + torchvision, surya-ocr, transformers, opencv-headless 54 MB, scikit-learn 9 MB, pdftext — *stima* | **~1.5–2.5 GB** |
| Modelli `--disable_ocr`: pdftext + rf-detr layout 20M ~80 MB (eventuale ocr-error 262 MB) | **+0.08–0.35 GB** |
| Surya 2 VLM bf16 1.31 GB / GGUF 1.43 GB | **non necessario** |
| Modelli piccoli: line detector 73 MB, inline math 73 MB | solo per OCR/math |

| Config | Disco | RAM | GPU |
|---|---|---|---|
| fast `--disable_ocr` | +1.6–2.9 GB | ~1–2 GB | no |
| Marker sopra tier Docling esistente (torch condiviso) | **+0.6–1.3 GB** | ~1–2 GB | no |

In `--disable_ocr` Marker **non avvia nemmeno il server di inferenza**.

### 5.4 Riepilogo born-digital

| Motore (config minima utile) | Modelli | Disco aggiunto | RAM | GPU |
|---|---|---|---|---|
| **Xberg** testo nativo | 0 | **+0.15 GB** | 0.2–0.4 GB | no |
| Xberg + layout | 29–161 MB | +0.19–0.32 GB | 0.4–0.8 GB | no |
| **MinerU flash** | 0 | +1–1.5 GB | 0.5–1 GB | no |
| **MinerU basic** | 226–818 MB | +1.8–2.3 GB | 2–3 GB | no |
| **Marker fast `--disable_ocr`** | 80–350 MB | +1.6–2.9 GB | 1–2 GB | no |
| Marker su tier Docling esistente | 80–350 MB | +0.6–1.3 GB | 1–2 GB | no |

Conclusione: **Xberg** è il candidato naturale per il tier light (+155 MB,
in-process, nessun torch); **Marker no-OCR** ha senso solo se si tiene già il tier
`medium` (torch condiviso); **MinerU** è difficile da giustificare (+1.8–2.3 GB per
usare solo layout/tabelle, mentre il suo pezzo forte VLM è inutile su born-digital).

---

## 6. LLM vision: quando integrarlo

Principio: con born-digital il text layer resta la fonte di verità (fedeltà,
citazioni, bbox). Il VLM è un **riparatore mirato**, mai il default. Non deve
entrare in `Auto` senza opt-in: `apply_plan` oggi è puro e testabile.

Casi d'uso, in ordine di ROI:

| Caso | Perché il text layer fallisce | Segnale di attivazione |
|---|---|---|
| **Tabelle difficili** | tabelle senza righe, celle unite, header multipli, annidate/multi-pagina | `has_tables` vero ma griglia `find_tables` malformata/celle vuote |
| **Formule / math** | MathType/Type3/private-use → glifi illeggibili anche su digitale | font matematici, densità di non-ASCII/garbage in un blocco |
| **CMap/encoding rotto** | pagina corretta ma `get_text()` dà CID, `\ufffd`, vocali mancanti | quality score basso (replacement char, densità lettere, hit-rate dizionario) |
| **Reading order ambiguo** | multi-colonna senza split netto, colonne sovrapposte, sidebar | `columns>=2` con `columns_overlap` incoerente / split contraddittori |
| **Struttura semantica** | gerarchia titoli dedotta solo da `max_size` | heading ambigui, didascalie, note, cross-ref |
| **Figure vettoriali** | diagrammi/chimica/mappe: solo etichette sparse nel text layer | regione con vettoriale e poco testo |
| **Moduli/stampe** | campi, timbri, firme | form fields / regioni grafiche |

**Quando NON usarlo**: prosa pulita mono-colonna, tabelle con righe. Lì il
deterministico è più veloce e non allucina.

Pattern di integrazione:

1. Estendere `LayoutProfile` con `suspect_blocks: tuple[bbox, ...]` (o
   `needs_vlm: bool`), calcolato in `profile_page` → resta puro e testabile.
2. Aggiungere `Fix("vlm_repair", …, when=lambda p,b: bool(p.suspect_blocks), apply=…)`
   nel `FIX_REGISTRY`: scheduler e override `fix_rules.json` funzionano già, e il
   predicato `when(profile, backend)` limita l'applicazione.
3. L'`apply` oggi restituisce una stringa intera: per il VLM serve **block-level**.
   Crop con `_region_image` (già presente); reinserimento del frammento con verifica
   contro il text layer (`_reading_normalize`, `_longest_prefix_in`/`_longest_suffix_in`);
   se il VLM "deriva", tenere l'originale.
4. Esecuzione in background con il pattern di `DoclingExtractThread`/`TranslateThread`.
5. **Locale o remoto**: locale (Surya 2 0.65B 1.31 GB / MinerU 1.2B 2.2 GB /
   PaddleOCR-VL 1.83 GB / Qwen-VL GGUF) mantiene la privacy; remoto (Gemini/OpenAI/
   Ollama) ha footprint zero ma manda le pagine fuori. L'app già parla con Google
   Translate, ma un'immagine di pagina è più sensibile di un paragrafo.
6. Uso **offline** (a costo zero nel prodotto): VLM come *oracolo* su un campione
   per scoprire dove sbagliano i motori deterministici e codificare la lezione in
   nuove euristiche di `layout_engine`.

---

## 7. Sinergia PyMuPDF4LLM ↔ Xberg

La sinergia **non** è "uno perfeziona il Markdown dell'altro": entrambi producono
Markdown piatto senza ID né bbox, quindi allineare i paragrafi per fonderli è lo
stesso problema già risolto in parte con `_reading_normalize`/`_longest_*` per
Docling, e su larga scala è fragile. Serve una **rappresentazione strutturata
condivisa**.

Punto di forza: la geometria esiste già. PyMuPDF è sempre caricato e
`_collect_blocks` legge `page.get_text("dict")` (span, bold/italic, bbox); Xberg
espone output strutturati (JSON tree, DocTags) con la layout detection attiva.
Prerequisito da verificare empiricamente: che i bbox di Xberg combacino con quelli
PyMuPDF.

Quattro forme, in ordine di ambizione:

- **A. Routing per pagina** (semplice, alto valore). `profile_page` decide: pagina
  pulita mono-colonna → PyMuPDF4LLM; multi-colonna/tabelle/indice/referenze →
  Xberg. Nessuna doppia estrazione.
- **B. Riuso, non rivalità** (sweet spot). Xberg come *backend*, poi sopra gira il
  `layout_engine` esistente (de-sillabazione, riordino colonne, `split_glued`,
  spaziature), perché `_apply_fix` accetta già qualunque backend. Zero lavoro di
  allineamento. Aggiungere il predicato per non rifare un riordino già fatto da
  Xberg (`when(profile, backend)`).
- **C. Consenso/diff a livello di blocco**. Layer di adapter:
  ```python
  @dataclass
  class PageResult:
      blocks: list[dict]   # {bbox, kind, text, md, conf}
      tables: list[dict]
      images: list[str]
  ```
  adapter `pymupdf4llm` (da `get_text("dict")` + `pymupdf4llm.to_markdown`) e
  adapter `xberg` (output elementare); allineamento per IoU del bbox + confronto del
  testo normalizzato. Concordano → alta confidenza; divergono → conflitto, segnale
  per un fix `reconcile` o per il VLM **solo lì**.
- **D. Specializzazione per elemento** (più fragile). PyMuPDF4LLM per il corpo,
  Xberg per content filtering (header/footer/watermark + dedup cross-page, oggi
  euristico via `_strip_margin_blocks`), reading-order repair, metadata/JSON.
  Splicing tra strutture: da tenere per ultimo.

Costi/caveats:

- PyMuPDF4LLM ha costo marginale ~0; Xberg aggiunge solo il wheel (+155 MB), niente
  secondo stack di inferenza.
- Eseguire entrambi per pagina raddoppia la latenza (~0.5 s + ~0.5 s): limitarlo
  alle pagine ambigue (predicato `when`).
- Rischio **doppio fixing**: `layout_engine` fa già parte di ciò che farebbe Xberg →
  mitigare con i predicati sul backend, supportati nativamente dal registry.
- Senza layout detection di Xberg il differenziale vs PyMuPDF si riduce: il valore
  vero sono i modelli layout/tabelle opzionali.

**Scala di escalation a tre stadi** (unisce le due domande):
1. PyMuPDF4LLM (economico, baseline)
2. Xberg (struttura/tabelle/reading order, ancora deterministico)
3. diff → VLM **solo sui blocchi divergenti** (crop, non pagina intera)

---

## 8. Architettura di integrazione proposta

1. **Registro engine** al posto della catena `if/elif` in `_extract_text`:
   interfaccia comune `is_available()` / `extract_page(pdf, n) -> (md, images)`
   (come già fa `benchmark/engines.py` con `extract_*(pdf, page)`). La voce compare
   nel combo solo se il pacchetto è importabile.
2. **Import lazy** dentro i metodi, così il tier light resta leggero e i motori
   assenti disabilitano la voce (pattern già usato per Docling/pdf_oxide).
3. **Worker riusabile**: generalizzare `DoclingExtractThread` (coda prioritaria +
   prefetch + cache per pagina + `generation`) a tutti i motori lenti.
   Marker/MinerU in subprocess.
4. **Requirements separati**: `requirements-xberg.txt`, `requirements-marker.txt`,
   `requirements-mineru.txt`. Un tier con più motori pesanti resta **solo da
   sorgente** (PyInstaller non regge torch+vLLM+ONNX sommati).
5. **Cache modelli**: `~/.cache/xberg`, `~/.mineru/models`, cache HF — documentare
   il download al primo uso e la possibilità di prefetch (come per Docling).

---

## 9. Fasi proposte

- **Fase 1 — Xberg + `layout_engine`** (forma B). Backend di prima classe, import
  lazy, `requirements-xberg.txt`, modelli opzionali. Rischio minimo, costo
  +155 MB. Da validare sul benchmark esistente contro PyMuPDF4LLM.
- **Fase 2 — fix `reconcile`** (forma C) + `suspect_blocks` nel profilo; attivo
  solo quando PyMuPDF4LLM e Xberg divergono; escalation al VLM solo sui blocchi
  sospetti. Introduce il VLM come opt-in.
- **Fase 3 — valutare Marker fast `--disable_ocr`** solo se si mantiene il tier
  `medium` (torch condiviso).
- **Fase 4 (opzionale, da sorgente) — MinerU** solo se emerge un vantaggio reale su
  layout/tabelle che giustifichi +1.8–2.3 GB.
- **Trasversale — benchmark**: estendere `benchmark/engines.py` e usare il consenso
  come oracolo; aggiungere test di regressione per i fix.

---

## 10. Rischi

- **Licenze**: mineru Apache-2.0 con condizioni aggiuntive; pesi Marker/Surya
  OpenRAIL-M (limite commerciale 5M$); Xberg MIT. Da verificare prima della
  distribuzione.
- **Piattaforme**: MinerU richiede Python 3.10–3.14, vLLM solo Linux; Marker su CPU
  richiede il binario `llama-server` (llama.cpp) — difficile da impacchettare con
  PyInstaller. Xberg ha wheel precompilate per Linux/macOS/Windows e richiede AVX2
  per le feature ONNX.
- **Privacy**: VLM remoto = pagine fuori dalla macchina. Preferire locale o usarlo
  solo offline come oracolo.
- **Riproducibilità**: due motori + VLM rendono il debug più difficile; mantenere
  sempre la possibilità di forzare un singolo backend e loggare chi ha prodotto
  ciascuna pagina.
- **Allineamento repo**: verificare che `layout_engine.py`/`main.py` di
  `noesis-pdf-reader-lite` siano allineati al gemello prima di implementare.

---

## 11. Domande aperte

1. Obiettivo: **tutti e tre** i motori in app (tier opzionali), oppure **Xberg nel
   light** + al più uno degli altri?
2. Target: **distribuzione binaria** (PyInstaller/NSIS) o **solo sorgente**? Marker
   e MinerU bilanciati realisticamente solo da sorgente.
3. Il VLM deve essere **solo offline come oracolo** o anche **runtime opt-in**?
   Locale o remoto?
4. La sinergia deve arrivare alla forma **C (consenso/diff)** o ci si ferma alla
   **B (Xberg + `layout_engine`)**?

---

## 12. Fonti (raccolte il 2026-09-28)

- Repo `noesis-pdf-reader`: `NEXT_STEPS-installazione.md` (baseline 359 MB / 1.6 GB /
  6 GB), `layout_engine.py`, `main.py`, `benchmark/engines.py`.
- PyPI: `xberg` 1.2.9 (wheel 59 MB Linux x86_64), `mineru` 4.0.8, `marker-pdf` 2.0.0,
  `surya-ocr` 0.22.1 (+ metadata dipendenze).
- HuggingFace: `xberg-io/layout-models` (315.7 MB), `xberg-io/paddleocr-onnx-models`
  (1702.9 MB), `xberg-io/paddleocr-vl-1.6` (1839.1 MB),
  `opendatalab/MinerU-4_models_onnx` (818.5 MB), `..._torch` (853.4 MB),
  `opendatalab/MinerU2.5-Pro-2605-1.2B` (2220.2 MB),
  `datalab-to/surya-ocr-2` (1334.7 MB), `datalab-to/surya-ocr-2-gguf` (1429.2 MB),
  `datalab-to/line_detector0` (73.4 MB), `datalab-to/ocr_error_detection` (261.9 MB).
- Misura diretta: `pip download xberg --no-deps` + estrazione wheel → **155 MB
  installati**, `_xberg.abi3.so` 94 MB.
- Docs: `docs.xberg.io` (installation, model-sources, extraction),
  `opendatalab.github.io/MinerU` (tiers, model_source, extension_modules,
  quick_usage), README `datalab-to/marker` e `datalab-to/surya`.
