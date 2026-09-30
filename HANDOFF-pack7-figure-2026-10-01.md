# Handoff — Pack 7 (figure/flowchart embedding) · 2026-10-01

Data: 2026-09-30 (sera) · Autore: agente OpenCode · Repo: `noesis-pdf-reader-lite`
Baseline: `main` @ `b20ee2f` (Pack 6). Stato: **da fare domani**.

> **TL;DR** — Il markdown è leggibile e corretto su tutte le 150 pagine del test
> random (Pack 6), ma il residuo n.1 è il **figure/flowchart non embeddato**:
> la didascalia c'è, l'**immagine no** (o il diagramma resta come frammenti di
> testo). Pack 7 deve ridurre `fig_missing` a ~0 senza regressioni.

---

## 1. Obiettivo di domani
Ridurre/eliminare il residuo **figure non embeddati** (`fig_missing`) emerso
dall'arbitraggio visivo completo (150/150) e, secondariamente, gli altri residui
localizzati. Criterio guida: markdown **leggibile e corretto**; meglio conservare
testo/immagine che perdere contenuto.

## 2. Evidenze (pagine concrete dal test Pack 6)
Artefatti: `/tmp/opencode/pack6/out150` (md/raw/pages/results), corpus
`/tmp/opencode/pack6/corpus3`, ledger visivo `/tmp/opencode/pack6/visual_notes.md`.

**Figure non linkate** (`fig_missing`):
- corpus1: `ce24` E-FIGURE (`2003`, `3039`, `3369`), `ha22_2227`
- corpus2: `fe22_3133`, `fe22_1386`, `fe22_3737`, `fe22_2997`, `fe22_0340`,
  `fe23_0066`
- corpus3: `arxiv_23240_0025`, `arxiv_38133_0007`, `arxiv_38179_0009`,
  `plos_0256464_0003`

**Flowchart resi come frammenti `>`** (leggibili ma frammentati): `fe22_4186`,
`fe22_4219`, `to22_0437`.

## 3. Cause ipotizzate (da verificare con un dump)
1. **Didascalie con lettera/numero**: `_FIGURE_CAPTION_RE` e il matching in
   `_link_figures` potrebbero non riconoscere `E-FIGURE 175-1`, `FIG. E3`,
   `FIG. E9`, `Fig 1.36`, `E-TABLE`-like.
2. **Figure vettoriali/composite**: `page.get_image_info()` non le vede (non
   sono raster) → nessun candidato in `_figure_candidates` → nessuna regione →
   nessun `![]`.
3. **Clip alla colonna (Pack 5)**: `_figure_regions` clippa i candidati alla
   colonna della didascalia; figure a piena larghezza o multi-colonna possono
   essere scartate.
4. **Didascalia non trovata nel md**: `_link_figures` cerca la didascalia nel
   testo; forme diverse (es. `Fig. E3` vs `Figure E3`) non matchano.
5. **Flowchart**: la regione del diagramma non è un'immagine → resta come
   testo (`>` o lista), a volte frammentato.

## 4. Piano di lavoro (D1–D6)
- **D1 — Diagnosi.** Script `diag_figs.py`: per ogni pagina dei 150 con
  `fig_missing` dumpare `page.get_image_info()`, `page.cluster_drawings()`,
  `_figure_candidates`, `_figure_regions`, e il match della didascalia. Capire
  quante figure sono **raster vs vettoriali** e quante didascalie **non
  matchano**. Output: tabella cause.
- **D2 — Didascalie.** Estendere `_FIGURE_CAPTION_RE`/matching a `E-FIGURE`,
  `FIG. E3`, `Fig.`, `E9`, numeri con lettera e romani; normalizzazione robusta
  per il confronto con il md.
- **D3 — Figure vettoriali.** Se il candidato è un cluster di drawings sopra la
  didascalia, usarlo come regione-figura (union, con clip alla colonna) e
  renderizzarlo via `_render_figure` → `![]`.
- **D4 — Matching didascalia↔md.** Rendere la ricerca della didascalia tollerante
  (varianti, punteggiatura, apici/superscript); log dei fallimenti.
- **D5 — Flowchart.** Valutare: (a) rendere il diagramma come immagine **e**
  conservare il testo pulito; oppure (b) normalizzare i frammenti in una lista
  leggibile. Scegliere in base alla leggibilità (arbitraggio visivo).
- **D6 — Residui secondari.** `caption` promossa a heading (`ce24_4347`);
  numeri di riga bioRxiv; banner/running head ripetuto (zenodo IJPLA);
  ligature/spaziature dei font (`mw15`/`ox2`: "fi ltered", "read in g th is").

## 5. Criteri di accettazione
- `fig_missing` sui 150 di Pack 6: da **~15 a ~0** (o casi spiegati).
- **Nessuna regressione**: audit token prima/dopo = 0 token persi; 0 pagine
  peggiorate; ordine di lettura invariato.
- Dove si linka una figura, il PNG è la **regione giusta** e la didascalia resta.
- Test `tests/test_pack7_figures.py` con gold sui casi reali (skip se assenti);
  suite verde.

## 6. Come verificare (riuso harness Pack 6)
```bash
cd /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite
PY=.venv/bin/python
# (a) test sulle 150 pagine già sorteggiate (o nuovo sorteggio)
$PY -u /tmp/opencode/pack5/run_batch.py /tmp/opencode/pack6/corpus150.json /tmp/opencode/pack7/out150
$PY /tmp/opencode/pack5/reanalyze.py /tmp/opencode/pack7/out150
# (b) audit anti-rotture prima/dopo
$PY /tmp/opencode/pack5/audit_vs2.py /tmp/opencode/pack7/out150/md
# (c) arbitraggio visivo a blocchi di 10 (metodo Pack 6)
```
Artefatti: `out150/md/*.md`, `pages/*.png`, `results.jsonl`.
Baseline Pack 6 per il prima/dopo: `/tmp/opencode/pack6/out150`; worktree Pack 5
già pronta in `/tmp/opencode/pack6/base5` (eventualmente aggiornare a `b20ee2f`).

## 7. Vincoli / note
- Macchina a 4 core: **non** lanciare due run pesanti in parallelo (causa flake
  dei test di performance).
- `get_text("dict")`/render sono la parte costosa; la rotazione (Pack 5) e il
  rilevamento figure aggiungono ~0.3–0.7 s/pagina.
- Se una figura non è recuperabile, **non perdere il testo**: è preferibile
  lasciare la didascalia + eventuale testo che rimuovere.
