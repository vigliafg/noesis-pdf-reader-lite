# Analisi — superficie d'azione della finestra destra

> **Oggetto**: bottoni flottanti (FAB), mini toolbar delle tab, doppioni, e
> ristrutturazione della tab Immagini in una raccolta di **oggetti tipizzati**.
>
> **Stato**: ✅ **IMPLEMENTATO** (opzione B, fasi P1–P4). Decisioni utente:
> **B** (separazione netta FAB/mini toolbar), **P1+P2–P4**, tab **"🗂️ Oggetti"**.

## 0. Strumenti (analisi automatica e ripetibile)

| strumento | cosa fa |
|---|---|
| `tools/ui_audit.py` | costruisce la finestra reale (headless) ed **enumera** tab, mini toolbar, CTA dei FAB, azioni delle card per tipo; rileva sovrapposizioni. Output JSON + markdown. |
| `tests/test_ui_actions.py` | verifica il **workflow** dei FAB (comparsa post-estrazione/post-traduzione, CTA, azioni cablate) e la forma dell'inventario. |
| `tools/harness.py` | esegue tutto (i test entrano nel layer `unit`). |

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui_audit.py \
    --json /tmp/opencode/ui_audit.json --md /tmp/opencode/ui_audit.md
.venv/bin/python -m unittest tests.test_ui_actions -v
```

## 1. Inventario finale (dall'audit)

### Tab

| tab | etichetta | contenuto |
|---|---|---|
| `original` | `📄 Originale` | testo editabile + mini toolbar |
| `translated` | `🇮🇹 Italiano` (endonimo) | testo editabile + mini toolbar |
| `images` | `🗂️ Oggetti` | mini toolbar di tab + raccolta di oggetti |

Nella tab bar, a destra: **radio motore** (`Google`, `Microsoft`) + spinner.

### Mini toolbar delle finestre di testo

| bottone | funzione |
|---|---|
| `A−` / `A+` / `↺` | dimensione font della finestra |
| `📋` | copia il testo della finestra |
| `💾` | esporta `.md`/`.txt` della finestra |
| `🔁` | ri-estrai pagina (Originale) / ritraduci (lingua) |
| `●` | modifiche non ancora salvate su disco |

### FAB — CTA unica

| FAB | etichetta | azione |
|---|---|---|
| post-estrazione (`original`) | `🌐 Traduci pagina` | passa alla traduzione della pagina |
| post-traduzione (`translated`) | `▶ Traduci la successiva` | va alla pagina successiva e la traduce |

Il FAB è in basso a destra della finestra attiva, con badge e glow; compare solo
quando la tab è attiva **e** il contenuto è pronto; si riposiziona al resize.

### Tab 🗂️ Oggetti

**Mini toolbar di tab**: filtri `Tutti / Immagini / Tabelle / Testi` · conteggio ·
`💾 Esporta tutti` · `🗑️ Rimuovi tutti`.

**Azioni di card, per tipo**:

| tipo | azioni |
|---|---|
| immagine | `💾 Salva` · `📋 Copia` · `🗑️ Rimuovi` (+ click → zoom) |
| tabella | `📋 Copia markdown` · `💾 Salva .md` · `🗑️ Rimuovi` |
| testo | `📋 Copia testo` · `💾 Salva .md` · `🗑️ Rimuovi` |

## 2. Cosa è cambiato (prima → dopo)

| aspetto | prima | dopo |
|---|---|---|
| **Esporta** | in mini toolbar (`💾`) **e** nel FAB → doppione | solo mini toolbar |
| **Esporta batch** | nel FAB **e** in toolbar principale → doppione | solo toolbar principale |
| **Copia** | solo nel FAB | mini toolbar |
| **Ri-estrai / Ritraduci** | solo nel FAB | mini toolbar |
| **FAB** | menu con 5–6 voci | **CTA unica** (traduci / successiva) |
| **Traduci pagina** | voce di menu del FAB | la CTA del FAB |
| **Tab oggetti** | `🖼️ Immagini`, senza toolbar, azioni miste | `🗂️ Oggetti`, toolbar di tab, azioni **per tipo** |
| **Modello catture** | `_images` + `_capture_text[uri]` | `_captures[uri] = Capture{kind,text,method,source,page}` |
| **Testo vuoto tab** | "Usa 🖱️ Seleziona zona…" (obsoleto) | "Usa 📸 Cattura…" (`objects.empty`) |

## 3. Workflow FAB — correttezza (verificata)

- **post-estrazione**: con la tab *Originale* attiva e testo pronto, la CTA
  *Traduci pagina* è visibile; premendola si passa alla tab lingua e parte la
  traduzione.
- **post-traduzione**: con la tab lingua attiva e traduzione pronta, la CTA
  *Traduci la successiva* è visibile; premendola si va alla pagina successiva e
  la si traduce.
- **visibilità**: dipende da tab attiva + contenuto pronto + `_current_page >= 0`;
  al cambio pagina entrambe vengono nascoste e riaggiornate.
- **riposizionamento** al resize; **cablaggio** verificato dai test.

Test: `tests/test_ui_actions.py` (comparsa, etichette, click→traduzione,
mini-toolbar copia/export/ri-estrai cablate, inventario).

## 4. Modello dati degli oggetti (P2)

```text
Capture = { uri, kind, text, method, source, page }
kind   ∈ { image, table, text }        # table/text da "cattura e interpreta"
method ∈ { none, native, ocr, table }  # da _interpret_region
source ∈ { capture, figure }           # manuale | automatica
```

`_capture_kind(method)` mappa `table→table`, `native|ocr→text`, `none→image`.
Le figure automatiche (data URI) restano `image` (nessun `Capture`).

## 5. Punti aperti (opzionali, non implementati)

- **Persistenza** delle catture: oggi sono in memoria; valutare un sidecar per
  documento (come traduzioni/edit).
- **Esporta tutti**: oggi scrive in una cartella scelta (immagini PNG, testi
  `.md`); si può aggiungere `.zip`.
- **Filtri**: oggi il filtro agisce a livello di tab; valutare un ordinamento
  per pagina o per data.

## 6. Riferimenti

- Piano: `~/.opencode/plan/piano-oggetti-fab-2026-10-10.md`.
- Test: `tests/test_ui_actions.py`, `tests/test_workflow_e2e.py`,
  `tests/test_ui_alignment.py`.
- Harness: `tools/harness.py`, `docs/HARNESS-TEST.md`.
