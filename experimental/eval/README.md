# Harness di valutazione — PyMuPDF4LLM vs Xberg

Valutazione **evidence-based** dei due motori su un corpus di PDF reali, con
l'agente AI come **arbitro visivo**: confronta il PNG della pagina con i
markdown prodotti da ciascun motore.

## Corpus

Generato da `make_corpus.py` in `corpus.json`:

| Bunch | Pagine | Focus |
|---|---|---|
| **A** | ha22.pdf 1230–1240 (11) | tabella complessa (TABLE 148-1), figure, testo denso |
| **B** | ha22.pdf 2230–2240 (11) | prosa a due colonne, figure, reading order |
| **ce24_r1** | ce24.pdf 10 pagine casuali > 300 | — |
| **co23_r1** | co23.pdf 10 pagine casuali > 300 | — |
| **cu25_r1** | cu25.pdf 10 pagine casuali > 300 | — |
| **pa19_r1** | pa19.pdf 10 pagine casuali > 300 | — |
| **pa23_r1** | pa23.pdf 10 pagine casuali > 300 | — |
| **su18_r1** | su18.pdf 10 pagine casuali > 300 | — |
| **su19_r1** | su19.pdf 10 pagine casuali > 300 | — |

Le pagine casuali usano **seed fisso** (riproducibile) e vengono scelte oltre
pagina 300. I PDF «altri» vivono in una cartella esterna, fuori dal repo:

```text
NOESIS_EVAL_PDF_DIR=/percorso/pdfs          # default: ../noesis-pdf-cloner-service/pdfs
```

> `ha22.pdf` è escluso dal campionamento casuale perché coperto dai bunch A/B.

## Perché i ritagli

Xberg estrae a livello **documento**: estrarre interi volumi (fino a 4132
pagine, 300 MB) sarebbe impraticabile. `make_slices.py` ritaglia ogni bunch in
un PDF da 10–11 pagine e **verifica che il testo coincida con l'originale**;
entrambi i motori lavorano sugli stessi PDF piccoli → confronto equo.

## Pipeline

```bash
experimental/eval/run_all.sh          # dalla radice, col venv di progetto
```

| Fase | Script | Output |
|---|---|---|
| 0 | `make_corpus.py` | `corpus.json` |
| 1 | `make_slices.py` | `out/slices/<id>.pdf` + `slices.json` |
| 2 | `render_pages.py` | `out/<id>/pages/*.png` (150 DPI) + `crops/*` (300 DPI) |
| 3 | `run_engines.py` | `out/<id>/<engine>/{raw,engine}/*.md` + `out/metrics/*.json` |
| 4 | `make_report.py` | `manifest.json`, `summary.md`, `report.html`, `scorecard.md` |

`run_engines.py` gira in un **processo separato per (bunch, engine, passata)**,
così tempi e picco RSS sono attribuiti correttamente. La cache di contenuto di
Xberg (`~/.cache/xberg`) viene svuotata **una volta** prima dell'intera passata
`cold`; la passata `warm` la riusa. I *modelli* layout vivono invece nella cache
HuggingFace (`models--xberg-io--layout-models`) e non vengono toccati.

## Cosa viene misurato

- **Velocità**: `import_ms`, ms totali, ms/pagina. Xberg è document-level →
  ms/pagina *ammortizzato* (una chiamata per l'intero ritaglio); PyMuPDF4LLM è
  per pagina. La differenza architetturale è già un risultato.
- **RAM**: picco RSS del processo (`ru_maxrss`).
- **Struttura/qualità (proxy)**: caratteri, parole, righe `\ufffd`, righe di
  tabella markdown, riferimenti immagine, heading.
- **Fedeltà/ordine/tabelle/figure**: giudizio dell'arbitro visivo (scorecard).

## Metodo dell'arbitro

Per ogni pagina si guarda il PNG, si leggono i due markdown (raw e
`+layout_engine`) e si compila la scorecard (`A` = vince PyMuPDF4LLM, `B` =
vince Xberg, `=` pari) su fedeltà, reading order, tabelle, figure, struttura.
`pdftotext -layout` è il riferimento terzo *non in gara* per smascherare
omissioni oggettive. Il verdetto finale (forze/debolezze + quando usare quale)
va in `VERDETTO.md`.

## Configurazione Xberg

Xberg gira **con i modelli layout/tabelle** (`LayoutDetectionConfig(strategy=
"always")` + `use_layout_for_markdown=True` + `PdfConfig(reading_order=True)`):
è lì il valore rispetto a PyMuPDF4LLM. Prima osservazione strumentale: su CPU
una estrazione layout di 11 pagine è nell'ordine dei **minuti** (molto più lenta
del text-layer PyMuPDF4LLM), quindi il `warm` con cache di contenuto è ciò che
rende Xberg usabile sulle riaperture.
