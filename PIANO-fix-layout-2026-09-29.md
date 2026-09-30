# Piano di lavoro — 2026-09-29

Documento operativo di `noesis-pdf-reader-lite`. Branch di lavoro:
**`fixes-layout`** (pushato, `origin/fixes-layout`). Obiettivo della giornata:
chiudere e mettere su `main` il lavoro su PyMuPDF4LLM, poi aprire i Pack 2/3.

---

## 0. Contesto e decisione (non si torna indietro)

- **Motore unico: PyMuPDF4LLM.** Xberg, Marker, MinerU **abbandonati**.
  Evidenza: su 92 pagine Xberg è 4–5× più lento, 3–8× più RAM e fragile
  (interleave colonne/tabelle). Verdetto completo in
  `origin/experimental` → `experimental/eval/VERDETTO.md`.
- Il branch `experimental` è **archiviato su remoto** (`origin/experimental`);
  copia locale degli artefatti in `/tmp/opencode/noesis-eval-archive/`.
- Tutto il valore ora è **migliorare PyMuPDF4LLM** (estrazione + layout engine).

---

## 1. Stato di partenza (già fatto)

| Elemento | Stato |
|---|---|
| Fix spaziatura (span di spazio + gap geometrico) | ✅ su `fixes-layout` |
| Pack 1 `cleanup_markdown` (header/footer, heading, liste, corsivi) | ✅ su `fixes-layout` |
| Test sicurezza/idempotenza/gold | ✅ 171 test OK |
| `.gitignore` `*.pdf` | ✅ |
| Ultimo commit | `a5ddd1a` |

---

## 2. Task di domani, in ordine

### T1 — Portare `fixes-layout` su `main` (priorità 1)
1. `git checkout fixes-layout && git pull`
2. Aprire la **PR** `fixes-layout → main` con descrizione:
   root cause spaziatura, Pack 1, impatto (13 pagine corrotte → 0), test.
3. Verificare la **CI** (`.github/workflows/`), poi merge (squash).
4. Dopo il merge: `main` aggiornato, branch `fixes-layout` cancellabile.

**Acceptance**: CI verde, `main` contiene fix + Pack 1, suite verde su `main`.

### T2 — Chiudere i buchi di test rimasti (priorità 2)
- **T2.1 Inclusion zones**: test funzionale di `_inclusion_order_markdown` con
  span di spazio preservati (le zone verdi non devono perdere spazi).
- **T2.2 CI-safe end-to-end**: fixture sintetica (PDF generato con pymupdf) che
  riproduca uno span di solo spazio; se non riproducibile, mantenere i test con
  stub e aggiungere un test d'integrazione su pagina generata a due colonne.
- **T2.3 Prestazioni**: misurare il costo del cleanup (4 passate regex/pagina)
  e annotare una soglia di guardia.

**Acceptance**: nuovi test **girano in CI senza PDF esterni**; suite verde.

### T3 — Pack 2: tabelle (priorità 3)
- Fix `rebuild_tables`: ricostruire la griglia da `page.find_tables()` e
  sostituirla al markdown di pymupdf4llm.
- `normalize_table_captions`: caption sempre `**TABLE x** …` su riga propria.
- `fix_empty_cells`: rimuovere `||` adiacenti e riallineare le colonne numeriche.
- Test + gold su **co23/p931** (celle vuote), **B/p2240** (griglia persa),
  **ce24/p489** (1 colonna), **pa23/p743** (caption come heading).

**Acceptance**: griglia corretta sui 4 casi, nessuna regressione sulle 92 pagine.

### T4 — Pack 3: figure (priorità 4)
- `uncomment_picture_text`: rendere visibile il testo dentro le figure (ora è
  un commento HTML che sparisce nel render).
- `link_figures`: placeholder `![figura](…)` dai bbox di `get_image_info()`.
- Test.

### T5 — Passate a livello documento (priorità 5, se resta tempo)
- Dedup header/footer per frequenza tra pagine.
- Tabelle multi-pagina (merge tramite header ricorrente).
- Gerarchia heading coerente col TOC (`doc.get_toc()`).

### T6 — Documentazione e release (a chiusura)
- Aggiornare `README.md` (feature del motore) e changelog/versione se serve.
- Ripetere build release se il merge lo richiede.

---

## 3. Comandi pronti

```bash
# ambiente
.venv/bin/python -m unittest discover -s tests -v      # suite completa

# gold con PDF reali (fuori repo)
NOESIS_GOLD_PDF_DIR=../noesis-pdf-cloner-service/pdfs \
  .venv/bin/python -m unittest tests.test_text_spacing -v

# archivio valutazione (non nel repo)
ls /tmp/opencode/noesis-eval-archive            # out/, corpus.json, VERDETTO

# recuperare il branch sperimentale archiviato, se mai servisse
git fetch origin experimental && git checkout experimental
```

---

## 4. Criteri di accettazione globali

1. **Nessuna parola incollata** introdotta dal motore (glue = 0 sul corpus).
2. **Nessun falso positivo** del cleanup (tabelle, code block, corpo intatti).
3. Suite verde **anche in CI** (i test non devono dipendere da PDF esterni).
4. `main` resta il repo "lite": un solo motore, nessun hook Xberg.
5. Ogni nuovo fix attivabile/disattivabile da `fix_rules.json`.

---

## 5. Rischi e note

- **PDF esterni non versionati** (`ha22.pdf` e gli altri): i gold test **skipano**
  in CI. T2.2 serve proprio a coprire questo buco.
- Euristiche **conservative**: in caso di dubbio non trasformare (meglio un
  difetto cosmetico che cancellare contenuto).
- Non reintrodurre Xberg/Marker/MinerU: la decisione è presa e documentata.
- Se un domani si riapre il tema, il verdetto e l'harness sono recuperabili da
  `origin/experimental` (nessun lavoro perso).

---

## 6. Stato al termine della giornata

| Task | Stato | Note |
|---|---|---|
| T1 `fixes-layout` → `main` | ✅ | PR #1, CI verde, merge squash su `main` (`48fd6de`); branch `fixes-layout` cancellato |
| T2 buchi di test | ✅ | T2.1 zone di inclusione; T2.2 end-to-end sintetico CI-safe; T2.3 soglia di guardia sul cleanup |
| T3 Pack 2 tabelle | ✅ | `rebuild_tables` + `normalize_table_captions` + `fix_empty_cells`; 4 casi gold verdi |
| T4 Pack 3 figure | ⚠️ parziale | `uncomment_picture_text` fatto e testato; `link_figures` **rimandato** (vedi sotto) |
| T5 passate a livello documento | ⬜ | non iniziato (priorità 5, tempo esaurito) |
| T6 documentazione | ✅ | README aggiornato (pack + CI + test) |

### Cosa è cambiato nel codice

- **CI**: nuovo `.github/workflows/tests.yml` (prima nessun workflow eseguiva i
  test). Gira headless, i gold su PDF esterni si skippano da soli.
- **Pack 1** (già su `fixes-layout`): spaziatura + `cleanup_markdown`.
- **Pack 2**: tre fix in `layout_engine.py`, gated da `profile.has_tables` e
  disattivabili da `fix_rules.json`. `rebuild_tables` ricostruisce **solo** le
  tabelle malformate (`||` adiacenti o righe di lunghezza diversa), perché su
  alcune tabelle `find_tables()` è peggiore di pymupdf4llm (fonde l'header nella
  prima riga). Sui layout a 2 colonne con overlap non ricostruisce: lo fa già
  `reorder_columns`. `fix_empty_cells` riallinea i valori `+N` che PyMuPDF fonde
  nella cella del testo (co23/p931).
- **Pack 3**: `uncomment_picture_text` rende visibile il testo dentro le figure
  (era `<!-- Start of picture text -->`).

### Limiti noti (documentati, non risolti)

1. **co23/p931**: la griglia è riallineata per la maggior parte delle righe, ma
   alcune celle restano fuse da PyMuPDF (`Age, women Age in years–10`): è un
   limite a monte di `find_tables`, non reintroducibile dal motore.
2. **Didascalie perse dal reorder** (co23/p931 `TABLE 2`, ha22/p2240
   `TABLE 294-1`): `_column_aware_markdown` salta i blocchi dentro la regione
   della tabella, e la didascalia ci finisce dentro. Difetto **preesistente**
   (verificato: c'era prima dei Pack 2/3), da fixare in una passata dedicata.
3. **`link_figures` rimandato**: sulle pagine con figure del corpus
   `page.get_image_info()` non restituisce immagini (figure vettoriali; il testo
   arriva dall'OCR di pymupdf4llm), quindi non è validabile sui casi reali; va
   inoltre deciso nome/URI del file e l'integrazione con la gallery delle
   immagini.
4. **T5** (dedup header/footer tra pagine, tabelle multi-pagina, heading dal
   TOC) non è stato affrontato.

### Regressione

Confronto su **57 pagine reali** (ha22 A/B + 5 pagine per ce24, co23, cu25,
pa19, pa23, su18, su19) tra `main` pre-Pack 2 e questa proposta: nessuna perdita
di contenuto visibile; le uniche differenze sono miglioramenti (parole incollate
ricomposte, celle vuote rimosse, tabelle malformate ricostruite) e artefatti di
apice (`streptococci` + `<sup>b</sup>` → `streptococcib`).

---

## 7. Seguito — correttezza misurata e box (2026-09-29, seconda passata)

### Metrica (correzione importante)
La prima misura di "recall" era **inquinata**: la normalizzazione usava
`<[^>]+>`, che su testi con `pH <5.5` / `>10 g` rimuoveva tutto fino al `>`
successivo, gonfiando le "parole mancanti". Corretta in
`</?[a-zA-Z][a-zA-Z0-9]*[^>]*>` (solo veri tag HTML). Con la metrica corretta:

| | recall contenuto | parole mancanti | glue | spazi/100 |
|---|---|---|---|---|
| RAW PyMuPDF4LLM | 99.50% | 241 | 124 | 14.02 |
| ENGINE (dopo i fix) | **99.64%** | **174** | **113** | ~baseline |

Il motore è **al livello del baseline o leggermente sopra**, e le uniche due
pagine con perdita reale sono le stesse del raw (una scansione illeggibile e un
blocco che il raw stesso non estrae).

### Fix dei box
1. **Titoli di box tagliati dai margini**: `_margin_noise` prendeva per header di
   pagina qualsiasi riga corta nel bordo alto/basso, compresa la continuazione di
   un titolo di box (`or Maldigestion`, `box 3`). Ora un candidato deve **iniziare
   con maiuscola o cifra** (i veri header sì; le continuazioni di box no).
2. **Sfondi di colonna scambiati per box**: alcune pagine dipingono l'intera
   colonna con un rettangolo pieno; `_detect_boxes` lo rendeva una tabella a una
   colonna, spezzando le parole (pa23/p303, ce24/p489). Aggiunto
   `_looks_like_prose`: un rettangolo il cui testo è **prosa** (≥50% di righe con
   ≥8 parole) non è un box esplicativo e resta testo normale. I box/tabelle veri
   (righe corte, elenchi) restano invariati.

Effetto: pa23/p303 passa da ~143 righe-tabella a 0; su 57 pagine recall 99.50%
→ 99.64%, glue 124 → 113, nessuna pagina con delta grave.

### Test
- `test_margin_noise_ignores_lowercase_box_title_continuation`
- `test_prose_background_rect_is_not_a_box`
- suite completa: **216 OK**.

### Ancora aperto
- co23/303 e ha22/1235 **non** erano perdite reali (erano un artefatto della
  metrica sbagliata): con la metrica corretta il testo c'è tutto.
- Restano i limiti della §6 (`link_figures`, T5).

---

## 8. Didascalie perse — risolte (terza passata)

Le didascalie che sparivano (`co23/p931` TABLE 2, `ha22/p2240` TABLE 294-1)
non erano colpa del `reorder`: cadevano nella **fascia del margine alto** e
`_margin_noise` le scambiava per running header, quindi `_strip_running_headers`
le cancellava.

Fix in `layout_engine._margin_noise` (tre guardie, tutte conservative):
1. il candidato header deve iniziare con **maiuscola o cifra** (le continuazioni
   di titolo di box iniziano minuscole);
2. **niente didascalie**: una riga `TABLE|FIG|BOX|… n` è contenuto
   (`_CAPTION_MARGIN_RE`);
3. **niente titoli sopra un box/tabella**: `_is_title_above` protegge la riga
   immediatamente sopra un box rilevato (`ha22/p2240 "Transudative Pleural
   Effusions"`). Il calcolo usa solo `get_drawings` (≈11 ms), **non**
   `find_tables` (≈1 s/pagina): le didascalie di tabella sono già coperte dal
   punto 2.

Inoltre `main._box_title` non prende più un **numero di pagina** nel margine
laterale come titolo del box (era "2199" su ha22/p2240): richiede sovrapposizione
in x col box e scarta i titoli fatti di sole cifre.

### Test
- unit: caption in margine, titolo sopra box in margine, `_box_title` vs numero
  di pagina; gold co23/p931 e ha22/p2240.
- suite completa: **221 OK**.

---

## 9. Figure come "corpo unico + didascalia" (Pack 3b)

Problema segnalato: una figura non arriva come corpo + didascalia, ma
**smontata** in pezzi sciolti (testo interno/etichette + didascalia), perché
PyMuPDF4LLM non emette il corpo della figura (zero `![](...)`) e lascia solo il
testo interno come commento HTML (poi reso citazione).

### Soluzione (implementata)
- `_figure_regions(page)`: per ogni didascalia `FIG/FIGURE n` individua la
  regione grafica sopra di essa usando `page.cluster_drawings()` (funziona anche
  per le figure **vettoriali**, dove `get_image_info()` è vuoto) + le immagini
  embedded, con filtri per righe/sfondi.
- `_render_figure`: renderizza la regione in PNG (`_region_image`, riusa la
  logica della cattura manuale) nella cartella immagini del documento
  (`page_XXXX_fig_N.png`).
- `_link_figures`: inserisce `![figura n](file://…)` **prima della didascalia**;
  il testo interno alla figura (blocco citazione) viene tolto **solo** se è
  dentro la regione renderizzata (così non è duplicato), altrimenti resta.
- L'engine riceve `figures_dir`; l'app passa la sua cartella immagini e, a
  estrazione finita, aggiunge le figure della pagina alla **gallery**.
- Disattivabile da `fix_rules.json` (`disable: ["link_figures"]`).

### Verifica
- pa23/p602: grafico reso, `FIG. 14.10` dopo l'immagine, legenda non duplicata.
- ha22/p1230: mappa resa, `FIGURE 148-1` dopo l'immagine.
- Nessun falso positivo su pagine senza didascalia (`ce24/p489`, `co23/p301`).
- Costo: la scansione grafica parte **solo** se c'è una didascalia `FIG n`.
- Test: `tests/test_figures_link.py` (4 unit + 2 gold); suite **227 OK**.

### Ancora aperto
- `link_figures` non copre i casi in cui la didascalia non è `FIG n` (es.
  "Figure" senza numero) o è lontana dalla figura (> 80 pt).
- Resta **T5** (dedup header/footer tra pagine, tabelle multi-pagina, heading
  dal TOC).

---

## 10. Fix restanti (quarta passata)

### T5.1 — header/footer per frequenza tra pagine ✅
`_document_noise(page)` campiona le pagine del documento, conta le righe brevi
nei margini e tiene quelle che ricorrono su ≥3 pagine: sono chrome di pagina
anche se una singola pagina non le riconosce (es. header in minuscolo).
`_margin_noise` unisce questo insieme a quello della pagina corrente. Cache per
file (l'app riapre il documento a ogni pagina); ~1,5 s una volta per documento.

### T5.3 — gerarchia heading dal TOC ✅
Nuovo fix `toc_headings` (gated da `profile.has_toc`): per la pagina corrente
legge `doc.get_toc()` e allinea il numero di `#` dei titoli a quello reale del
TOC. Conservativo: tocca solo titoli già riconosciuti come heading o righe che
combaciano esattamente con una voce; niente tabelle/code block.

### Gap figure ✅
`_FIGURE_CAPTION_RE` accetta anche didascalie **senza numero** (`Figure: …`,
`Figure A …`) ma non frasi di corpo (`Figure shows that …`); la finestra sopra
la didascalia passa da 80 a 110 pt.

### T5.2 — tabelle multi-pagina ⬜ (non applicabile ora)
Il merge di una tabella spezzata su più pagine ha senso solo su un documento
concatenato; l'app estrae e mostra **una pagina alla volta** e non ha un
export/concatenazione. Implementarlo ora sarebbe codice morto: da fare insieme
a un eventuale "esporta/copia l'intero documento".

### Verifica
- Suite: **232 OK** (17 gold skip). CI verde.
- Corpus 57 pagine: recall 99.63% (≈ invariato), glue 113 vs 124 del raw.

---

## 11. Valutazione indipendente su corpus nuovo (80 pagine)

Corpus **diverso** dai precedenti: 10 pagine consecutive scelte a caso (pagina
> 200) da ognuno degli 8 PDF (`ha22 1768-1777`, `ce24 2581-2590`,
`co23 1564-1573`, `cu25 1149-1158`, `pa19 502-511`, `pa23 647-656`,
`su18 322-331`, `su19 1700-1709`). Confronto con il **text layer del PDF**,
più il motore **pre-fix** (commit `c24a173`).

| | recall contenuto | precisione | glue | righe tab. malformate | spazi/100 | figure linkate |
|---|---|---|---|---|---|---|
| RAW PyMuPDF4LLM | 99.55% | 98.36% | 244 | 12 | 13.97 | 0 |
| ENGINE **pre-fix** | 97.54% | 98.27% | 533 | 42 | 13.01 | 0 |
| ENGINE **ora** | **99.72%** | **99.33%** | **241** | **0** | 13.58 | **37** |

- `�` (U+FFFD): raw 3 su 1 pagina → **0**.
- Peggior delta per pagina vs raw: **≤ 4 parole** (numeri di pagina e parole
  incollate risolte), su 5 pagine su 80.

### Difetti ancora rilevati (arbitro esigente)
1. **Over-tabling dei box**: 3/80 pagine (es. `ce24/2584`) rendono un box/elenco
   come tabella a 1 colonna (88 righe-tabella vs 21 del raw). Nessuna perdita di
   contenuto (recall 99.7%), ma la leggibilità ne risente.
2. **Legenda figure**: il testo interno alla figura resta sotto l'immagine come
   citazione, con i suoi `<br>` letterali. Voluto (scelta "tieni la legenda"),
   ma il rendering non è pulitissimo.
3. Delta residui minimi (≤4 parole su 5 pagine), tutti numeri di pagina o
   parole incollate risolte.

### Fix aggiuntivi emersi da questo corpus
- **Nota di prosa a filo pagina** (`co23/p1564` "Page numbers followed by f
  indicate figure…"): veniva cancellata come footer. Ora una riga di margine che
  è una **frase** (finisce con `.` e ha ≥6 parole) non è più un candidato
  header/footer.
- **Legenda figura tenuta**: `_link_figures` non rimuove più la legenda, la
  sposta **sotto la didascalia** (corpo-immagine → didascalia → legenda), come
  da scelta dell'utente.
- **Box come liste, non tabelle a 1 colonna**: `_box_to_md` ricuce le righe
  andate a capo, rende i sottotitoli in MAIUSCOLO come `**grassetto**` e gli
  item come `- bullet` (paragrafi se il box è prosa). L'over-tabling sparisce:
  su 80 pagine le righe-tabella passano da **362 a 160** (raw: 165), e
  `ce24/p2584` da 88 a 6.

### Risultati aggiornati (dopo questi fix)
| | recall | precisione | glue | tab. malformate | righe-tabella | figure | `�` |
|---|---|---|---|---|---|---|---|
| RAW | 99.55% | 98.36% | 244 | 12 | 165 | 0 | 3 |
| ENGINE ora | **99.72%** | **99.33%** | **241** | **0** | **160** | **37** | **0** |

### Difetti ancora rilevati (arbitro esigente)
1. ~~Over-tabling dei box~~ → **risolto** (righe-tabella pari al raw).
2. Legenda figure: il testo interno resta sotto l'immagine come citazione
   multi-riga (i `<br>` ora sono a-capo veri, non HTML letterale).
3. Delta residui minimi (≤4 parole su 5 pagine), numeri di pagina e parole
   incollate risolte.

### Punteggio (rubric, 1-100)
Pesi: recall 50, precisione 15, glue/integrità spazi 15, tabelle 10, figure 5,
penalità formattazione fino a 5.

| | voto |
|---|---|
| RAW PyMuPDF4LLM | **≈ 89** |
| ENGINE pre-fix | **≈ 63** |
| ENGINE ora | **≈ 98** |

---

## §12 — Pack 4: leggibilità (fix 1-7) — 2026-09-29

Analisi visiva su 29 pagine (5 iniziali + 24 nuove significative) e verdict
dell'arbitro (modello con visione): **l'ordine di lettura è corretto**, ma la
*presentazione* ha difetti sistematici che il recall non misura. Sette fix.

| # | Fix | Dove | Chiave di disattivazione |
|---|---|---|---|
| 1 | Header/numero di pagina non trapelano più anche "travestiti" (`## HEADER`, `- 1123`, header spezzato in due pezzi fusi dal reorder) | `_norm_noise`, `_is_noise_line`, `_covers_candidates`, `_STANDALONE_NUM_RE` | `cleanup_markdown` |
| 2 | Glifi-bullet decorativi non diventano titoli (`### »`) e non restano in testa alle righe (`»** Titolo**`) | `_drop_decor_lines`, `_strip_leading_decor` | `cleanup_glyph_lines` |
| 3 | Titoli spezzati su due righe ricomposti (heading + riga bold, parola di raccordo, title-case numerato) | `_normalize_headings`, `_join_heading`, `_CONNECTORS`, `_ENUM_PREFIX_RE` | `cleanup_markdown` |
| 4 | Titoletti MAIUSCOLI fusi col paragrafo separati (senza toccare i run-in normali e le didascalie) | `_normalize_caps_runins` | `cleanup_caps_runins` |
| 5 | Cella-header vuota (colonna-etichetta) ripristinata in testa; righe dati allineate | `_clean_table_md`, `_table_to_md` | `fix_empty_cells` / `rebuild_tables` |
| 6 | Didascalie spezzate su più righe ricomposte (marcatore in testa, frammenti ridondanti scartati) | `_merge_caption_fragments_md` | `cleanup_caption_fragments` |
| 7 | Testo interno alla figura (assi/etichette) tolto quando la figura diventa immagine; legenda discorsiva conservata sotto | `_figure_internal_text`, `_link_figures` | `link_figures` |

Micro-fix aggiuntivi: spazio dopo l'apertura del grassetto (`** testo**` →
`**testo**`) in `_normalize_emphasis`.

### Metodo di arbitraggio
- **Non** il raw come ground truth (il raw non è verità: il recall non misura
  l'ordine). Arbitro = **lettura visiva** della pagina renderizzata vs markdown.
- Checklist automatica validata a mano: header/pagina trapelati, glifi-heading,
  titoli spezzati, maiuscole fuse, disallineamento tabelle, didascalie
  frammentate, figure mancanti, glue/`�`.
- Confronto **prima/dopo** sulle stesse 29 pagine + corpus nuovo di 50 pagine.

### Esito (arbitraggio 2026-09-29)

Arbitro = modello con visione + checklist automatica (validata a mano). Baseline =
HEAD `899a265` (stesso engine prima dei fix). Corpus: 29 pagine prima/dopo + 50
pagine nuove (random, non usate, 6-7 per PDF).

**29 pagine (prima → dopo)**

| difetto | BASE | NOW |
|---|---|---|
| header/pagina trapelati | 23 | **0** |
| glifi-heading | 6 | **0** |
| titoli spezzati | 5 | **1** (falso positivo: `ENDOCRINE DISORDERS` + `DIABETES MELLITUS`, due titoli distinti — verificato a video) |
| maiuscole fuse | 1 | **0** |
| tabelle disallineate | 2 | **0** |
| figure non linkate | 1 | **0** |
| **punteggio medio** | **96.0** | **99.4** |
| pagine con difetti | 12/29 | 1/29 (il falso positivo) |

**50 pagine nuove (NOW)**: punteggio **99.0**, con `leak=0, glyph=0, caps=0,
misalign=0, frag=0, figmiss=0`; unici residui 6 `split` = heading vuoti
`### ■` (glifo-bullet non nel set) su 3 pagine. Aggiunto `■□◻◼` ai glifi
decorativi → **risolti** (verificato su `ha22/p2806`: nessun heading vuoto,
`**FURTHER READING**` pulito). Atteso NOW50 ≈ 99.6.

**Fix aggiuntivi emersi dall'arbitraggio (oltre ai 7)**
- titoli MAIUSCOLI spezzati ricomposti (`… RELATED MOOD` + `DISORDERS`);
- **heading vuoti** `### ` / `### ■` uniti o eliminati;
- linking figure: matching didascalia robusto ai corsivi (`**Figure 19.3**
  _Continued_` ↔ `Figure 19.3 ■ Continued`) e alle didascalie incastonate in
  testo OCR (ricerca per contenimento) → risolti i `figmiss` di `pa19/503` e
  `pa23/147`.

**Giudizio dell'arbitro (visione)**
- **Ordine di lettura**: corretto su tutte le pagine ispezionate a video
  (colonne sx→dx, box embedded al loro posto, tabelle/figure a cavallo come
  separatori di banda, frasi a ponte ricomposte). Nessun interlacciamento.
- **Presentazione**: i difetti sistematici sono scomparsi (header/pagina, glifi,
  titoli spezzati, maiuscole fuse, tabelle disallineate, figure mancanti).
- **Verdetto**: il markdown è **leggibile in senso naturale**; il contenuto è
  integro (nessuna perdita rilevata: recall/precision restano 99.7/99.3).
- **Voto finale di leggibilità: ≈ 99/100** — restano solo residui rari e
  cosmettici (es. righe vuote residue, tabelle a intestazione multipla rese al
  meglio possibile in markdown).

**Residui noti (non bloccanti)**
- Alcune righe vuote residue dopo la rimozione di header/numero pagina.
- Le tabelle con header su più righe (righe fuse) restano tali: il markdown non
  può fondere celle; l'allineamento è però corretto.
- Pagine con OCR rumoroso conservano il rumore nel testo (limite della sorgente).

### Riproduzione
`/tmp/opencode/run_all.sh` (base → now → now50 → confronto);
metriche in `/tmp/opencode/arb/{base,now,now50}.json`, markdown in
`/tmp/opencode/arb/*.md/`, log in `/tmp/opencode/run_all2.log`.


---

## §13 — Piano di oggi (2026-09-30): test su **120 pagine** nuove (`corpus2`)

**Obiettivo**: validare l'engine attuale (dopo il merge del Pack 4) su **12 PDF
nuovi e più difficili** (cartella `corpus2/`, layout più impegnativi), prendendo
**10 pagine NON consecutive per PDF (120 in totale)**, casuali ma distribuite in
10 punti diversi dell'intero PDF (una per decimo → rappresentative). Con
**valutazione automatica (checklist + punteggio + velocità)** e **arbitraggio
visivo** fatto dall'agente (modello con visione): è l'agente a decidere se il
markdown è una rappresentazione **perfettamente leggibile e corretta** del PDF.

### Task
| # | Task | Note |
|---|---|---|
| E1 | Generare il corpus di **120 pagine** (10 per ciascuno dei 12 PDF di `corpus2/`, una per decimo del PDF → non consecutive e rappresentative) | `select_corpus.py` → `/tmp/opencode/pack5/corpus120.json` |
| E2 | Eseguire l'**arbitro** (checklist + punteggio + timing per pagina) con **timeout per pagina** e log di avanzamento | `run_batch.py`; una riga per pagina, `flush` |
| E3 | **Arbitraggio visivo** dell'agente: render PNG input + markdown output, confronto diretto pagina↔md | PNG in `/tmp/opencode/pack5/pages/` |
| E4 | Compilare il **report/verdetto**: velocità, efficienza, correttezza/leggibilità (voto 1-100), difetti residui | sezione nuova §14 |
| E5 | Se emergono difetti: fix mirato + test; altrimenti ok | follow-up |

### Metodo di valutazione (doppio)
1. **Automatica** — checklist dell'arbitro: header/pagina trapelati, glifi-heading,
   titoli spezzati, maiuscole fuse, tabelle disallineate, didascalie frammentate,
   figure mancanti, `glue`, `�`.
2. **Visiva (arbitro = agente)** — su un campione: ordine di lettura, fedeltà al
   senso della pagina, distinguibilità di box/figure/tabelle, fluidità di lettura.

### Comandi
```bash
cd /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite
# E1 — genera il corpus (10 pagine/PDF, una per decimo)
.venv/bin/python /tmp/opencode/pack5/select_corpus.py
# E2 — arbitro sulle 120 pagine (engine attuale), timeout+progresso
.venv/bin/python -u /tmp/opencode/pack5/run_batch.py /tmp/opencode/pack5/corpus120.json /tmp/opencode/pack5/out
# E3 — arbitraggio visivo: PNG già prodotti in /tmp/opencode/pack5/pages/
```
Artefatti: `/tmp/opencode/pack5/out/results.jsonl` (+ `summary.json`),
`/tmp/opencode/pack5/out/md/*.md`, `raw/*.md`, `pages/*.png`, `fig/`.

### Accettazione
- Header/pagina, glifi-heading, titoli spezzati, maiuscole fuse, didascalie
  frammentate → ~0; tabelle allineate; figure linkate dove presenti.
- Nessuna regressione su `glue`/`fffd`; nessuna perdita di contenuto (diff a
  campione sul testo di pagina).
- **Punteggio atteso ≥ 98/100**; sotto questa soglia: elencare i difetti e
  pianificare i fix.

### Nota
Il confronto "prima/dopo" non serve se non ci sono regressioni: il riferimento è
quanto già validato in §12 (29 pagine 96.0→99.4; 50 pagine 99.0).


---

## §14 — Report Pack 5: 120 pagine `corpus2` (2026-09-30)

Arbitro: **l'agente** (visione pagina↔markdown) + checklist automatica.
Baseline: `main` = `cdf3784` (dopo il merge del Pack 4). Nessun fix applicato
durante il test.

### 1. Metodo
- **Corpus**: 12 PDF nuovi e più difficili (`corpus2/`), **10 pagine per PDF =
  120**, una per decimo del documento (random, non consecutive: distanza minima
  17 pagine), rappresentative di tutto il PDF.
- **Pipeline reale**: `pymupdf4llm.to_markdown(doc, pages=[p], ocr_language="eng")`
  → `main._apply_engine_on_page(page, raw, figures_dir=...)`; Tesseract per gli
  scan.
- **Robustezza**: `signal.alarm` per pagina (240 s), `try/except` per pagina,
  una riga di log con `flush` per ogni pagina, `results.jsonl` scritto in append
  dopo **ogni** pagina. Durata ~6 min, stato sempre visibile, **0 blocchi**.
- Harness: `/tmp/opencode/pack5/{select_corpus,run_batch,reanalyze,report,review_list}.py`;
  artefatti in `/tmp/opencode/pack5/out/` (md, raw, pages PNG, fig, results).

### 2. Velocità ed efficienza
| fase | media | mediana | max | totale | quota |
|---|---|---|---|---|---|
| estrazione (OCR+parse) | 0.73 s | 0.56 s | 3.46 s | 87.9 s | 24 % |
| engine (layout+cleanup+tabelle+figure) | 2.22 s | 1.18 s | 17.41 s | 266.1 s | 72 % |
| render PNG (arbitraggio) | 0.05 s | — | — | 5.8 s | 2 % |
| **totale** | **3.1 s/pagina** | | | **370.5 s** | **19.4 pagine/min** |

- 120/120 pagine OK, **0 errori, 0 timeout**.
- L'engine è il collo di bottiglia (~2.2 s/pagina; picchi 17 s su `to22`, PDF
  tabellare/denso). L'estrazione è veloce anche con OCR (0.73 s medi).

### 3. Checklist automatica (detector tarati)
- **Punteggio medio 96.5/100**; mediana 98; 39/120 pagine con score 100.
- Occorrenze: `html_markup` 58 · `fig_missing` 24 · `caption_fragment` 23 ·
  `header_leak` 21 · `page_num_leak` 20 · `heading_glued` 15 ·
  `table_misalign` 12 · `split_word_heading` 12 · `split_heading` 8 · `glue` 6 ·
  `caps_merged` 2 · `fffd` 154.
- **Attenzione**: come nel Pack 4, buona parte di questi flag sono **falsi
  positivi** dei detector (vedi §5); il numero va letto insieme
  all'arbitraggio visivo, che è la fonte autorevole.

Peggior punteggio: `ox2_0959` 0 (indice ruotato a 3 colonne), `to22_0251` 64
(falsa tabella), `to22_0945` 90, `oh2_0313` 90.5, `na25_0346` 89.5.

### 4. Difetti REALI (arbitraggio visivo)

**ALTA severità**
1. **`to22_0251` — falsa ricostruzione di tabella.** Una pagina a *lista
   definizionale* (Tabella 6, voci A/B/C/S) viene riconosciuta come "tabella" da
   `find_tables` e `rebuild_tables` la distrugge: le etichette A/B/C/S spariscono
   e compaiono righe spurie `- 1`, `- 2`, `- 3`, `- d`. Contenuto/struttura persi.
2. **`to22_0762` — box laterale perso + ordine rotto.** Il box rosa
   "Bactericidal/Bacteriostatic" (tabella) **non compare** nel markdown
   (retention 0.50) e il box di destra "Reasons for Combination Therapy" viene
   inserito **dentro** la lista di sinistra, spezzando una frase. Il punteggio
   automatico dava 100: caso non rilevato dalla checklist.

**MEDIA severità**
3. **Markup HTML nel markdown (`<mark>`, `<sup>`, `<br>`).** 28 pagine su 7 PDF
   (oh2 10, fe23 9, ox2 4, ox16 3, na25 3, co26 1, fe22 1). Doppio danno: (a) è
   rumore HTML in un file markdown; (b) **impedisce la rimozione di header/
   footer**, perché `_norm_noise` non cancella i tag → **header trapelati
   sistematici**: `fe23` 10/10 pagine ("104 Section I`<sup>n</sup>` Diagnostic
   Imaging"), `oh2_0313` ("**`<mark>`8 The nervous sysTem`</mark>`**"),
   `ox2_0616` ("600 CHAPTER 9 **`<mark>`Endocrine organs`</mark>`**"),
   `ox16_0244` (idem).
4. **`fe23_0064` — pagina ruotata 90° (landscape).** Tabella grande + note:
   il markdown perde le note a piè di tabella e fonde le colonne
   (retention 0.41). `page.rotation` è 0 ma il testo è disegnato a 90°.
5. **`ox2` — indice a 3 colonne.** `profile_page` rileva `columns=1`
   (`splits` non trovati, `body=1`) → `reorder_columns` non parte → le 3 colonne
   restano **interlacciate** (ordine di lettura errato). In più 154 `�`
   (glifo mancante nel PDF sorgente) e numero pagina `943` trapelato.

**BASSA severità**
6. **`mw15` — titoli con font "spaziato".** 7/10 pagine hanno heading con parole
   spezzate: `**D** **i agnost** **i c Test** **i ng**`, `**Esophageal Var** **i ces**`,
   `**Herpes S** **i mplex**`. Leggibili ma visivamente rotti.
7. **`ne17_0024` — voce numerata promossa a heading.** `### **3** : open mouth
   sufficiently to place` + frase spezzata su due righe.
8. **Numeri di pagina isolati trapelati** (`ox2` 6, `na25_0346`, `ne17_0090`,
   `fe22_4258` "> **1954 Section IV**").
9. **`co26_1260` — heading spurio `# a`** (etichetta figura).
10. **Titoli bold incollati al testo**: `fe22_2615` ("EPIDEMIOLOGY &
    DEMOGRAPHICS **PREVALENCE (IN U.S.):**"), `mo21_0685` ("…heel stick.
    **Abnormal findings**"). 15 occorrenze.

**INFORMATIVI (non difetti)**
- `fig_missing` (24 pagine): quasi sempre un **flowchart/diagramma catturato
  come testo** `>` (contenuto preservato, es. `al17_0344`, `al17_0254`,
  `fe22_1194`) oppure una figura resa come immagine (Fix 7). Accettabile.
- Retention <0.90: quasi sempre **testo interno figura rimosso per design**
  (fix 7) — il contenuto è nell'immagine linkata (`to22_0339`, `to22_0945`,
  `ne17_0269`, `al17_0254`, `ox2_0765/0343`) o **watermark** rimosso
  (`mo21_0099` "https://t.me/ALGRAWANY33"). Fanno eccezione i reali `to22_0762`,
  `to22_0251`, `fe23_0064`.

### 5. Falsi positivi dei detector (da non contare come difetti)
- `caption_fragment` (23): etichette bold di paragrafo (`**Treatment**`,
  `**Definition**`, `**Etiology**`) e voci d'indice (`**estrogen fractions 393**`).
- `glue` (6): artefatti OCR del **maiuscoletto** ("sysTem", "skIlls", "sympToms",
  "retrOgrade", "deLange"), non parole incollate dall'engine.
- `caps_merged` (2): mnemonica in maiuscolo ("Erections POINT AND SHOOT").
- `split_heading`: parte sono coppie legittime heading + etichetta bold.
- `header_leak` `co26_0489`: è il **titolo del Box 2** (contenuto).
- `fig_missing` e `fffd`: vedi sopra (design / sorgente).

### 6. Cosa funziona bene
- Prosa a 1 e 2 colonne: **ordine di lettura corretto**, header/footer e numeri
  di pagina rimossi, spaziatura buona, tabelle per lo più corrette, figure
  renderizzate e linkate accanto alla didascalia.
- `al17` 99.8, `mw15` 100 (a parte i titoli spaziati), `ox16` 100, `fe22` 98,
  `mo21` 98.5: pagine complesse (box, tabelle, figure, riferimenti) rese bene.
- Robustezza operativa: nessun blocco, timeout per pagina mai scattato,
  avanzamento sempre visibile.

### 7. Verdetto (arbitro = agente)
- **Leggibilità complessiva ≈ 92/100.** Ripartizione stimata delle 120 pagine:
  ~85 % corrette e scorrevoli; ~10 % con difetti cosmetici minori (titoli
  spaziati/incollati, markup HTML, numero pagina); ~5 % con problemi seri
  (interlacciamento indice 3 colonne, corruzione da falsa tabella, box perso,
  pagina ruotata).
- **Il markdown è una rappresentazione corretta del PDF nella grande
  maggioranza delle pagine**, ma **non ancora "perfetta"**: i difetti si
  concentrano in **stili editoriali specifici** (`fe23`/`ox16`/`oh2` con header
  colorati → markup; `ox2` indice a 3 colonne; `mw15` titoli spaziati; PDF con
  pagine landscape).
- **Efficienza**: buona (19 pagine/min; ~3 s/pagina), engine dominante.

### 8. Raccomandazioni — Pack 5 (fix mirati, in ordine di impatto)
1. **Normalizzare i tag HTML** (`<mark>`, `<sup>`, `<br>`, `</…>`) in
   `_norm_noise`/`_strip_md_line` **e** in output → elimina la maggior parte
   degli header trapelati (fe23/oh2/ox2/ox16) e il rumore HTML.
2. **Blindare `rebuild_tables`**: non ricostruire quando il "tavolo" è una
   lista/definizione (righe a 1 cella, colonne incoerenti) → fix `to22_0251`.
3. **Pagine ruotate/sideways**: rilevare la direzione del testo (≠ orizzontale)
   ed estrarre con la pagina raddrizzata → fix `fe23_0064`.
4. **Colonne ≥3 / indici**: migliorare `_detect_column_splits` per 3+ colonne e
   per gli indici (`has_index`) → fix interlacciamento `ox2`.
5. **Ricostruire i titoli spaziati**: unire le run di lettere singole nei
   heading (`**D** **i agnost**…` → `**Diagnostic**`) → fix `mw15`.
6. **Non promuovere a heading** le voci di lista numerate e tenere intera la
   frase avvolta → fix `ne17_0024`.
7. **Preservare i box/tabelle laterali** e non inserirli dentro le liste
   (to22_0762); normalizzare `U+FFFD` quando adiacente a spazi/cifre (`ox2`).
8. Ogni fix con chiave di disattivazione in `fix_rules.json` e test dedicati
   (`tests/test_pack5_*.py`), come per i pack precedenti.

### 9. Pagine ispezionate visivamente (pagina ↔ md)
`al17` 14/171/254/344/397/447; `ox2` 959/616; `fe23` 64/191; `to22`
251/339/762/945/1194/1350; `oh2` 313/113; `co26` 489/945; `ne17` 24;
`mw15` 159; `ox16` 244; `mo21` 685; `na25` 436.

### 10. Riproduzione
```bash
cd /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite
.venv/bin/python /tmp/opencode/pack5/select_corpus.py
.venv/bin/python -u /tmp/opencode/pack5/run_batch.py \
    /tmp/opencode/pack5/corpus120.json /tmp/opencode/pack5/out
.venv/bin/python /tmp/opencode/pack5/reanalyze.py /tmp/opencode/pack5/out
.venv/bin/python /tmp/opencode/pack5/report.py /tmp/opencode/pack5/out/results2.jsonl
```


---


---

## §15 — Pack 5: fix implementati + report **prima/dopo** (2026-09-30)

Stesse 120 pagine di §14, ri-eseguite dopo i fix e **ri-validate con un audit
token-per-token** (il punteggio automatico da solo non basta). Esito finale:
**0 pagine peggiorate, 32 migliorate, nessuna perdita di contenuto, nessun md
rotto**. Il percorso è stato iterativo: il primo run "dopo" nascondeva 3
regressioni sottili che l'audit ha trovato e che sono state corrette (vedi §5).

### 1. Fix implementati (tutti disattivabili da `fix_rules.json`)
| # | Fix | Dove | Chiave |
|---|---|---|---|
| A | Tag HTML residui rimossi (`<mark>`,`<sup>`,`<br>`) → non impediscono più la rimozione di header/footer | `_normalize_html_tags`, `_norm_noise`, `_apply_cleanup` | `cleanup_html_tags` |
| B | `U+FFFD` e caratteri di controllo C0 → spazio | `_normalize_replacement_chars` | `cleanup_fffd` |
| C | Titoli a lettere separate ricomposti (`**D** **~~i~~ vert**` → `Diverticulum`) | `_rejoin_spaced_headings` | `cleanup_markdown` |
| D | `_norm_noise` unisce lo spazio davanti a lettera singola ("II n" == "IIn") | `_norm_noise` | `cleanup_markdown` |
| E | Pagine con testo a 90° raddrizzate | `detect_sideways_rotation` + `_extract_pymupdf4llm`/`_apply_engine_on_page` | — |
| F | Rete di sicurezza del riordino: fallback al raw se perde >15% di termini o aggiunge ≥5 righe-frammento | `_apply_reorder_columns` (`_content_retention`, `_fragment_lines`) | `reorder_guard` |
| G | `rebuild_tables` saltato sulle pagine ruotate | `_apply_rebuild_tables` | `rebuild_tables` |
| H | Soft hyphen `U+00AD` rimosso (parola ricomposta: `dom\xad peridone` → `domperidone`) | `_normalize_soft_hyphens` | `cleanup_soft_hyphens` |
| I | Running head `NN CHAPTER n …` riconosciuto anche fuori dalle bande-margini | `_RUNNING_HEAD_RE`, `_is_noise_line` | `cleanup_markdown` |
| J | Didascalie con marcatore a lettera (`TABLE E2 …`) riconosciute e riattaccate | `_TABLE_CAPTION_RE`, `_caption_from_block` | `normalize_table_captions` |
| K | Regione-figura **clippata alla colonna della didascalia** (non "assorbe" header/tabelle) | `_figure_regions` | `link_figures` |
| L | Testo interno figura rimosso solo se **etichetta corta** (≤8 parole) | `_link_figures` | `link_figures` |

Test: `tests/test_pack5_corpus2.py` — **17 test** (13 unit + 4 gold su corpus2),
verdi. Suite completa: **282 test OK** (17 skip). I nuovi passaggi hanno un
**fast-path** (`"<"`/`"~~"`/`"\ufffd"`/`"\u00ad"` assenti → salta), quindi la
guardia `CleanupPerformanceTests` resta sotto soglia.

### 2. Risultati finali (120 pagine, prima → dopo)
| metrica | prima | dopo |
|---|---|---|
| punteggio medio | 96.47 | **98.55** |
| punteggio mediano | 98.00 | **100.00** |
| punteggio minimo | 0.0 | **90.0** |
| pagine "clean" (100) | 39 | **71** |
| pagine con ≥1 difetto | 81 | **49** |
| retention media | 0.959 | 0.960 |
| pagine **peggiorate** | — | **0** |
| pagine **migliorate** | — | **32** |

| difetto (occorrenze) | prima | dopo |
|---|---|---|
| html_markup | 58 | **0** |
| fffd | 154 | **0** |
| header_leak | 21 | **2** |
| page_num_leak | 20 | **6** |
| split_word_heading | 12 | **2** |
| glue | 6 | **1** |
| split_heading | 8 | 7 |
| table_misalign | 12 | 11 |
| caption_fragment | 23 | 22 |
| heading_glued | 15 | 16 |
| fig_missing | 24 | 24 |
| caps_merged | 2 | 2 |

### 3. Pagine migliorate (32) — nessuna peggiorata
`to22_0251` **64→100**; `ox2_0959` **0→97**; `fe23_0064` **93.5→100**; `oh2_0313`
**90.5→100**; `oh2_0422/0600/0682/0734`, `ox16_0058/0244/0394` →100; i 9 `fe23`
con header `Section IIⁿ`; i 7 `mw15` con titoli spaziati; `co26_0241/1260`,
`na25_0346/0547`, `ne17_0090/0269`, `ox2_0064`, e i `ox2` con header
`NN CHAPTER n`.

### 4. Differenze visive confermate (arbitro = agente)
- `fe23_0064`: prima perdeva le note e fondeva le colonne; dopo riproduce
  esattamente la tabella `TABLE 1.2` e le note a–b. ✔
- `to22_0251`: prima righe spurie `- 1/- 2/- 3` e Tabella 6 tagliata; dopo lista
  definizionale integra (A/B/C/S + corpo) e figura linkata. ✔
- `oh2_0313`: prima l'header "8 The nervous sysTem" trapelava (in `<mark>`); dopo
  parte da "Myotonic dystrophy". ✔
- `mw15_0159`: prima `# **Zenker's D** **i vert** **i culum**`; dopo
  `# **Zenker's Diverticulum**`. ✔

### 5. Regressioni trovate dall'audit e corrette (punto chiave)
Il **primo** run "dopo" aveva 0 pagine con punteggio in calo, ma l'audit
token-per-token ha rivelato 3 problemi **mascherati dal punteggio**:
1. **`to22_0251` — perdita di contenuto**: con il fallback al raw, Fix 7
   (figura) eliminava "TABLE 6 … The ABCS" e le etichette A/B/C/S (la regione
   figura veniva da un cluster a tutta larghezza). → fix K (clip alla colonna)
   + L (solo etichette corte): contenuto e figura ora entrambi presenti.
2. **`fe22_1194` — didascalia persa**: `rebuild_tables` sostituiva la tabella e
   `_caption_from_block` non riconosceva il marcatore "TABLE **E2**" (lettera) →
   didascalia non riattaccata. → fix J.
3. **`mw15_0368` — numeri spezzati** ("32 7-3 28") per il fallback troppo
   aggressivo al raw → soglia retention da 0.93 a 0.85 (fallback solo per
   `to22_0762`, dove il riordino perde davvero il box).
Inoltre l'audit ha rivelato **soft hyphen** `U+00AD` in output (7→12 pagine) →
fix H; e i 4 header `NN CHAPTER n` (ox2) → fix I.
**Rete di sicurezza aggiuntiva**: ogni run ora è confrontato a livello di token
prima/dopo (`audit_vs2.py`), così una perdita di contenuto non può più passare
inosservata dietro un punteggio migliore.

### 6. Residui noti (dopo, non regressi)
1. **`ox2` indice a 3 colonne** (6 pagine): resta **interlacciato** + numero di
   pagina isolato. Il testo è **6.4 pt** (< soglia 6.5 di `_body_blocks`).
   Un tentativo di abbassare la soglia sugli indici è stato **revertito** perché
   faceva perdere voci d'indice (meglio interlacciato che con perdita).
2. **`mw15_0263/0293`**: due numeri "spaziati" dal font (`3 0-6 0` = 30-60).
   Cosmetico, contenuto presente.
3. **`ne17_0024`**: voce numerata (`- **3** : …`) promossa a heading.
4. **`fe22_4258`**: `> **1954 Section IV**` (numero/sezione trapelato).
5. **`to22_0762`**: box reso con lettere spaziate ("C hloramphenicol") — cosmetico.
6. **`co26_0489`**: "Subjective Global Assessment" è il titolo del Box 2 → **falso
   positivo** del detector.
7. `fig_missing` (24), `caption_fragment` (22), `heading_glued` (16),
   `split_heading` (7), `table_misalign` (11): in gran parte falsi positivi o
   cosmetici (flowchart-come-testo, etichette bold, intestazioni multi-riga).

### 7. Audit anti-rotture (metodo)
`audit_vs2.py` confronta i md prima/dopo a livello di token e verifica la
struttura (celle vuote adiacenti, heading vuoti, fence, celle di tabella). Esito
sul run finale:
- **token-contenuto rimossi** solo per header/heading rimossi di proposito
  (fe23/oh2), per letter-spacing del box (`to22_0762`) o per fusione di parole
  spezzate (mw15) → **nessuna perdita**.
- **struttura**: unica variazione = `html_tags` 58→0 (miglioramento).
- **pagine peggiorate: 0**.

### 8. Velocità
| fase | prima | dopo |
|---|---|---|
| estrazione (media) | 0.73 s | 1.01 s |
| engine (media) | 2.22 s | 2.32 s |
| wall (120 pagine) | 370.5 s | 418.8 s |

Il rilevamento della rotazione (`get_text("dict")` per pagina) costa ~0.3 s/pagina.

### 9. Verdetto
- **Nessun peggioramento e nessun md rotto**: 0 pagine con punteggio in calo,
  0 perdite di contenuto all'audit, struttura invariata (solo HTML → 0).
- Tutti i difetti **sistematici** risolti: markup HTML 58→0, header trapelati
  21→2, `\ufffd` 154→0, titoli spaziati 12→2, numeri pagina 20→6, glue 6→1,
  soft hyphen 12→1 pagine.
- **Leggibilità stimata: ~92 → ~97/100**; pagine corrette e scorrevoli da ~85% a
  ~95%.
- Restano difetti **localizzati e non regressi** (indice 3 colonne, 2 numeri
  spaziati, una voce-heading, un box letter-spaziato) → candidati a un Pack 6.


---

## §16 — Pack 6: corpus 3 + test 150 pagine (corpus 1+2+3) + arbitraggio (2026-09-30)

### 1. Corpus 3 (lavori scientifici OA, layout vari)
- **58 PDF / 1272 pagine** scaricati e validati (pymupdf), usati nel sorteggio.
- Fonti: **arXiv** (28, campi diversi: cs/math/physics/q-bio/econ), **PLOS** (12,
  riviste diverse), **bioRxiv/medRxiv** (8), **Zenodo** (10). Layout: LaTeX 1/2
  colonne, PLOS 1-colonna con tabelle, preprint, report multilingua.
- Fonti tentate e **non disponibili** in questa sessione (documentato): Europe PMC
  (503), OpenAlex (rate-limit), **CDC MMWR** (403), ECDC/ISS (stub/404),
  Nature/Science/Lancet/Annals (paywall/JS).

### 2. Test random: 150 pagine da corpus 1 + 2 + 3
- **50 pagine per corpus**, 52 PDF distinti, dedup e non-blank.
- Engine Pack 6: **150/150, 0 errori**, wall 636 s (~14 pagine/min), **media
  98.77**, mediana 100, min 84.
- Per corpus: **corpus1 99.3** (libri), **corpus2 97.8** (12 PDF difficili),
  **corpus3 99.2** (lavori OA).
- Difetti (occorrenze): `caption_fragment 25`, `fig_missing 19`, `glue 17`,
  `table_misalign 14`, `split_heading 10`, `page_num_leak 6`, `heading_glued 5`,
  `split_word_heading 5`, `caps_merged 3`, `header_leak 1`.

### 3. Prima/dopo (Pack 5 → Pack 6) sulle stesse 150
- **Identico: 0 migliorate, 0 peggiorate, 0 token persi.** I fix Pack 6 sono
  **mirati** e non scattano su pagine casuali → nessuna regressione.
- **Delta mirato** (Pack 5 → Pack 6), verificato sui bersagli:
  `ne17_0024` heading numerato ✔, `mo21_0685` titolo incollato ✔,
  `mw15_0263/0293` numeri spaziati ✔, `to22_0762` box `C hloramphenicol` ✔.

### 4. Fix Pack 6 (con chiave `fix_rules.json`)
| Fix | Funzione | Chiave |
|---|---|---|
| Voce numerata non più heading + frase ricucita | `_demote_numbered_headings` | `cleanup_numbered_headings` |
| Titolo bold incollato in coda alla frase → riga propria | `_split_trailing_bold_heading` | `cleanup_split_bold_heading` |
| Numeri "spaziati" ("3 0-6 0" → "30-60") | `_despace_numbers` | `cleanup_despace` |
| Lettera iniziale separata nei box (`>` ) | `_despace_blockquote_letters` | `cleanup_despace` |
| Running head "NN CHAPTER n …" fuori dalle bande-margini | `_RUNNING_HEAD_RE` | `cleanup_markdown` |
| Soft hyphen U+00AD e caratteri di controllo | `_normalize_soft_hyphens`/`_normalize_replacement_chars` | `cleanup_soft_hyphens`/`cleanup_fffd` |

Test: `tests/test_pack6_layouts.py` — **12 test** (8 unit + 4 gold). Suite
completa: **294 test OK** (17 skip).

### 5. Arbitraggio visivo pagina↔md (~22 pagine dei 3 corpus)
- **corpus1**: `cu25_1694` tabella ok, foto non linkate; `ce24_4026` tabella
  corretta; `ce24_4347` **caption promossa a heading** (difetto reale) + tabella
  con celle-label; `su19_1826` figura linkata + didascalia ok; `cu25_1729`
  ordine corretto; `co23_1029` figure linkate e ordinate ✔.
- **corpus2**: `fe22_3133` figure non linkate (testo presente); `to22_1519`
  tabella 27 presente; `co26_0522` ordine corretto; `to22_0437` flowchart reso
  come lista (contenuto presente); `fe22_3745` tabella con celle fuse;
  `mw15_0122` **"read in g th is"** (letter-spacing); `ox2_0502` **"fi ltered"**
  (legatura); `fe22_0804` label run-in (`DEFINITION …`); `to22_0762` box
  de-spaziato ✔.
- **corpus3**: `arxiv` pagine math (formule: limite intrinseco del markdown);
  `biorxiv` con **numeri di riga**; `plos` tabella ok; `zenodo` **drop cap**
  russo; `biorxiv` figure linkate ✔.

### 6. Residui noti (dopo Pack 6)
1. **Figure non linkate** (`fig_missing 19`): didascalie tipo "FIG. E3" dove la
   regione-figura non viene trovata → l'immagine non è incorporata (il testo e
   la legenda ci sono). Es. `fe22_3133`, `fe22_1386`.
2. **Legature/spaziature dei font** (`mw15`/`ox2`): "fi ltered" → filtered,
   "read in g th is" (parole spezzate). Cosmetico; la de-spaziatura attuale
   copre numeri e box `>`, non la prosa.
3. **Drop cap** esotici (`zenodo` russo: "А" → heading).
4. **Numeri di riga biorxiv** (`page_num_leak`).
5. **Caption → heading** su `ce24_4347` (appendice).
6. `caption_fragment/heading_glued/split_heading/table_misalign`: in gran parte
   falsi positivi o cosmetici (etichette bold, intestazioni multi-riga).

### 7. Verdetto Pack 6
- **Nessuna regressione** sulle 150; nessuna perdita di contenuto; corpus1/2/3
  omogenei (97.8–99.3, media 98.77).
- Pack 6 risolve 4 classi di difetti mirati (voce-heading, titolo incollato,
  numeri spaziati, box letter-spaziato) **senza effetti collaterali**.
- Leggibilità **ottima sui layout comuni**; residui **localizzati** (figure non
  linkate, ligature dei font, drop cap) → candidati a un Pack 7 mirato.
- **Limite di questa sessione**: arbitraggio visivo su ~22 pagine (campione
  ragionato); le 150 sono coperte automaticamente (checklist + audit token).

### 8. Arbitraggio visivo COMPLETO (150/150 pagine, 15 blocchi da 10)
In 15 blocchi da 10 pagine (uno per volta, con feedback a fine blocco), ho letto
le **150 pagine** (PNG pagina ↔ md) e giudicato correttezza e pulizia del md.
Esito: **~120–125 OK**, **~25–30 ⚠️ localizzati**, **nessuna perdita di testo**,
**nessun problema d'ordine di lettura** rilevato a video.
Categorie degli ⚠️ (nessuna bloccante):
- **figure/flowchart non embeddati** (`fig_missing`): ~15 pagine
  (`ce24` E-FIGURE, `ha22_2227`, `fe22_2997/1386/3737/3133/0340`,
  `fe23_0066`, `arxiv_23240/38133/38179`, `plos_0256464_0003`);
- **flowchart resi come frammenti `>`** (leggibili): `fe22_4186/4219`;
- **caption promossa a heading**: `ce24_4347`;
- **numeri di riga** bioRxiv (cosmetico): `biorxiv_512946/578257`;
- **banner/running head** ripetuto: `zenodo` IJPLA;
- **letter-spacing dei font** ("fi ltered", "read in g th is", cosmetico):
  `mw15`/`ox2`.
Dettaglio per pagina: `/tmp/opencode/pack6/visual_notes.md`.

---

## §17 — Piano Pack 7 (2026-10-01): figure/flowchart embeddati

Dettaglio completo in `HANDOFF-pack7-figure-2026-10-01.md`.

**Obiettivo**: ridurre a ~0 il residuo `fig_missing` (figure/flowchart non
embeddati) emerso dall'arbitraggio Pack 6, senza regressioni; secondari:
caption→heading, numeri di riga bioRxiv, banner ripetuto, ligature dei font.

**Evidenze**: `ce24` E-FIGURE (2003/3039/3369), `ha22_2227`,
`fe22_3133/1386/3737/2997/0340`, `fe23_0066`, `arxiv_23240_0025/38133_0007/38179_0009`,
`plos_0256464_0003`; flowchart `fe22_4186/4219`, `to22_0437`.

**Task**:
| # | Task |
|---|---|
| D1 | Diagnosi `diag_figs.py`: raster vs vettoriale, didascalie non riconosciute |
| D2 | Didascalie `E-FIGURE`, `FIG. E3`, `Fig.`, numeri con lettera/romani |
| D3 | Figure vettoriali/composite: regione da `cluster_drawings` sopra la didascalia |
| D4 | Matching didascalia↔md tollerante (varianti/punteggiatura) |
| D5 | Flowchart: immagine + testo pulito (o lista leggibile) |
| D6 | Residui secondari (caption→heading, line numbers, banner, ligature) |

**Accettazione**: `fig_missing` ~15→~0; audit token 0 perdite; 0 pagine
peggiorate; PNG corretto + didascalia conservata; `tests/test_pack7_figures.py`
+ suite verde. Metodo di verifica: harness Pack 6 (`run_batch`, `reanalyze`,
`audit_vs2`) + arbitraggio visivo a blocchi di 10.
