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
