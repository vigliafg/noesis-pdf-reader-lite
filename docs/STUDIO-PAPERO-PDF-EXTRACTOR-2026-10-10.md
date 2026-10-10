# Studio — Analisi di `papero-pdf-text-extractor` (idee trasferibili)

> **Scopo.** Analisi approfondita del progetto open-source
> **papero** (`beatrizalmeidaf/papero-pdf-text-extractor`), estrattore di
> struttura da PDF **CPU-only, senza modelli ML**, per estrarne idee
> trasferibili al nostro motore (`ir_layout` + `main._apply_ir_on_page`).
>
> **Natura.** Documento di **brainstorming/ricognizione**. Non propone
> modifiche da mergiare: elenca algoritmi, scelte di design e metodologie
> osservate, le mappa sui nostri difetti aperti e propone una priorità.
>
> **Riferimenti al codice esterno.** Repository
> `https://github.com/beatrizalmeidaf/papero-pdf-text-extractor`,
> commit `1b076dad96be246f9bca58cf69c59c77651ea694`, release **3.1.1**
> (MIT license). I rimandi `file:riga` in questo documento sono quelli di
> quel commit.
>
> **Rimandi interni.** `STUDIO-E2E-VERDETTO-2026-10-11.md` (difetti aperti),
> `MIGLIORIE-MOTORE.md` §3 (esperimenti parcheggiati),
> `HANDOFF-2026-10-11.md` §7 (residuo word-level), `PYMUPDF4LLM-KNOWLEDGE-BASE.md`.

---

## 0. Sintesi esecutiva

papero risolve lo stesso problema del nostro motore (recuperare **struttura**
da un PDF, non solo il testo) con una strategia **opposta**: solo geometria su
`pypdfium2`, zero modelli ML, più Apache Tika come secondo motore (metadati,
heading del tagged-PDF, OCR, formati non-PDF).

Le **idee più utili per noi**, in ordine di valore:

1. **Tabella come immagine = artefatto parallelo, non sostituto** del markdown
   (JSON/ZIP con il crop, markdown con la griglia). Valida la via ibrida del
   nostro brainstorm su "tabelle come figure" (§6.1).
2. **Ricostruzione a livello di glifo** (span/parole/segmenti) → produce il
   **vocabolario pulito** che servirebbe al nostro fix de-glue/de-split
   parcheggiato (§6.2).
3. **Composizione degli accenti disegnati come glifi separati**
   (`Computa¸ca˜o` → `Computação`) (§6.3).
4. **Matematica tipografica** ricostruita dalla geometria (frazioni/radici
   disegnate → testo + LaTeX), con la regola che distingue una frazione da un
   bordo di tabella (§6.4) — risposta alla nostra "prosa formula persa".
5. **Tabelle ruled/unruled/booktabs** con *righe logiche* — riferimento
   indipendente per la classe "tabella complessa" (§6.5).
6. **Ordine XY-cut column-aware** con guardie anti-tabella — metro
   indipendente per le colonne interlacciate (§6.6).
7. **Figure raster + grafici vettoriali** unificati, con etichette assorbite
   (§6.7).
8. **Fidelity report** runtime con copertura "senza separatori" — antidoto ai
   falsi positivi del nostro `text_integrity` (§6.8).
9. **Metodologia a due implementazioni con test di parità in CI** (§6.9).
10. Dettagli minori (drop-cap, marker di lista, hard-break, furniture,
    heading autoritativi, chunk RAG) (§6.10).
11. **Vista di confronto posizionata** pagina↔output, col testo mancante
    cerchiato e i blocchi flaggati evidenziati (§6.11) — forte per la nostra UI.
12. **Dashboard di fidelity** di campagna (segnali pooled + problemi per
    codice) (§6.12).
13. **Export XLSX/DOCX/CSV/ZIP** e **robustezza del motore** (isolamento +
    riavvio su crash) (§6.13, §6.14).

**Cautele**: stack diverso (`pypdfium2` vs `pymupdf`); papero ammette che i
tool ML vincono su layout irregolari e matematica complessa; nessuna nuova
dipendenza senza valutazione (nostro vincolo). Vedi §7.

---

## 1. Metodo

- Clone shallow del repository in `/tmp/opencode/papero` (fuori dal workspace,
  nessun file del nostro progetto toccato durante la lettura).
- Lettura integrale di `src/papero_extract/layout.py` (3062 righe) e degli
  altri moduli Python; ricognizione di `web/assets/engine.js` (2312 righe) e
  `tests/` (incluso il test di parità Python↔JS).
- **Tutti i moduli Python** sono stati letti integralmente, inclusi `api.py`,
  `batch.py`, `cli.py`, `config.py`, `tika_client.py`, `tika_xhtml.py`,
  `__init__.py`.
- Del **motore JS** (`web/assets/engine.js`, 2312 righe) sono stati letti i
  lettori (`read_glyphs`/`read_chars`/`read_graphics`), `analyze_page`,
  `extract_document` e `cropping`; gli altri asset web (`export.js`,
  `fidelity.js`, `compare.js`, `i18n.js`, `app.js`, `columns.js`,
  `mathtext.js`, `texfonts.js`, `symbols.js`) e i test sono stati letti per
  intero.

---

## 2. Panoramica architetturale

### 2.1 Moduli (Python)

| modulo | righe | ruolo |
|---|---:|---|
| `layout.py` | 3062 | **cuore**: geometria, span, ordine, tabelle, figure, formule, passate documento |
| `api.py` | 583 | REST API (FastAPI, opt-in) |
| `extractor.py` | 493 | entry point `extract`/`extract_text`, orchestrazione PDFium + Tika |
| `batch.py` | 438 | cartella → dataset + fidelity report |
| `render.py` | 356 | Document → Markdown/testo/HTML/CSV |
| `fidelity.py` | 345 | valutazione qualità senza ground truth |
| `cli.py` | 294 | CLI |
| `tika_xhtml.py` | 237 | parsing XHTML di Tika → blocchi |
| `tika_client.py` | 219 | client Tika Server (keep-alive) |
| `model.py` | 212 | modello dati condiviso (`Document`/`Page`/`Block`) + schema JSON |
| `mathtext.py` | 202 | matematica inline → LaTeX |
| `symbols.py` | 189 | legature, PUA, Unicode↔LaTeX, apici/pedici, accenti |
| `columns.py` | 139 | gutter/colonne da bbox (profilo di copertura) |
| `cleaning.py` | 114 | pulizia testo (de-sillabazione, header/footer/numero pagina) |
| `tex_fonts.py` | 99 | decodifica dei font matematici TeX senza ToUnicode |
| `chunks.py` | 89 | chunk per RAG |
| `config.py` | 63 | configurazione |
| `__init__.py` | 44 | export pubblico |

### 2.2 Pipeline per pagina (`layout.analyze_page`, `layout.py:2361-2434`)

```
read_chars        →  ogni glifo visibile: loose box, font, hint spazio/a-capo
read_graphics     →  regole (h/v), ink vettoriale, immagini, fill
_typeset_math     →  frazioni/radici disegnate → glifi "5⁄12", "√2"
build_spans       →  righe visive + split a gap larghi (span)
charts            →  regioni-grafico vettoriali (prima delle tabelle)
detect_ruled_tables  →  tabelle con regole (+ senza regole)
detect_figures    →  figure raster + grafici, etichette assorbite
reading_order     →  XY-cut ricorsivo column-aware → bande
lines_to_blocks   →  paragrafi, liste, formule, code + run inline
```

### 2.3 Passate a livello documento (`layout.finalize`, `layout.py:2536-2555`)

```
_mark_furniture    →  header/footer/numero pagina/logo ripetuti
_normalize_sizes   →  dimensioni omogenee (font metriche)
_classify_headings →  heading per dimensione/formato + hint tagged-PDF
_merge_heading_lines
_absorb_formula_bits
_compose_figures   →  pezzi di figura + didascalie in un blocco
_link_captions     →  didascalia ↔ tabella/figura per prossimità
_layout_format     →  allineamento, indent, interlinea
```

Le passate documento vivono fuori da `analyze_page` **per poter analizzare le
pagine in processi paralleli** (`extractor._run_layout`, `extractor.py:414`).

### 2.4 Due motori in parallelo (`extractor.extract`, `extractor.py:244-331`)

- **PDFium** = geometria (ordine, tabelle, figure, formule, bbox).
- **Tika** in un thread (è una chiamata HTTP): metadati, heading del
  tagged-PDF, OCR Tesseract, formati non-PDF (DOCX/PPTX/XLSX/EPUB/HTML…).
- Tika ha un timeout (`TIKA_WAIT_S = 30`); se non risponde, PDFium basta e si
  aggiunge un warning (`engine = "pdfium"`).

---

## 3. Dipendenze e vincoli dichiarati

Da `pyproject.toml` (v3.1.1):

- `pypdfium2>=4.30` (motore di layout + rendering), `pillow>=10` (crop PNG),
  `tika>=2.6.0` (server jar), `requests>=2.31`.
- Extra `api` = FastAPI/uvicorn; extra `dev` = pytest, fpdf2, ruff.
- `requires-python >=3.10`.
- **Nessuna dipendenza ML** (no PyTorch): è il punto di vendita del progetto
  (`benefits`: latenza ~136 ms `fast` / ~543 ms `structured` su arXiv dense,
  contro 82.9 s di Docling — numeri del loro README).

Il motore JS (`web/assets/engine.js`) è la stessa pipeline su pdf.js:
`tests/js/parity.mjs` confronta i due motori blocco per blocco in CI.

---

## 4. Analisi moduli nel dettaglio

### 4.1 `layout.py` — primitive

**`Char`** (`121-139`): glifo con box, font, `space` (0/1/2) e `newline`.
Deriva una volta `size`, `cx`, `cy` ("letti milioni di volte").

**`Span`** (`142-195`): run su una baseline, spezzato a gap larghi; calcola
`bold`/`italic`/`mono`/`math` per maggioranza di glifi; se **ruotato** tiene
l'ordine di stream; altrimenti costruisce i **segmenti** (`_segments`).

**`_segments()`** (`209-280`) — pezzo portante:
- stima la dimensione "testo principale" come la più condivisa (gli indici
  possono superare le lettere: `MnO2(s) + CO2(g)`);
- **tracking**: gap di parola = mediana dei gap del run + `main * WORD_GAP_EM`
  → gestisce il **letter-spacing** (`T Í T U L O`);
- marca `sup`/`sub` per **shift di baseline** e dimensione ridotta, con soglie
  a due livelli (piccolo netto vs "appena più piccolo");
- glifo di un altro font dentro un indice che segue i vicini (`(ℓ)`);
- **spazio**: solo se gap reale/space di PDFium, con eccezioni (virgola dopo
  icona inline, esponente che sta attaccato: `x²` non `x ²`).

**`_font_info()`** (`504-549`): legge nome/weight/flags una volta per font;
bold da weight≥600 o nome; italic da flag o nome; **`rotated`** dalla matrice;
**`white`** dal colore di riempimento. Riconosce i cut URW (`Medi`) e
Computer Modern (`cmbx`/`cmti`).

**`_fast()`** (`552-559`): ri-dichiara le funzioni PDFium calde con tipi C
semplici — "stessa libreria, marshalling più economico". Micro-ottimizzazione
che spiega la loro latenza bassa.

**`read_chars()`** (`575-668`): ogni glifo con loose box, font, hint; gestisce
surrogati UTF-16, marker di **sillabazione di PDFium** (`0x02`/`U+FFFE`),
glifi senza Unicode (PUA → `map_errors`), e il **"not" di TeX** (uno slash che
colpisce il glifo successivo: `=` → `≠`).

**`read_graphics()`** (`706-813`): distingue **regole** (h/v) da **ink**
vettoriale e **fill**; filtra fill bianchi/trasparenti (mascheramento); srotola
i `Form`; decodifica i path multi-segmento come griglie (se lineari ≥60%) o
come arte vettoriale.

### 4.2 Accenti disegnati come glifi separati — `_compose_accents` (`921-958`)

`a` + `˜` → `ã`. L'accento si attacca al vicino con cui **si sovrappone
orizzontalmente**; senza overlap: accenti **sopra** attaccano al precedente,
**cedilla/ogonek** al successivo (`BELOW_ACCENTS`). Mappa spacing-accent →
combining in `symbols.SPACING_ACCENTS` (`symbols.py:172-180`),
`compose_accent()` usa `NFC` (`symbols.py:183-189`). Cita il caso LaTeX
"`Computa¸ca˜o` → `Computação`".

### 4.3 Matematica tipografica — `_typeset_math` (`303-440`)

Frazioni e radici sono **disegnate**, non digitate: numeratore, una riga, il
denominatore; il segno di radice è a tratti. Letti come glifi escono `5 12` e
`10 2`; la geometria li rimette in linea come `5⁄12` e `10√2` (poi LaTeX
`\frac{5}{12}`, `\sqrt{2}`). Dettagli:
- **`row_goes_on()`**: se ci sono glifi sulla stessa riga accanto alla regola,
  è una sottolineatura/bordo di tabella, non una frazione;
- selezione per `bisect` sui centri dei glifi;
- il **segno di radice** richiede un "uncino" a sinistra e nessuna chiusura
  sotto (che sarebbe un box/cella);
- le righe usate come segni **vengono rimosse** dalle regole (`g.hrules`…),
  così non diventano bordi di tabella.

`plain_math` (`287-292`) e `math_latex` (`452-457`) chiudono il giro;
`_OPERATOR_GAP` (`445`) sistema la spaziatura attorno agli operatori.

### 4.4 Ordine di lettura — XY-cut column-aware (`1142-1276`)

- **`Atom`** (`1143`): un elemento (span | table | figure) con un `core`
  verticale "ristretto" per gli span (evita che un esponente allarghi la banda).
- **`_bands()`** (`1161`): raggruppa in bande per **sovrapposizione verticale**.
- **`_gutters()`** (`1176`): gap orizzontali tra gli atomi.
- **`_split_columns()`** (`1194-1231`): prova a tagliare su ogni gutter, **ma**
  - non taglia se attraversa le righe di una **tabella** (`cuts_table`:
    run di ≥3 righe o tutte le righe "a ponte");
  - accetta un gutter noto a livello pagina ("il gutter della pagina taglia
    anche se un lato non è prosa" — etichette di un grafico);
  - la condizione di "prosa" è sulla **lunghezza mediana** dei testi
    (`_text_like`, `TEXT_LIKE_CHARS=15`).
- **`reading_order()`** (`1244-1276`): ricorsivo; guarda **due bande avanti**
  (un titolo corto prova di essere colonna solo con le righe sotto);
  se una banda è una riga-tabella e la precedente no, non la taglia.

`columns.py` (`18-79`): il gutter di pagina da **profilo di copertura** — per
ogni striscia verticale somma l'altezza di testo che la attraversa; i blocchi a
tutta larghezza (tabella/figura su più colonne) sono contati a parte; la
"valle" tra due picchi è un gutter, se abbastanza larga (`MIN_GUTTER=6`) e con
testo adeguato su entrambi i lati. Scarta gutter che creerebbero colonne
strette (numeri di equazione a margine, etichette di figura).

### 4.5 Tabelle (`1279-1644`)

- **`_merge_rules`** (`1290`): fonde segmenti collineari.
- **`ruled_regions`** (`1314-1370`): **union-find** tra regole che si toccano →
  griglie connesse (bbox, x delle colonne, y delle righe, flag "verticali
  reali"). Le regole orizzontali impilate senza verticali (booktabs, Word
  "rows only") sono unite **solo se non c'è prosa in mezzo**
  (`_prose_between`, `1303`) **e non c'è un'immagine** in mezzo — cioè non
  fonde due tabelle identiche una sotto l'altra con testo/figura in mezzo.
- **`_projection_columns`** (`1431-1454`): confini di colonna = **canali di
  whitespace che nessuna parola attraversa**; le righe con molte meno parole
  del solito (titoli a tutta larghezza) sono escluse dalla proiezione.
- **`_grid_rows`** (`1457`): riempie le celle dai `Word` (unità che non si
  possono tagliare); scarta colonne vuote.
- **`_table_block`** (`1482`): sanità — ≥2 righe, ≥2 colonne, ≥3 celle piene.
- **`_logical_rows`** (`1503-1533`): per tabelle senza regole per riga decide
  i confini dentro ogni intervallo tra regole: se i gap tra le righe di testo
  sono **bimodali**, solo quelli grandi separano righe (le celle che vanno a
  capo sono più strette); una riga che riempie **molto meno colonne** di
  quella sopra, stretta sotto, **continua** le sue celle. È la gestione dei
  casi "cella multi-riga + intestazione raggruppata".
- **`unruled_table`** (`1576-1634`): fa crescere una tabella dalle bande
  (`reading_order`), con stop se compare prosa (`_median(lengths) > 38` o
  `median(words) ≥ 6`), e `_distinct_columns` per evitare prose su più colonne.

### 4.6 Figure e grafici (`1647-1832`)

- **`_plot_frames`** (`1648`): rettangoli di regole con **molti segni vettoriali
  e poco testo** = assi di un plot (uno scatter). "Una tabella è l'opposto."
- **`chart_regions`** (`1677`): cluster di ink non-regola + plot frame, **cresciuti
  4 volte** per assorbire assi/ticks/legenda.
- **`without_rules_in`** (`1704`): toglie le regole interne ai grafici, così non
  possono formare tabelle.
- **`_label_of`** (`1720`): decide se un testo appartiene al grafico (valore di
  tick, titolo d'asse, legenda): non bold, non più grande del body, non caption.
- **`detect_figures`** (`1741-1791`): raster + grafici clusterizzati; scarta se
  dentro una tabella; **prosa vs figura** (`words > 40` o area > 35%); i fill
  di sfondo su prosa (senza raster) **non** sono figure; le etichette esterne
  entrano per 3 passate.
- **`_compose_figures`** (`2907-3021`): fonde label/pezzi **sopra** una
  didascalia "Figure N" in un unico blocco-figura; dentro una figura, il testo
  (ticks, legenda) diventa parte della figura.

### 4.7 Righe, paragrafi, run (`1835-2345`)

- **`Line`** (`1836-1895`): bold/italic "di riga" se ≥90% dei glifi; `size`
  dal span più lungo; `mono`, `math`, `has_scripts`.
- **`_is_list_start`** (`1898`): marker da `_ENUM`; i bullet sono in
  `symbols.BULLETS`.
- **`_formula_like`** (`1906-1935`): una riga è formula per `math_score`,
  font math, script; rifiuta se ci sono troppe parole di prosa.
- **`_new_equation`** (`1952`): due righe-formula impilate sono due equazioni
  distinte se entrambe "affermano" e non c'è un operatore di continuazione.
- **`_join_lines`** (`1964`) + **`_hard_break`** (`1983`) + **`_right_edges`**
  (`1988`): ricuce i wrap, **disfa la sillabazione**, e distingue un a-capo
  **voluto** (la prima parola della riga successiva ci sarebbe stata) da un
  wrap → mantiene un `NEWLINE` intenzionale.
- **`_runs`** (`2046-2102`): ricostruisce run bold/italic/script con la stessa
  logica di join; **`_strip_prefix`** (`2105`) toglie il marker di lista dai
  run.
- **`lines_to_blocks`** (`2205-2345`): raggruppa per kind (code/formula/
  list_item/paragraph) e decide la continuazione di paragrafo (leading,
  rientro, "linea corta che finisce in .!?"), con casi specifici per liste.

### 4.8 Layout di blocco e immagini (`2134-2517`)

- **`_layout_format`** (`2134`): allineamento (left/center/right/justify),
  indent, prima riga e **interlinea** rispetto alla colonna del blocco (usa
  `columns.area_of`).
- **`_attach_images`** (`2491-2517`): se `opts.images`, ritaglia un **PNG** di
  **figure + tabelle + formule** (`wanted={"figure","table","formula"}`) via
  `page.render(crop=...)`. **Questo è il punto chiave per il nostro brainstorm
  su "tabelle come figure"**: il crop è un **artefatto parallelo**, non il
  contenuto testuale.
- **`_visible`** (`2437-2475`): testo **bianco su bianco** scartato; ma bianco
  su cella colorata/immagine **tenuto**, e "fake bold" (doppio glifo
  sovrapposto) distinto con un **render** + istogramma.

### 4.9 `symbols.py`

- `LIGATURES` (`11-19`): `ﬁ`→`fi` ecc.
- PUA Symbol/Wingdings per font (`23-45`) + `normalize_char` (`111-124`):
  legature, simboli PUA, spazi anomali (`_SPACES`), invisibili (`_INVISIBLE`:
  soft hyphen, ZW*).
- `GREEK`/`OPERATORS`/`LATEX` (`49-78`), `MATH_CHARS`, `MATH_FONT_HINTS`
  (`cmmi`, `cmsy`, `stix`, `cambria math`…).
- `SUPERSCRIPT`/`SUBSCRIPT` (`97-102`) e `to_superscript`/`to_subscript`.
- `math_score` (`132`): quota di caratteri-matematica.
- `strip_accents_lower` (`163`) per la signature/furniture.
- `SPACING_ACCENTS`/`compose_accent` (`172-189`) per gli accenti glifo.

### 4.10 `tex_fonts.py`

Decodifica i **font matematici TeX senza ToUnicode** (OML/OMS/OMX): il codice
grezzo torna al simbolo (`0x0F`→`ϵ`, `Z`→`∫`, `hx,yi`→`⟨x,y⟩`). Valido **solo**
se il produttore è TeX (`is_tex_producer`: pdftex/luatex/xetex/dvips/dvipdfm);
altri producer rinumerano i glifi. `NEGATED` (`22`) gestisce `≠` come "slash
combining + =".

### 4.11 `mathtext.py`

Matematica **dentro la prosa** (`math_spans`, `112-167`): una sequenza è math
per **cosa contiene** (operatore tra operandi, funzione + argomento, esponente,
radice, lettera greca), non per il font. `latexify` (`184`) scrive `10⁶`→
`10^{6}`, `tg x`→`\operatorname{tg} x`; `inline_latex` (`195`) avvolge in `$…$`.
**Mirror** in `web/assets/mathtext.js` (parità testata).

### 4.12 `cleaning.py`

- Design rule: *"tocca solo le righe ai bordi della pagina; i numeri nel corpo
  non si rimuovono"*.
- `_PDFIUM_HYPHEN` (`[\x02\ufffe]`), `_HYPHEN_BREAK`, `_normalize`,
  `clean_pages` (`84-114`): rimozione numero pagina/header/footer per
  **frequenza** tra pagine (signature con cifre → `#`) e de-sillabazione.

### 4.13 `model.py`

Modello tipizzato condiviso: `SCHEMA = "pdf-text-api/document@1"`,
`BLOCK_TYPES` (heading, paragraph, list_item, table, figure, formula, caption,
code, header, footer, page_number), `FURNITURE` (header/footer/page_number
esclusi dal testo). `Block` (`51-111`) porta `bbox`, `rows` (tabella, prima
riga header), `latex`, `number`, `marker`, `image`, `caption`, stile
(`font_size`, `bold`, `runs`…), e **layout** (`align`, `indent`, `first_line`,
`line_spacing`, `leading`, `pt`, `font`, `tracking`) per l'export HTML/Word.
Coordinate **top-left** (y verso il basso) così la `bbox` si disegna diretta.

### 4.14 `render.py`

- `md_table` (`55`), `figure_words` (`77`: parole della figura **senza** i
  valori di tick, per la ricerca), `_emphasis`/`md_inline`
  (`88-120`: `**bold**`, `*italic*`, `<sup>/<sub>`, a-capo duri con "  ").
- `block_markdown` (`187-216`): **la tabella resta markdown** (il commento
  esplicito: *"The crop of the table stays in JSON/ZIP: repeating it here would
  duplicate the table."*); la **figura** emette l'immagine + la legenda in
  blockquote; la **formula** `$$ … \tag{n} $$`; la caption in italico.
- `to_html` (`309`) usa `columns.bands` → **due colonne restano due colonne**
  (CSS grid con frazioni proporzionali alla larghezza reale), `data-bbox` su
  ogni blocco.
- `tables_csv` (`346`): une tabella per blocca, commento `# página N, tabela K`.

### 4.15 `fidelity.py` (dettaglio in §5)

### 4.16 `chunks.py`

Chunk RAG: non attraversa un heading, **una tabella = un chunk** (con la sua
caption in testa), paragrafi impacchettati fino a `max_chars`; ogni chunk porta
**section path**, pagine e **id dei blocchi** (per citare/evidenziare la fonte).

### 4.17 `extractor.py` — orchestrazione ed errori (dettaglio)

Complemento a §2.4, con i dettagli che contano:

- **`extract_text`** (`209`): modalità "fast", solo Tika, testo per pagina +
  `likely_scanned` (<25 car/pagina).
- **`extract`** (`244`): modalità strutturata; Tika in un thread
  (`_tika_threads`) **mentre** PDFium lavora; `LayoutOptions(images,
  detect_tables, detect_formulas)`; `_run_layout` (`414`) distribuisce le
  pagine su `ProcessPoolExecutor` oltre `parallel_threshold` (default 48; nei
  `Settings` 16).
- **`assemble`** (`334`): merge finale + **`_heading_hints`** (heading del
  tagged-PDF → `_signature(text): level`, *"autoritativi quando presenti"*) e
  warning `unmapped_glyphs` se troppi glifi PUA.
- **`_apply_ocr`** (`449`): per le pagine senza layer di testo tiene i blocchi
  `figure` e sostituisce il resto con i paragrafi OCR di Tika; riassegna
  `order`/`id`.
- **Errori tipizzati** con `code`/`status`: `InvalidPDFError` (415/422),
  `EncryptedPDFError`, `PageRangeError`, `TooManyPagesError` (413),
  `EngineUnavailableError` (503) — un vocabolario d'errore uniforme.
- **`_extract_other`** (`468`): non-PDF → Tika → `to_pages` (blocchi **senza
  geometria**, `bbox=None`).
- **`_timed_tika`** (`402`): `marked_content=not force_ocr`, `ocr="no_ocr"` di
  default; `ocr_and_text` solo forzando. Tika ha un `TIKA_WAIT_S=30`.

Modello utile: **doppio motore con timeout e fallback** (se Tika non c'è,
`engine="pdfium"` + warning) — trasferibile se aggiungessimo un secondo
motore/advisor.

### 4.18 `tika_client.py` — client a bassa latenza

- **Non** usa `tika.parser.from_buffer` (nuova connessione + probe + `/rmeta`):
  parla con `/tika` su una `requests.Session` **keep-alive per thread**
  (`session`, `69`); misurato ~29→14 ms a caldo.
- **Scoperta del server** (`83-120`): `PTE_TIKA_URL` → server già su
  `127.0.0.1:9998` → avvio locale da jar (`_find_or_download_jar`, riusa la
  cache di `tika-python`), JVM opts `-Xshare:auto -XX:+UseParallelGC`.
- **Warm-up** (`122`): 3 parse di un PDF minimo (`_WARMUP_PDF`) per JIT-are il
  parser.
- **Header Tika** rilevanti (`157-170`): `X-Tika-PDFOcrStrategy`,
  `PDFspacingTolerance=2.0`, `PDFsortByPosition=false`,
  `PDFenableAutoSpace=true` e — chiave — `PDFextractMarkedContent: true` per
  gli heading dal **tag tree** del PDF.
- Reconnect una volta su `ConnectionError` (`179-188`).

### 4.19 `tika_xhtml.py` — un parser per tutti i formati

- Tika emette lo **stesso XHTML** per PDF/DOCX/PPTX/XLSX/ODT/EPUB/HTML:
  `<h1-6>`, `<p>`, `<ul>/<ol>/<li>`, `<table>`, `<div class="page">` (PDF),
  `<div class="slide-content">` (PPTX).
- `_Parser` (`47`) è un `HTMLParser` event-driven → `Block`: gestisce pagine
  (`div.page`), contatori di lista (`ol`), tabelle (`tr`/`td`→`rows`), code
  (`pre`), e le **label** (`div.lbl` → marker di lista).
- `tagged` (`218`): rileva se gli heading vengono dal **tag tree**
  (`extractmarkedcontent`) o se esiste un `<h*>`.
- `to_pages` (`224`): blocchi **senza geometria** (non-PDF e fallback OCR).

### 4.20 `config.py` — settings da ambiente

`Settings` (`24`) da variabili `PTE_*`: limiti file/pagine, `workers`,
timeout, `parallel_threshold`, API keys, rate limit, CORS, cache (entries +
MB), UI on/off. Tutto sovrascrivibile **senza toccare il codice**.

### 4.21 `cli.py` — interfaccia

- Sottocomandi **`extract` / `batch` / `serve`** (`202`).
- `extract`: `--format {markdown,text,json,html,csv}` (o dedotto dall'estensione
  di `-o`), `--images`, `--image-scale`, `--no-tables/--no-formulas`, `--ocr`,
  `--math {unicode,latex}`, `--fast`, `--raw`, `--dehyphenate`,
  `--keep-headers`, `--workers`, `--page-breaks`.
- Con `--images` i crop sono scritti in `images/` accanto all'output (`72-81`).
- `batch` → dataset + report; `serve` → uvicorn.

### 4.22 `batch.py` — cartella → dataset + report di fidelity

- Output: `documents/` (`.md`+`.json` per file, stesse sottocartelle),
  `chunks.jsonl`, `manifest.json`, `fidelity/report.json`,
  `fidelity/summary.html`, `fidelity/problematic/*.json`.
- `process_document` (`80`): top-level (eseguibile in un worker), e **un file
  rotto non ferma il batch** (`_failed` con `internal_error`/`write_failed`).
- `run_batch` (`155`): avvia Tika **una volta** prima dei worker (i figli lo
  trovano già su); `ProcessPoolExecutor`; scrive i chunk man mano; `summarize`
  (`265`) aggrega i segnali **"pooled"** e conta i problemi **per codice**
  (severity, documenti, occorrenze).
- `summary_html` (`379`): dashboard con tile (documenti ok/warning/error),
  barre per segnale, tabella problemi, elenco documenti da rivedere.
- Robustezza: `_long` per path Windows >260; se Tika non parte, **degrada a
  Tika-off** con una nota.

### 4.23 `api.py` — REST + app web

- `create_app` (`244`): **`WorkerPool`** (`90`) di processi `spawn` con
  `max_tasks_per_child=1000` (PDFium perde un po') e **riavvio automatico** su
  `BrokenProcessPool` → 422 `parser_crashed`; **`RateLimiter`** (`130`)
  fixed-window; **`ResponseCache`** (`151`) LRU per **hash del contenuto +
  parametri** (`X-Cache` hit/miss).
- `POST /v1/extract` (`348`): file + `password`, `mode` (structured/fast),
  `format` (json/markdown/text/html/csv/**zip**), `pages`, `per_page`,
  `images`, `image_scale`, `tables`, `formulas`, `ocr`, `math`; `GET /health`.
- `_structured` (`491`): Tika in un thread-pool **asincrono**
  (`asyncio.gather`) mentre i processi fanno il layout; `assemble` in un thread.
- **`_zip_bundle`** (`225`): `.md` + `.txt` + `.html` + `.json` +
  `-tabelas.csv` + `images/*.png`.
- Header `Server-Timing: extract;dur=…` + `X-Page-Count` (`575`); app web su
  `/` (same-origin) e `/assets`.

### 4.24 Web app e motore JS (gemello)

- **`engine.js`** (2312 righe) è il **port** della pipeline su pdf.js; le soglie
  hanno **gli stessi nomi** (*"keep both files in sync"*). Differenze chiave:
  - **`readGlyphs`** (`1469`): legge l'**operator list** di pdf.js eseguendo a
    mano la macchina di stato del testo (matrice, spaziatura, rise, colore) per
    ricostruire ogni glifo con posizione esatta e **fill colour** (per il
    bianco-su-bianco); recupera i simboli dai **nomi PostScript** o
    dall'**encoding TeX** (`texGlyph`/`texChar`).
  - **`readChars`** (`1606`): fallback su `getTextContent()` quando l'operator
    list non ha glifi (font anomali).
  - **`analyzePage`** (`1772`) / **`extractDocument`** (`2227`): parallelismo a
    **copie multiple del documento** (`MAX_WORKERS=4`, `PAGES_PER_WORKER=4`),
    una per worker pdf.js; `cropImages` ritaglia i PNG su `<canvas>` (`cropOf`).
  - Con `images` attivo disegna la pagina **una volta**, **riusa l'operator
    list di quel render** e **ritaglia i crop durante l'analisi** (`r.crops`)
    su una copia della pagina: così il canvas (megabyte/pagina) non resta in
    memoria.
  - `visibleOnly` (`1600`): come `_visible` Python ma **senza render** (pdf.js
    tiene i glifi sovrapposti, quindi scartare i bianchi non rimuove nulla).
- **`export.js`** (588): Markdown/testo/HTML/CSV/JSON **+ XLSX (un foglio per
  tabella)** e **DOCX fedele alla pagina** (`toDocx`, `353`: colonne, rientri,
  interlinea, run bold/italic e font) + `toZip`. Registry `FORMATS`/`exportAs`.
- **`fidelity.js`** (241): port di `fidelity.py` (stessi pesi/soglie), con
  `referenceItems` = elementi di testo da pdf.js per la copertura.
- **`compare.js`** (160): **vista di confronto** — affianca pagina renderizzata
  e blocchi estratti **nella stessa posizione**; sul lato PDF segna in rosso il
  testo **mancante** nell'output (`miss`), sul lato output evidenzia i blocchi
  **flaggati** con un badge; pannello fidelity con **salto per pagina**;
  formule rese con KaTeX.
- **`i18n.js`** (282, pt/en), `app.js` (UI), `columns.js`/`mathtext.js`/
  `texfonts.js`/`symbols.js` (port dei moduli Python omonimi).

**Perché ci riguarda (forte)**: `compare.js` è **esattamente** una versione
migliorata del nostro pannello affiancato. Noi mostriamo pagina + markdown;
loro mostrano pagina + **blocchi disegnati sulla pagina**, con il **testo
mancante cerchiato** e i blocchi flaggati evidenziati. È il modo più diretto per
esporre all'utente *"cosa manca e dove"*, coerente coi nostri zone/overlay.

### 4.25 `__init__.py` e suite di test

- `__init__` (`1`): export pubblico + `__version__`. **Incoerenza minore**:
  `__init__` dice `3.1.0`, `pyproject` `3.1.1`.
- **Fixture sintetiche** con `fpdf2`: `tests/fixtures.py` genera `structured`,
  `hard` (booktabs, grafico+etichette, accenti TeX, Gantt+scatter, tabelle
  stessa larghezza), `declaration` (bianco-su-bianco, interlinea 1.5, a-capo
  voluti), `exam` (frazioni/radici disegnate, stati della materia, opzioni con
  esponenti).
- **`test_layout.py`**: tabelle ruled/unruled/booktabs (numeri mai tagliati,
  `0,014`), ordine a due colonne, formule/frazioni/radici, accenti composti,
  logo=furniture, celle multi-riga, tabelle stessa larghezza che **non** si
  fondono.
- **`test_mathtext.py`**: cosa è math e cosa no, `latexify`, bold attorno alla
  math, opzione CLI `--math`.
- **`test_batch.py`**: dataset, chunk (tabella = chunk con caption, mai
  attraverso un heading), fidelity (trova testo perso, disordine, tabella
  mancante); **`test_api.py`** con `TestClient` (health, formati, pagina, zip,
  cache hit, file vuoto/grande, cifratura, rate limit, **crash del worker** con
  riavvio).
- **`tests/js/expected.py` + `parity.mjs`**: confronto **Python ↔ JS blocco per
  blocco** + markdown/testo LaTeX + **verdetto di fidelity** + localizzazione
  del testo perso.

---

## 5. Fidelity report (auto-valutazione) — dettaglio

`fidelity.assess(doc, reference_text(...))` (`220-345`) calcola 5 segnali
pesati (`_WEIGHTS`, `33`): text 0.4, reading_order 0.2, tables 0.15,
figures 0.1, formulas 0.15. Soglie: text error/warning 0.80/0.95, order 0.10,
garbled 0.05/0.005.

Elementi trasferibili:
- **`reference_text()`** (`112`): testo per pagina da PDFium, senza layout.
- **`_coverage()`** (`148-166`): confronta **senza separatori** e con
  `_fold` (`132`, NFKD + rimozione combining + casefold). Una parola
  **sillabata a fine riga** o **spezzata da un accento disegnato** è comunque
  "presente" — e gli esponenti (`10⁹`) sono separati. Protezioni: i numeri
  <3 cifre non contano (evita che una tabella persa risulti "trovata").
- **`_backwards`** (`174`): un blocco letto dopo uno che sta **sopra** nella
  stessa colonna → difetto d'ordine (come i nostri invarianti I1/I3, ma a
  runtime e per pagina).
- **`_overlap`** (`183`): due blocchi sulla stessa area (tabella di ≥2 righe
  o multi-riga); `block_overlap`.
- Check "caption di **tabella/figura** senza tabella/figura" (`280-303`) →
  `table_not_detected` (error), `figure_not_detected` (warning); `_table_problem`
  (`210`) segnala tabelle monoriga/monocolonna/irregolari/per lo più vuote.
- `garbled_text`: caratteri senza Unicode; `formula_uncertain`: formula senza
  LaTeX.

**Perché ci riguarda**: è un modello per (a) **ridurre i falsi positivi** del
nostro detector (`text_integrity` sovra-segnala) usando una copertura senza
separatori + folding, e (b) **esporre all'utente** un "quali pagine rivedere".

---

## 6. Idee trasferibili, mappate sui nostri difetti

### 6.1 Tabella/formula come **immagine parallela** (non sostituto)

**Cosa fa papero** (`LayoutOptions.table_images=True` + `_attach_images` +
`render.block_markdown`): ritaglia il PNG di tabella/formula/figura nel
**JSON/ZIP**, ma il **markdown resta la griglia**.

**Nostro problema**: brainstorm "tabelle come figure"; unica classe **grave**
aperta = ordine in tabella complessa (`ce24 p480`).

**Idea**: tenere il markdown come output testuale e aggiungere il **crop** come
allegato/galleria per le tabelle difficili (trigger di qualità). È la via
ibrida già discussa, ed è **validata da un progetto in produzione**.

### 6.2 Ricostruzione **glifo-level** → vocabolario pulito

**Cosa fa papero**: `read_chars` + `build_spans` + `_segments` + `words_of`
costruiscono span/parole dalla geometria (tracking, giustificata, script,
rotazioni), **senza** dipendere dal testo del classificatore.

**Nostro problema**: residuo **dominante** = glue/split word-level di
`pymupdf4llm` (`byimagingor`, `Ab|sorption`, `**E** sophageal`); il fix
"de-glue grounded" è **parcheggiato** perché il vocabolario di pagina era
sporco (sillabazione + figure + chrome) — vedi `MIGLIORIE-MOTORE.md` §3 e
`HANDOFF-2026-10-11.md` §7.

**Idea**: usare una ricostruzione glifo-level (su `pymupdf.get_text("rawdict")`
+ `get_drawings`) come **sorgente del vocabolario** per il fix grounded, o come
correttore locale delle righe sospette. È la "condizione per riprovare" già
annotata.

### 6.3 Accenti disegnati come glifi separati

**Cosa fa papero**: `_compose_accents` + `compose_accent` (`Computa¸ca˜o` →
`Computação`).

**Nostro problema**: Pack 5 (corpus2) — `cleanup_glyph_lines`,
`cleanup_ligature`; casi LaTeX con accenti separati.

**Idea**: regola deterministica e testabile di ricomposizione accento→lettera
per overlap. Basso rischio, test unitario.

### 6.4 Matematica tipografica dalla geometria

**Cosa fa papero**: `_typeset_math` ricostruisce frazioni/radici **disegnate**
come testo + LaTeX, **e rimuove le righe-frazione** dalle regole (per non
farle diventare bordi di tabella). `mathtext.py` fa LaTeX inline.

**Nostro problema**: `arxiv p30` (prosa/formula persa, recall 0.67) — difetto
reale "noto".

**Idea**: la geometria come **recupero** della formula/prosa dove l'emissione
fallisce; l'idea "una riga-frazione non è un bordo di tabella" è trasferibile
anche al nostro `_grid_table_from_page`/`_rotated_table_rects`.

### 6.5 Tabelle ruled/unruled/booktabs + righe logiche

**Cosa fa papero**: §4.5 (`ruled_regions`, `_projection_columns`,
`_logical_rows`, `unruled_table`).

**Nostro problema**: `ce24 p480` (ordine in tabella complessa; celle unite +
sotto-sezioni). Nota: il **page model** nostrano cattura la struttura ma
**l'emissione regredisce** (`MIGLIORIE-MOTORE.md` §3, `tools/measure_table_emit.py`).

**Idea**: usare la logica papero come **metro indipendente** (non produttore):
`_logical_rows` è un'idea specifica per celle multi-riga/intestazioni
raggruppate che potrebbe raffinare il nostro emettitore *selettivo*, o servire
da controprova al gate.

### 6.6 Ordine XY-cut con guardie anti-tabella

**Cosa fa papero**: `reading_order` + `_split_columns` + gutter a copertura
(`columns.py`).

**Nostro problema**: `fe22 p1038` (colonne interlacciate).

**Idea**: secondo parere indipendente sull'ordine (coerente con la nostra
filosofia di invarianti/riferimenti indipendenti); la guardia "non tagliare le
righe di una tabella" e "guarda due bande avanti" sono direttamente
implementabili nel nostro reorder/guard.

### 6.7 Figure raster + vettoriali con etichette

**Cosa fa papero**: `detect_figures` + `chart_regions` + `_compose_figures`.

**Nostro problema**: `su19 p1050` (figura vuota), `figure_missing`, testo
interno duplicato, didascalie.

**Idea**: rilevare **grafici vettoriali** (non solo raster) e assorbire le
etichette; `_visible` (render) per scartare testo invisibile/fake-bold.

### 6.8 Metrica "senza separatori"

**Cosa fa papero**: `_coverage`/`_fold`.

**Nostro problema**: `text_integrity` **sovra-segnala** (bold-initial,
composti sillabati) → tasso grezzo gonfiato (`HANDOFF-2026-10-11.md` §8).

**Idea**: adottare la copertura senza separatori + folding come metrica
**anti-falso-positivo** nel nostro registry/gate.

### 6.9 Metodologia: due motori + parità in CI

papero mantiene Python e JS allineati con un test di parità blocco-per-blocco.
È l'estensione naturale del nostro "riferimento indipendente"
(oracolo/page model) e suggerisce di **unificare la logica colonne** in una
primitiva condivisa da runtime e strumenti (`columns.bands` è usato sia dal
layout sia dagli exporter).

### 6.10 Dettagli minori trasferibili

| idea papero | dove | nostro aggancio |
|---|---|---|
| `_drop_caps` (`817`) | drop-cap non fonde le righe | Pack 4/6, testi con capolettera |
| `_join_markers` (`1117`) | proiettile/numero + testo = uno span | Pack 4 "bullet decorativi" |
| `_order_line` (`961`) | limiti del "∑" impilati | ordine formule |
| `_merge_fragments` (`988`) | frammenti di riga fuori ordine | glue di `pymupdf4llm` |
| `_hard_break` (`1983`) | a-capo voluto vs wrap | titoli/paragrafi spezzati |
| `_OPERATOR_GAP` (`445`) | spaziatura operatori | spaziature (`3 0-6 0`) |
| `_mark_furniture` (`2565`) | header/footer/logo per frequenza+posizione, soglia 0.3 | Pack 1 |
| `_normalize_sizes` (`2637`) | dimensioni da `pt`, non dalle metriche del font | gerarchia titoli |
| `_classify_headings` + `_heading_hints` (`2761`/`extractor.py:437`) | heading tagged-PDF autoritativi | `get_toc` |
| `_front_matter` (`2655`) | autori/affiliazioni non sono heading | Pack 4 |
| `_attach_images` (`2491`) | crop per figura/tabella/formula | galleria 🖼️ |
| `chunks.py` | tabella = chunk con caption + path sezione | export RAG futuro |
| `model.py` `align/indent/line_spacing` | export HTML/Word fedele | resa markdown/UI |

### 6.11 Vista di confronto **posizionata** (pagina vs output)

**Cosa fa papero** (`compare.js`): blocchi disegnati **dove sono** nella pagina,
testo **mancante** cerchiato sul PDF (marcato dalla fidelity), blocchi **flaggati**
evidenziati, pannello fidelity con **salto per pagina**.

**Nostro aggancio**: è il nostro pannello affiancato, ma *overlay-per-posizione*
invece che testo a destra. Rende visibile *"cosa manca e dove"* in un colpo
d'occhio; coerente con le nostre zone/overlay.

**Idea**: layer "difetti" sopra la pagina resa (mancanti + flag) + click dall'
elenco problematiche alla pagina.

### 6.12 Dashboard di fidelity di campagna

**Cosa fa papero** (`batch.summary_html`): tile ok/warning/error, barre per
**segnale** (pooled), tabella **problemi per codice** (severity, documenti,
occorrenze), elenco documenti da rivedere.

**Nostro aggancio**: i nostri `STUDIO-E2E-*` e `tools/campaign_report.py` sono
più grezzi. Una dashboard unica con segnali pooled e problemi per codice
aiuterebbe l'arbitraggio e la lettura dei run.

### 6.13 Export DOCX/XLSX/CSV/ZIP

**Cosa fa papero** (`export.js`, `render.tables_csv`): **XLSX** con un foglio
per tabella, **DOCX** fedele alla pagina (colonne/interlinea/run/font),
**CSV** delle tabelle, **ZIP** (md+json+html+csv+immagini).

**Nostro aggancio**: le tabelle come **dati** (CSV/XLSX), la galleria e un
eventuale export "dataset" per RAG. Idea: tenere il markdown come output e
offrire in parallelo tabella/dati + crop (cfr. §6.1).

### 6.14 Robustezza del motore (isolamento + riavvio)

**Cosa fa papero** (`api.WorkerPool`): l'estrazione gira in processi; un PDF
patologico che **fa crashare** il parser non abbatte il servizio — il pool si
riavvia e risponde 422 `parser_crashed`. Più `Server-Timing`, timeout→504,
rate limit.

**Nostro aggancio**: oggi una pagina che fa crashare l'estrazione blocca la
lettura. Isolare l'estrazione di una pagina in un **sottoprocesso con riavvio**
è un'idea di robustezza trasferibile (indipendente dallo stack).

---

## 7. Cautele e limiti

1. **Stack diverso**: papero è `pypdfium2` + Tika; noi `pymupdf` +
   `pymupdf4llm`. Le idee vanno **re-implementate** con le API `pymupdf`
   (`get_text("rawdict")`, `get_drawings`, `get_image_info`), non incollate.
2. **papero stesso ammette**: *"ML tools still win on very irregular layouts
   and complex math (matrices, aligned systems)"*. La nostra GNN è
   probabilmente **meglio** su layout irregolari: le idee papero vanno usate
   come **secondo parere/fallback**, non come sostituto del motore.
3. **Nessuna nuova dipendenza senza valutazione** (nostro vincolo). Tika,
   `pypdfium2`, Pillow **non** a runtime; al più `pypdfium2` **dev-only** per
   un cross-check, o re-implementazione in `pymupdf`.
4. **Progetto giovane** (v3.1.1, ~203★) e in **portoghese**: prendere le
   **idee**, non la fedeltà 1:1. Anche la gestione del portoghese
   (accenti/`ç`) è specifica.
5. **`table_images` ha un costo**: nel markdown non duplicano, ma nel JSON il
   crop pesa e la galleria si riempie (il nostro problema 🖼️).
6. **Determinismo (R12)**: le soglie papero sono euristiche tarate sui loro
   corpus (medici/accademici). Vanno ritarate sui **nostri** corpus held-out,
   senza tuning sulla pagina bersaglio.

---

## 8. Priorità suggerita

Allineata all'audit (`STUDIO-E2E-VERDETTO-2026-10-11.md`,
`HANDOFF-2026-10-11.md` §7):

1. **Vocabolario glifo-level pulito** (§6.2 + §4.12) → sblocca de-glue/de-split
   parcheggiato, che è il residuo **numericamente dominante**.
2. **Metrica senza separatori** (§6.8) → riduce i falsi positivi del
   `text_integrity`/gate: migliora la **precisione** del metro.
3. **Modello "immagine come allegato"** per tabelle/formule (§6.1) → chiusura
   "soft" della classe grave senza perdere testo; riusa `_emit_clip`.
4. **Riferimento geometrico per tabelle complesse** (§6.5) → metro indipendente
   per `ce24 p480`.
5. **Accenti disegnati** (§6.3) e **matematica tipografica** (§6.4) → regole
   piccole, testabili.
6. **Rilevazione grafici vettoriali** (§6.7) → `figure_missing`.
7. **Vista di confronto posizionata** (§6.11) → rende visibili i difetti
   all'utente (mancanti/flag) senza guardare solo il markdown.
8. **Dashboard di fidelity** di campagna (§6.12) → metodo di arbitraggio
   per-classi più leggibile; e **robustezza motore** (§6.14).

---

## 9. Appendice — mappa file e numeri

### 9.1 Riferimenti esterni

- Repo: `github.com/beatrizalmeidaf/papero-pdf-text-extractor` (MIT).
- Commit analizzato: `1b076dad96be246f9bca58cf69c59c77651ea694`, 2026-10-02.
- Release: `3.1.1`. Licenza: MIT.
- Latenza dichiarata (loro README, arXiv densi, 1 CPU): `fast` ~136 ms/PDF,
  `structured` ~543 ms/PDF (39 ms/pagina mediana), 0 fallimenti su 54 paper.

### 9.2 Funzione → riga (riferimento rapido)

| ambito | funzioni (`layout.py`) |
|---|---|
| glifi/font | `read_chars` 575, `_font_info` 504, `_fast` 552 |
| span/segmenti | `build_spans` 847, `_segments` 209, `_compose_accents` 921, `_order_line` 961, `_merge_fragments` 988 |
| math tipografica | `_typeset_math` 303, `plain_math` 287, `math_latex` 452, `segments_text` 463 |
| ordine | `reading_order` 1244, `_split_columns` 1194, `_bands` 1161, `_join_markers` 1117 |
| tabelle | `ruled_regions` 1314, `detect_ruled_tables` 1536, `unruled_table` 1576, `_logical_rows` 1503, `_projection_columns` 1431, `_grid_rows` 1457, `_table_block` 1482 |
| figure | `detect_figures` 1741, `chart_regions` 1677, `_plot_frames` 1648, `without_rules_in` 1704, `_label_of` 1720 |
| righe/blocchi | `lines_to_blocks` 2205, `_join_lines` 1964, `_runs` 2046, `_hard_break` 1983, `_layout_format` 2134 |
| documento | `finalize` 2536, `_mark_furniture` 2565, `_classify_headings` 2761, `_compose_figures` 2907, `_link_captions` 3036, `_absorb_formula_bits` 2834 |

Altri moduli: `columns.py` (`gutters` 18, `columns` 82, `bands` 117),
`cleaning.py` (`clean_pages` 84), `symbols.py` (`SPACING_ACCENTS` 172,
`compose_accent` 183), `tex_fonts.py` (`tex_char` 94), `mathtext.py`
(`math_spans` 112, `latexify` 184), `render.py` (`block_markdown` 187,
`to_markdown` 219, `to_html` 309), `fidelity.py` (`assess` 220, `_coverage` 148),
`chunks.py` (`chunk_document` 19), `extractor.py` (`extract` 244,
`_heading_hints` 437, `_apply_ocr` 449), `tika_client.py` (`parse` 138,
`ensure_started` 83), `tika_xhtml.py` (`parse_xhtml` 212, `to_pages` 224),
`config.py` (`Settings` 24), `cli.py` (`main` 202), `batch.py`
(`run_batch` 155, `summary_html` 379), `api.py` (`create_app` 244,
`WorkerPool` 90, `_zip_bundle` 225).

Motore JS e asset web: `engine.js` (`readGlyphs` 1469, `readChars` 1606,
`analyzePage` 1772, `extractDocument` 2227, `cropImages` 2172), `export.js`
(`toMarkdown` 159, `toXlsx` 265, `toDocx` 353), `fidelity.js` (`assess` 130),
`compare.js` (`renderCompare` 118).

### 9.3 Glossario dei parametri papero utili

| nome | valore (default) | significato |
|---|---|---|
| `SPAN_GAP_EM` | 0.9 | gap (in font) che spezza uno span |
| `JUSTIFIED_GAP_EM` | 2.6 | gap-massimo con spazio reale (giustificata) |
| `WORD_GAP_EM` | 0.18 | gap che inserisce uno spazio |
| `PARA_GAP_EM` | 0.55 | gap verticale che resta nello stesso paragrafo |
| `GUTTER_MIN` | 5.0 pt | gutter minimo |
| `RULE_MAX` | 2.5 pt | spessore massimo di un path = regola di tabella |
| `EDGE_ZONE` | 0.09 | quota della pagina per header/footer |
| `TEXT_LIKE_CHARS` | 15 | lunghezza mediana che rende una colonna "prosa" |
| `image_scale` | 2.0 | 144 dpi per i crop |
| `table_images`/`formula_images` | True | crop anche di tabelle/formule |

---

*Fine analisi. Documento di sola ricognizione: nessuna modifica al motore.*
