# Harness di test — app + intero workflow

> **Un solo comando** verifica tutta l'app e l'intero workflow, in automatico,
> headless, **senza corpus e senza rete**, e produce un report con esito
> binario. È lo strumento che l'agente usa per dire "verde" senza ambiguità.

## Comando

```bash
# full: suite completa + pipeline E2E + arbitrato visivo
.venv/bin/python tools/harness.py
# oppure, da qualsiasi directory:
tools/run-harness.sh
```

Esito **0** = tutti i layer verdi, **1** = almeno un layer rosso.
Report in `<out>/harness_report.json` e `<out>/harness_report.md`
(default `--out`: `/tmp/opencode/harness`).

### Opzioni

| opzione | effetto |
|---|---|
| `--quick` | solo il layer `unit` (iterazione veloce) |
| `--module tests.test_workflow_e2e` | esegue **solo** un modulo unittest |
| `--no-visual` | salta l'arbitrato visivo |
| `--corpus` | aggiunge lo smoke su `corpus1` (saltato se assente) |
| `--out DIR` | cartella del report |
| `--timeout N` | timeout per layer (secondi) |
| `--list` | elenca i layer |

## Layer

| layer | cosa verifica | dove |
|---|---|---|
| `unit` | suite `unittest` completa: motore, regressioni, UI, i18n, tema, **workflow E2E** | `tests/` |
| `pipeline` | pipeline di estrazione E2E (`tools/e2e.py --mode auto`) su PDF sintetico: gate testo/figure/tabelle + `report.jsonl` | `tools/e2e.py` |
| `visual` | arbitrato visivo (`--mode visual`): checklist `review/index.md` | `tools/e2e.py` |
| `corpus` | smoke su pagine reali di `corpus1` (opt-in) | `corpus1/` |

## Copertura del workflow

`tests/test_workflow_e2e.py` percorre l'app **dal vivo**, in un unico modulo
headless:

1. apertura PDF + estrazione (thread) + gate export/reader abilitati;
2. navigazione + cache (testo identico al ritorno);
3. figura embedded in galleria;
4. **📸 Cattura immagine** (solo immagine, nessun testo);
5. **🔤 Cattura e interpreta** — testo nativo allegato alla card;
6. cattura di **tabella nativa → markdown**;
7. **zone**: escludi / includi / rosso-prevale-sul-verde / reset;
8. **▶ Estrai** (conclude l'editing, purga la cache e riesegue);
9. toggle Markdown;
10. traduzione (mock, nessuna rete);
11. export pagina (file dialog mockato);
12. **export batch completo** (wizard mockato → thread → file scritto);
13. reader detached sulla pagina corrente;
14. tema chiaro/scuro;
15. impostazioni applicate a caldo (tema, font, markdown);
16. **retranslate** su tutte le 5 lingue UI (it/en/fr/de/es);
17. FAB + overlay di estrazione;
18. screenshot della finestra.

Più i casi limite (`WorkflowEdgeCasesTests`): cattura senza documento, estrazione
con zone senza documento.

## Strumenti di analisi UI

Per analizzare (non solo verificare) la superficie d'azione della finestra
destra — FAB, mini toolbar, card — c'è `tools/ui_audit.py`:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui_audit.py \
    --json /tmp/opencode/ui_audit.json --md /tmp/opencode/ui_audit.md
```

Enumera tab, mini toolbar, voci dei FAB e azioni delle card, e rileva le
sovrapposizioni di etichetta. I risultati confluiscono in
[`docs/ANALISI-UI-AZIONI.md`](ANALISI-UI-AZIONI.md). Il workflow dei FAB è
verificato da `tests/test_ui_actions.py` (entra nel layer `unit`).

## Convenzioni (valgono per ogni test del harness)

- **Headless**: `QT_QPA_PLATFORM=offscreen`; i test si **saltano** se Qt/pymupdf
  non sono disponibili.
- **Isolamento**: `XDG_CONFIG_HOME`/`XDG_DATA_HOME`/`XDG_CACHE_HOME` in una
  cartella temporanea → nessuna interferenza con le impostazioni reali.
- **Niente rete**: la traduzione è mockata; l'help non apre il browser.
- **Niente corpus**: PDF sintetici generati al volo; i casi su corpus reale sono
  opt-in e si saltano se i file mancano (vedi `docs/REGOLE-TEST.md`, R6/R12).
- **Determinismo** (R12): stesso input → stesso output; nessun fix non
  deterministico a runtime.

## Aggiungere copertura

- Nuova funzione UI → un test in `tests/test_workflow_e2e.py` che la guida
  end-to-end (apri → agisci → attendi con `wait_until` → asserisci).
- Nuovo caso di motore/regressione → modulo dedicato in `tests/` (entra da solo
  nel layer `unit`).
- Nuovo scenario di estrazione → parametrizza `tools/e2e.py` e aggiungi il
  layer in `tools/harness.py`.

## CI

`.github/workflows/tests.yml` esegue `python tools/harness.py` su ogni push/PR e
pubblica il report come artifact `harness-report`. "CI verde" = **harness
verde**.
