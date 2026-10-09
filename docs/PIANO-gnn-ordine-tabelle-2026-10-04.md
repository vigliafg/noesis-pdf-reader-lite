# Piano — 2026-10-04 · Motore GNN: misura, ordine, tabelle

> Da mettere in pratica domani. Basato sulla **base di conoscenza verificata**
> `~/.opencode/plan/PYMUPDF4LLM-KNOWLEDGE-BASE.md` (v1.28.2) e sul verdetto E2E
> del campione a 53/60 pagine. Stato di partenza: `main @ 7db44d7` (CI verde),
> suite 388 OK, harness E2E operativo.

---

## 0. Fatti accertati (non più assunzioni)

1. **Motore attivo = GNN** (`BoxRFDGNN`, ONNX, CPU), non le euristiche classiche.
   `pymupdf4llm._use_layout == True`; dispatch verificato con strumentazione
   (`parse_document`=1, legacy=0). Modelli in `pymupdf/layout/resources/onnx` (49 MB).
2. Il nostro `ir_layout.py` lavora **a valle del GNN** sui **`page_boxes`**
   (`class`+`bbox`+`pos`) di `to_markdown(page_chunks=True)`.
3. **Ordine di lettura** = euristica post-processing `utils.find_reading_order`
   (strisce/colonne) applicata *prima* di esporre i `page_boxes` → possiamo
   **riordinare noi** i box nel nostro strato IR, senza toccare il modello.
4. **Tabelle**: il GNN predice una **griglia** che può essere **degenere**
   (`ha22 p230` → v=1; `ce24 p2480` → h=0,v=0). Griglia assente/degenere = contenuto perso.
5. **Figure/formule** = classi `picture`/`formula` → immagini (base64). I bleed
   nascono da `utils.clean_pictures` che estende i box col testo intersecante.
6. **OCR**: modello ONNX di decisione (soglia 0.93) + plugin ibridi; qui **Tesseract**.
7. **Marginalia** (`page-header`/`page-footer`) inclusa di default dal motore →
   la esclude il nostro strato IR.

## 1. Obiettivo del ciclo (1 giornata)

Chiudere **Fase 0 (misura)** e **Fase 1 (ordine)**; aprire **Fase 2 (tabelle)** se
resta tempo. Le fasi 3–5 sono fuori scope oggi.

**Definition of Done del ciclo:**
- Fase 0: golden set stratificato CI-safe + proxy deterministici + score di confidenza
  che **correla** con l'advisor sul golden; escalation ≥ 80% dei difetti reali.
- Fase 1: su pagine held-out della classe "colonne/box/liste/refs", i difetti
  **gravi d'ordine → 0**, senza regressioni sulla baseline.
- Fase 2 (se raggiunta): recall tabelle ≥ 0.90 sul golden tabelle; 0 `table_structure` gravi.

## 2. Decisioni bloccate (non si rimettono in discussione)

- **Motore singolo** PyMuPDF4LLM + GNN; **nessuna nuova dipendenza**.
- **Advisor**: discovery + escalation runtime **opt-in** (remoto), mai in CI.
- **Validazione**: campione **stratificato per classe di layout**; "fatto" = miglioramento
  sulla **classe** su pagine **held-out**, non sulla pagina bersaglio.
- **L'utente è l'arbitro**; **mai** auto-sostituzione del deterministico.
- Test **CI-safe** (no rete); baseline aggiornata **deliberatamente**.
- Isolamento su branch/worktree; merge su `main` con `--no-ff`.

---

## FASE 0 — Misura (abilitante) · [primo passo]

### 0.1 Golden set stratificato, CI-safe
- **Classi** (6): `prosa`, `colonne/box-liste`, `tabelle`, `indici`, `equazioni`, `figure`.
- **Dimensione**: ~8 pagine/classe da corpus1/2/3 (≈48 pagine), pescando anche dai
  difetti già noti (vedi `STUDIO-E2E-60-PARZIALE-2026-10-03.md`):
  - ordine: `fe22 p894/p1101/p1806/p2171/p2410`, `pa19 p1025`, `co23 p612`, `su19 p2210`;
  - tabelle: `ha22 p230`, `ce24 p2480`, `cu25 p1362`, `ox16 p506`, `co26 p1575`;
  - indici: `ha22 p4077/p4102`, `al17 p873`, `na25 p859`;
  - figure: `pa19 p366/p1025/p1224`, `ox2 p647`.
- **Processo**: run `tools/e2e.py --mode advisor` sulle pagine → tu arbitri → salva le
  **etichette attese** in `tests/data/golden/<classe>.json` (classi GNN attese, presenza
  tabelle/griglia, ordine atteso, presenza figure/formule).
- **CI**: un test `tests/test_golden_layout.py` confronta l'output dell'IR con le
  etichette **senza rete**. L'advisor serve solo a *scoprire*, non a gate-are la CI.

### 0.2 Proxy deterministici (dalle classi GNN)
Costruire in `ir_layout.py` (o nuovo `layout_proxies.py`) funzioni pure sui `page_boxes`:
- `order_proxy`: anomalie d'ordine (box testo che scavalca colonne; float dentro colonna;
  refs interlacciate) → score 0..1.
- `table_proxy`: qualità griglia (`h/v lines`, row/col count, copertura parole per cella).
- `figure_proxy`: `picture`/`formula` presenti vs immagini embeddate; header/footer leakage.
- `text_proxy`: recall prosa (già nel gate) + frammentazione.
- **Criterio**: i proxy devono *predire* i verdetti advisor sul golden (riportare
  precision/recall; soglia provvisoria da calibrare).

### 0.3 Score di confidenza + soglia di escalation
- `confidence(page) = f(gate, proxies)`; calibrare su golden.
- Obiettivo: catturare **≥80%** dei difetti reali (oggi il gate ne vede ~35%).
- Esporre lo score nel `report.jsonl` dell'harness.

### 0.4 Estensione baseline per-classe
- Estendere `tests/data/e2e_baseline.json` a metriche **per classe**, non solo per pagina.

**Uscita Fase 0**: `tests/data/golden/*.json`, proxy + confidence, baseline per-classe,
test CI-safe verde.

---

## FASE 1 — Ordine di lettura (classe n.1)

### Approccio (no nuove dipendenze)
Il GNN dà box classificati; `find_reading_order` li ordina con strisce/colonne ma
**non gestisce i float**. Nel nostro IR:
1. **Estrarre** i `page_boxes` una sola volta (`to_markdown(page_chunks=True)`) — mai
   rieseguire il GNN (stato process-global con lock).
2. **Segmentazione a colonne** dalle geometrie dei box (x-projection/gap), non fidandosi
   ciecamente dell'ordine del motore.
3. **Gestione esplicita dei float**: `table`, `picture`, `formula` e box-testo con
   sfondo colorato che cadono **dentro** una colonna → ancorati al loro `y0` ma **senza
   spezzare** un blocco di testo.
4. **Liste di riferimenti** (`ref_order`): riconoscere la sequenza numerata e ordinarla
   in lettura sequenziale (colonna sinistra → destra).
5. Riusare/generalizzare gli helper già presenti (`_column_splits`, `_group_figure_blocks`,
   `_is_figure_caption`) invece di aggiungere patch per pagina.

### Task
- [ ] `layout_proxies.order_proxy` (0.2) come metrica di riferimento.
- [ ] Riscrivere l'ordinamento dei box in `ir_layout.py` come funzione
      `reorder_boxes(boxes, page)` testabile in isolamento.
- [ ] Casi guida: `fe22` (liste+box+tabelle), `pa19 p1025` (fusione colonne),
      `co23 p612` (BOX fuori posto), `su19 p2210` (refs).
- [ ] Test unitari sui casi guida + test golden held-out.

### Criterio di uscita
Sulle pagine **held-out** delle classi `colonne/box-liste` e `prosa`:
**0 difetti gravi d'ordine**; nessuna regressione su `tests/data/e2e_baseline.json`.

---

## FASE 2 — Tabelle (se resta tempo)

### Diagnosi
Griglia GNN **presente ma degenere** (`v=1`, `h=0,v=0`). Non basta "c'è/non c'è".

### Approccio
1. **Validare** la griglia: `h/v lines`, row/col count, copertura parole per cella,
   plausibilità (≥2 righe e ≥2 colonne; celle non vuote).
2. Se degenere → **ricostruire** con, in ordine di costo:
   - `page.find_tables(strategy="lines_strict"|"lines"|"text")` (provare strategie);
   - griglia da **copertura parole** (generalizzare `_grid_table_from_page` già in `ir_layout`);
   - `utils.find_virtual_lines` (righe/colonne virtuali da vettori).
3. Mantenere il **gate su parole di pagina** (non `find_tables`) per non degradare.

### Criterio di uscita
Recall tabelle **≥ 0.90** sul golden `tabelle`; **0** `table_structure` gravi.

---

## FASI 3–5 — solo in coda, se avanza tempo
- **3. Indici**: percorso dedicato (rilevamento + estrazione multi-colonna), niente fallback `current`.
- **4. Figure/marginalia**: rilevare immagini dentro i box; marginalia gestita nel motore IR.
- **5. Cosmetica + UI**: item di elenco vuoti spuri; advisor in UI (chiave/modello, cache verdetti, escalation).

---

## Protocollo anti-overfitting (vale sempre)
- Un fix è "fatto" solo se migliora la **classe** su pagine **held-out**.
- Ogni ciclo: `--baseline` → delta **per classe** → aggiornamento baseline **deliberato**.
- L'advisor **non** entra in CI: in CI solo proxy + golden (no rete).
- **Cache** dei verdetti advisor (pagina+hash) per discovery ripetibile.

## Rischi e mitigazioni
| rischio | mitigazione |
|---|---|
| Riordinare a valle non basta | validare presto sul golden; se serve, agire sul modo in cui leggiamo i box (non sul GNN) |
| Doppio passaggio GNN (costo) | una sola `page_chunks`; mai richiamare `get_layout` |
| Griglia ricostruita che peggiora casi buoni | gate su parole di pagina + confronto col golden |
| Confidence non calibrata | calibrazione esplicita su golden prima di usarla a runtime |
| Overfitting sui casi guida | held-out per classe obbligatorio |

## Comandi utili
```bash
# harness su una pagina (advisor)
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/e2e.py PDF --pages N \
    --via-app --mode advisor --pipelines ir --out DIR
# campione multi-corpus stratificato (da aggiungere: --stratified)
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/e2e_sample.py --build \
    --per-corpus 8 --mode advisor --out /tmp/opencode/golden
# suite
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests
```

## File previsti
- **Nuovi**: `layout_proxies.py`, `tests/test_golden_layout.py`, `tests/data/golden/*.json`.
- **Modificati**: `ir_layout.py` (reorder + tabelle), `tools/e2e.py` (score confidenza,
  metriche per classe), `tools/e2e_sample.py` (campionamento stratificato),
  `tests/data/e2e_baseline.json`.

## Primo passo concreto (Fase 0.1)
1. Aggiungere `--stratified` a `tools/e2e_sample.py` (classi di layout).
2. Costruire il golden preliminare pescando dai difetti noti.
3. Run advisor sulle ~48 pagine → arbitraggio → salvare `tests/data/golden/*.json`.
4. Implementare `order_proxy` e misurarne la correlazione con l'advisor.

## Handoff
Al termine: aggiornare `HANDOFF-2026-10-04.md` con stato, delta per classe, e
prossime priorità. Riferimenti: KB (`PYMUPDF4LLM-KNOWLEDGE-BASE.md`),
`STUDIO-E2E-60-PARZIALE-2026-10-03.md`, `HANDOFF-2026-10-03.md`.
