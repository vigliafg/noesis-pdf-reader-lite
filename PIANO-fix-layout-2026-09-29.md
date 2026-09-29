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

## §13 — Piano di domani (2026-09-30): test su 150 pagine nuove

**Obiettivo**: validare l'engine attuale (dopo il merge del Pack 4) su un corpus
**nuovo di 150 pagine** estratte dal corpus degli 8 PDF, con **valutazione
automatica (checklist + punteggio)** e **arbitraggio visivo** fatto dall'agente
(modello con visione): è l'agente a decidere se il markdown è leggibile in senso
naturale.

### Task
| # | Task | Note |
|---|---|---|
| E1 | Generare il corpus di **150 pagine** (random + significative, escluse tutte quelle già usate: corpus2 80, 5 viste, vis_pages 24, corpus50 50) | script `select_corpus150.py`; salvare in `/tmp/opencode/pack5/corpus150.json` |
| E2 | Eseguire l'**arbitro** (`arbiter.py pack150 corpus150.json`) e raccogliere metriche per-difetto + punteggio | usare `python -u`; una riga per pagina |
| E3 | **Arbitraggio visivo** dell'agente su un campione rappresentativo (≈20 pagine: 2 colonne, box, tabelle, figure, indice/riferimenti): render PNG + confronto col markdown | `render_pages.py` |
| E4 | Compilare il **verdetto**: metriche, difetti residui, giudizio di leggibilità (voto 1-100) e accettabilità | sezione nuova §14 |
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
# E1 — genera il corpus
.venv/bin/python /tmp/opencode/select_corpus150.py
# E2 — arbitro sulle 150 pagine (engine attuale)
.venv/bin/python -u /tmp/opencode/arbiter.py pack150 /tmp/opencode/pack5/corpus150.json
# E3 — render del campione per l'arbitraggio visivo
.venv/bin/python /tmp/opencode/render_pages.py /tmp/opencode/vis5 /tmp/opencode/pack5/sample.json
```
Artefatti: `/tmp/opencode/arb/pack150.json`, `/tmp/opencode/arb/pack150.md/`,
`/tmp/opencode/vis5/*.png`.

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

