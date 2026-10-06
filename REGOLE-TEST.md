# REGOLE DEI TEST — Noesis PDF Reader Lite (IR/GNN)

> Fonte unica e vincolante delle regole di verifica. Ogni report, harness e
> arbitrato visivo deve rispettare queste regole. In caso di conflitto, la
> **Golden Rule #1** prevale su tutte le altre.

## Golden Rule #1 — Contenuto **E** flusso di lettura

La fedeltà di un md alla pagina PDF si misura su **due assi inseparabili**:

1. **Contenuto**: tutto il testo della pagina è presente (una volta).
2. **Flusso di lettura**: il testo scorre nell'**ordine naturale di lettura**,
   riga dopo riga, colonna dopo colonna.

**Un ordine di lettura alterato (colonne/righe intrecciate) è un difetto
GRAVE a prescindere dal recall.** La presenza di tutte le parole non sana un
ordine sbagliato. Se le colonne vengono interlacciate, la pagina è
**bocciata**, anche con `recall = 1.0`.

Conseguenze operative:
- L'ordine è una condizione **primaria**: viene valutato **insieme e prima**
  di figure e tabelle.
- L'harness non può dichiarare "pagina pulita" se il flusso è errato.
- L'arbitrato visivo giudica sempre **(a) contenuto** e **(b) flusso**; un
  difetto di flusso è classificato `text_order` con severità **alta**.

## Parametri

| parametro | valore | significato |
|---|---|---|
| `FLOW_MIN` | **0,95** | sotto questa soglia la pagina è difettosa per ordine |
| unità di riferimento | blocchi di prosa | ordine geometrico colonna-major |
| punteggio | **LIS / n** | frazione di unità nella più lunga sottosequenza in ordine corretto (1,0 = perfetto) |
| pagine a 1 colonna | `flow = 1,0` | non applicabile |
| pagine con < 3 unità | `flow = 1,0` | non applicabile |

## Le altre regole

- **R2 — Contenuto completo**: `recall` della prosa ≥ 0,90 (esclusa la prosa
  dentro le figure, che è resa come immagine).
- **R3 — Figure**: le figure attese (`real_pictures`) devono essere
  incorporate; nessuna figura vuota/troppo piccola.
- **R4 — Tabelle**: recall delle celle ≥ 0,85; struttura non garbled.
- **R5 — Marginalia non è difetto**: numero di pagina, testate correnti
  (`CHAPTER n`), fasce laterali (`PART n`), elenchi collaboratori → `marginalia`.
- **R6 — Determinismo/CI**: nessuna rete nei test; l'advisor è opt-in e non
  gira mai in CI; l'arbitro visivo è l'agente, mai un LLM esterno.
- **R7 — Niente regressioni**: ogni fix validato su pagine **tenute fuori**
  dal campione bersaglio; mai sostituire automaticamente l'output
  deterministico.

## Come si applica

- **Harness** (`tools/e2e.py`, `tools/verify_pages.py`): il check `flow` entra
  in `_checks`, produce il flag `flow`, è mappato a `text_order` (severità
  alta) e concorre al verdetto "pagina pulita".
- **Arbitrato visivo** (`diff_pages.py`, `montage.py`): il diff input↔output
  include l'ordine atteso vs reale; per `flow < FLOW_MIN` si produce il
  fianco-a-fianco PDF | md.
- **Regressione** (`tests/test_ir_flow.py`): caso guida `corpus1/ha22.pdf`
  p103 (colonne intrecciate da figura a 1,5 colonne) + pagine fuori campione.

## Casi guida

| pagina | difetto | atteso |
|---|---|---|
| `corpus1/ha22.pdf` p103 (stampata 62) | figura a 1,5 colonne → colonne intrecciate | `flow < FLOW_MIN` prima del fix; `flow ≥ FLOW_MIN` dopo |
| `corpus1/ha22.pdf` p104 (stampata 63) | tabella con testo ruotato → celle frammentate | `table_structure` |
