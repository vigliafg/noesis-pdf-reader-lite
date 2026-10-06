# Golden Rule #1 — il flusso di lettura diventa difetto primario (2026-10-06)

> Conseguenza della diagnosi su `corpus1/ha22.pdf` p103–p104: la presenza di
> tutte le parole **non basta** se l'ordine di lettura è alterato. Da ora la
> fedeltà si misura su **contenuto E flusso di lettura**.

## 1. La regola

`REGOLE-TEST.md` (nuovo, fonte unica) definisce la **Golden Rule #1**:

> Fedeltà = contenuto **E** flusso di lettura. Un ordine alterato (colonne/righe
> intrecciate) è un difetto **GRAVE a prescindere dal recall**. L'ordine è una
> condizione **primaria**, valutata insieme e prima di figure e tabelle.

Parametri: `FLOW_MIN = 0,95`; unità = blocchi di prosa in ordine geometrico
colonna-major; punteggio = unità collocabili in ordine crescente / unità trovate.

## 2. La metrica (`tools/verify_pages.py`)

- **`_column_splits_robust`** — rilevatore colonne indipendente dal motore:
  sceglie il confine con **meno blocchi che lo attraversano** e gutter più ampio,
  tollerando i blocchi "a ponte" (titoli centrati). Ricorsivo (2+ colonne).
- **`_reference_units`** — ordine naturale: bande delimitate da blocchi a piena
  larghezza, poi **colonna-major** (sinistra→destra, alto→basso).
- **`_flow_score`** — per ogni unità cerca **tutte** le occorrenze a parola
  intera delle prime parole nell'md e assegna **greedy crescente** (massimo
  numero di unità collocabili in ordine). `flow = assegnate / trovate`.
  - **Guard**: si applica solo a layout a 2+ colonne con **prosa reale**
    (≥2 blocchi `text` per colonna) → niente falsi positivi su pagine
    figura/sidebar.

## 3. Harness (`tools/e2e.py`, `tools/e2e_blocks.py`)

- `_checks` aggiunge il check **`flow`**; `_flags` produce il flag `flow`;
  `_AUTO_KIND` lo mappa a **`text_order`** (severity **high**).
- `_make_record` salva `flow_score` / `flow_transitions`.
- Verdetto "pagina pulita" = solo se **flow ok** (blocco duro). Baseline E2E con
  tolleranza `TOL_FLOW`.
- `_write_review` chiede all'agente di giudicare **contenuto E flusso**.
- `e2e_blocks.py`: colonna `flow` nel report per blocco.
- Script di arbitrato aggiornati: `audit.py` (stat flow + "flow più bassi"),
  `diff_pages.py` (sezione **ORDINE/FLUSSO** con ordine reale vs atteso),
  `montage.py` (etichetta `flow=` sulle celle a rischio).

## 4. Validazione (250 pagine run D, nuova metrica)

- flow medio **0,983**; **12/250** sotto 0,95 (contro 16 pagine che la vecchia
  metrica segnalava, ma che **non era mai applicata**: l'ordine non entrava mai
  nel verdetto).
- **Falsi positivi noti**: pagine figura/sidebar (`to22 p845/p275`, `fe22 p475`),
  dove i "lati" non sono colonne di prosa. Il guard ne elimina la maggior parte.
- Casi **reali** verificati: `ha22 p1585`, `ha22 p311`, `co26 p452` (falso
  positivo rientrato dopo il fix dell'ancoraggio).

## 5. Fix del motore (`ir_layout._group_figure_blocks`)

**Causa**: la clausola "attraversa una colonna" rendeva **a tutta larghezza** una
figura a **1,5 colonne** (singolo pannello), creando bande che spezzavano la
colonna di testo → righe intrecciate.

**Fix**: a tutta larghezza solo se **davvero larga** (≥0,6·pw) **o** se è una
figura **multi-pannello** che attraversa le colonne.

**Esito**: `ha22 p103` flow **0,56 → 1,00**, `p1585` **0,33 → 1,00**,
`p311` **0,71 → 1,00**. Suite completa **469 OK** (17 skip).

## 6. Test di regressione

- `tests/test_ir_flow.py`: unit test della metrica (ordine corretto → 1,0;
  intrecciato → <0,95; blocco a ponte non nasconde le colonne) + **caso guida
  ha22 p103** (ora colonna-major) + colonna sinistra contigua.
- `tests/test_e2e_harness.py`: flag `flow` su colonne intrecciate, ok su ordine
  corretto.

## 7. Run E

250 pagine **nuove** (seed `20261009`), blocchi da 5, `--mode auto --resume`,
artefatti in `~/.local/share/opencode/e2e250e/`. Valutazione **severissima** con
le nuove regole: l'ordine (flow) è condizione primaria del verdetto.
