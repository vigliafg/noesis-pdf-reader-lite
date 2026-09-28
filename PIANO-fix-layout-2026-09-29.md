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
