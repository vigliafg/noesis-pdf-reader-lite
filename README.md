# noesis-pdf-reader-lite

Versione **semplificata/purificata** di Noesis PDF Reader: un solo motore di
rendering (**PyMuPDF**), un solo motore di estrazione (**PyMuPDF4LLM**) e
l'**engine adattativo dei fix di layout** sempre attivo (profilo → piano →
pipeline, `layout_engine.py`). Nessun dropdown a runtime.

## Cosa include

- Vista affiancata: pagina renderizzata (sinistra) + testo estratto in
  markdown (destra).
- Engine adattativo sempre attivo (riordino colonne, tabelle, box, legende,
  de-duplicazione titoli, pulizia di header/footer e titoli, spaziature) — il
  piano viene scelto automaticamente per ogni pagina. I fix sono raggruppati in
  "pack": **Pack 1** pulizia markdown (header/footer, heading, liste, corsivi),
  **Pack 2** tabelle (griglia ricostruita da `find_tables`, didascalie
  `**TABLE x**` su riga propria, celle vuote/colonne fantasma rimosse e valori
  numerici riallineati), **Pack 3** figure (testo dentro le figure visibile e,
  quando c'è una didascalia `FIG n`, la figura viene **renderizzata come corpo
  unico + didascalia**: `![figura n](file://…)` e ingresso nella gallery 🖼️).
  In più, passate a **livello documento**: header/footer deduplicati per
  frequenza tra pagine e gerarchia dei titoli allineata all'indice (`get_toc`).
  I box esplicativi sono resi come **liste/paragrafi** (non tabelle a una
  colonna). **Pack 4** leggibilità: header/numeri di pagina non trapelano più
  anche se il markdown li "traveste" (`## HEADER`, `- 1123`), i bullet
  decorativi dei box non diventano titoli (`### »`), i titoli spezzati su due
  righe vengono ricomposti, i titoletti MAIUSCOLI fusi col paragrafo vengono
  separati, la cella-header vuota delle tabelle viene ripristinata in testa, le
  didascalie spezzate su più righe ricomposte e il testo interno alle figure è
  tolto quando la figura diventa un'immagine. **Pack 5** (layout difficili,
  `corpus2`): i tag HTML residui (`<mark>`, `<sup>`, `<br>`) vengono rimossi —
  non impediscono più la rimozione degli header; i `\ufffd` (glifo assente) sono
  normalizzati; i titoli a lettere separate vengono ricomposti; le pagine con
  testo a 90° (landscape) vengono raddrizzate prima dell'estrazione; il riordino
  colonne ripiega sul raw se perderebbe contenuto o produrrebbe righe-frammento.
  **Pack 6** (layout difficili, corpus 3): una voce numerata promossa a heading
  torna paragrafo e la frase avvolta viene ricucita; un titolo bold incollato in
  coda a una frase va su riga propria; i numeri "spaziati" dal font
  (`3 0-6 0` → `30-60`) e la lettera iniziale separata nei box vengono
  ricomposti. Ogni fix è
  attivabile/disattivabile da `fix_rules.json` (chiavi: `cleanup_markdown`,
  `cleanup_glyph_lines`, `cleanup_caps_runins`, `cleanup_caption_fragments`,
  `cleanup_html_tags`, `cleanup_fffd`, `cleanup_soft_hyphens`, `cleanup_despace`,
  `cleanup_numbered_headings`, `cleanup_split_bold_heading`, `reorder_guard`,
  `fix_empty_cells`, `normalize_table_captions`, `link_figures`, `spacing`, …).
- Navigazione (prec/succ, spin), indice (TOC), toggle Markdown; il pannello
  sinistro è sempre adattato alla finestra (lo zoom di lettura è nel reader).
- Tab testo: Originale / Traduzione / 🗂️ Oggetti. L'Originale mostra un
  unico testo: l'output del motore adattativo (auto) oppure, quando ci sono
  zone manuali, il risultato ricostruito da esse; la tab di traduzione
  (bandiera + nome della lingua di destinazione scelta, es. "🇫🇷 Français")
  traduce la versione mostrata nella lingua impostata da ⚙️ Impostazioni
  (traduzione rinviata a dopo una pausa di 600 ms durante il disegno delle
  zone). Cache su disco per (pagina, lingua di destinazione).
- Mini toolbar di ogni finestra di testo: **A− / A+ / ↺** (dimensione),
  **📋** copia, **💾** esporta (`.md`/`.txt`, con le modifiche, senza header) e
  **🔁** ri-estrai (Originale) / ritraduci (lingua); il punto **●** segnala
  modifiche non salvate.
- Cattura di oggetti: dal menu **📸 Cattura ▾** due azioni —
  **📸 Cattura immagine** e **🔤 Cattura e interpreta** (immagine + testo:
  nativo se presente, altrimenti OCR; le tabelle native diventano markdown).
  Tutto finisce nella tab **🗂️ Oggetti**, raggruppato per tipo (immagini /
  tabelle / testi) con filtri, conteggio, **💾 Esporta tutti** e **🗑️ Rimuovi
  tutti**; ogni oggetto ha le azioni del suo tipo (immagine: salva/copia;
  tabella: copia markdown/salva .md; testo: copia testo/salva .md). Se la
  pagina ha zone già disegnate, la cattura è bloccata con l'avviso di fare
  prima 🧹 Reset zone.
- Esclusione manuale di zone (🚫 Escludi zona): header, footer, immagini,
  didascalie… il motore adattativo riordina il testo rimanente. È aggiuntiva
  al sistema automatico (che resta il default). Se la zona disegnata contiene
  un'immagine, la stessa trascinata la estrae anche nella tab 🗂️ Oggetti
  (escludi + estrai in un solo gesto).
- Inclusione manuale di zone (🟩 Includi zona): i box verdi numerati (1, 2,
  3…) definiscono l'ordine di lettura. Il testo viene ricostruito seguendo
  la numerazione; ciò che è fuori dai box verdi viene scartato (whitelist).
  Un box verde = una colonna/regione. Rosso e verde compongono: il rosso
  toglie il rumore, il verde ordina; dove si sovrappongono vince il rosso.
- 🎯 Zone ▾: menu compatto nella mini-toolbar che raccoglie 🚫 Escludi,
  🟩 Includi e 🧹 Reset; il pulsante mostra il modo attivo.
- ▶ Estrai: conclude l'editing delle zone, svuota la cache della pagina
  (estrazione + traduzione, non le figure) e la riesegue secondo le zone
  selezionate; il pulsante è attivo solo quando la pagina ha zone.
- 💾 Esporta batch (o `Ctrl+E`): procedura guidata in 5
  passi per esportare un gruppo di pagine estratte e/o tradotte — pagina
  corrente, intervallo o lista libera (`1,3,7-9`, `all`); contenuto
  originale/traduzione/entrambi con opzione figure; lingue e motore con
  "traduci prima le pagine mancanti"; output come Markdown unico, ZIP di
  pagine singole o cartella di pagine singole. Finestra di progresso
  annullabile con anteprima animata; a fine export le pagine riempiono anche
  le cache dell'app. Rispetta le modifiche salvate e ignora le pagine con zone
  manuali.
- ✓ Azione successiva (FAB): a elaborazione conclusa compare un pulsante
  flottante con **una sola azione** — sulla tab Originale **🌐 Traduci pagina**,
  sulla tab Traduzione **▶ Traduci la successiva** — con glow, riflesso animato
  e punto di notifica. Le azioni locali sono nella mini toolbar; l'export batch
  è nella toolbar in alto.
- 👁 Reader ▾: reader interno non-modale e read-only (navigazione, zoom,
  adatta-larghezza/pagina, rotazione) oppure apertura nel visualizzatore di
  sistema. Usa un documento separato e si apre sulla pagina corrente.
- 🎨 Tema Scuro / Chiaro / Come il sistema (⚙️ Impostazioni → Aspetto),
  applicato a caldo e persistito; i colori vengono dai token di `theme.py`.
- Animazioni: overlay "liquido" durante estrazione/traduzione + indicatore
  animato sulla tab attiva; anteprima animata nella finestra batch.
- ⚙️ Impostazioni (menu in cima a destra): lingua UI (it/en/fr/de/es),
  lingua del documento (origine, default "auto") e lingua della traduzione
  (destinazione); gruppi **Aspetto** (tema), **Avanzate** (svuota cache
  documento, info cache) e **Notifiche** (avviso a fine export); più le
  preferenze: qualità di rendering, rendering Markdown, header di estrazione,
  dimensione font del testo, "riprendi dall'ultima pagina" (per documento) e
  "ricorda l'ultima tab". Il menu è mostrato nella lingua UI scelta, con
  anteprima dal vivo. Tutto è salvato in `config.json` nella cartella dati
  dell'app (creato al primo avvio con la lingua dell'OS o italiano) e persiste
  tra gli aggiornamenti. Cambia solo il "chrome" UI: il testo estratto del PDF
  resta nella lingua originale del documento.

## Installazione (venv dedicato)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py            # oppure: ./run.sh
```

### Avvio

```bash
./run.sh                           # apre la GUI (o harrison2025.pdf se presente)
./run.sh /percorso/file.pdf        # apre direttamente un PDF
```

## Guida online (help)

Il pulsante **❓ Guida** nella toolbar apre il sito di help nel browser di
sistema. Il sito è un insieme di pagine statiche in `docs/help/` (5 lingue:
it/en/fr/de/es, 18 sezioni: features, uso, esportazione batch/azioni/reader/tema,
installazione/disinstallazione per piattaforma, disclaimer uso lecito,
scorciatoie e FAQ) pubblicato su
**GitHub Pages** dal workflow `.github/workflows/pages.yml` a ogni push su
`main`:

```text
https://vigliafg.github.io/noesis-pdf-reader-lite/help/
```

Per modificare la guida si edita `docs/help/<lingua>/index.html` (CSS e JS
condivisi in `docs/help/css/` e `docs/help/js/`); la pubblicazione è
automatica al push.

## Documentazione

- **Migliorie del motore** (indice + cronologia per area, esiti, rimandi):
  [`docs/MIGLIORIE-MOTORE.md`](docs/MIGLIORIE-MOTORE.md).
- **Regole dei test** (Golden Rule #1 — ordine + contenuto):
  [`docs/REGOLE-TEST.md`](docs/REGOLE-TEST.md).
- **Harness di test** (un comando per app + workflow):
  [`docs/HARNESS-TEST.md`](docs/HARNESS-TEST.md).
- **Analisi UI** (FAB, mini toolbar, tab Immagini → oggetti):
  [`docs/ANALISI-UI-AZIONI.md`](docs/ANALISI-UI-AZIONI.md).
- **Base di conoscenza PyMuPDF4LLM**:
  [`docs/PYMUPDF4LLM-KNOWLEDGE-BASE.md`](docs/PYMUPDF4LLM-KNOWLEDGE-BASE.md).
- **Guida utente** (it/en/fr/de/es): `docs/help/`.
- **Studi e handoff**: `STUDIO-*.md`, `HANDOFF-*.md` (indice completo in
  `docs/MIGLIORIE-MOTORE.md` §6).

## Test

**Harness unico** (app + intero workflow: suite completa + pipeline E2E +
arbitrato visivo, headless, senza corpus né rete):

```bash
.venv/bin/python tools/harness.py        # oppure: tools/run-harness.sh
.venv/bin/python tools/harness.py --quick    # solo suite unit (veloce)
```

Esito `0` = tutto verde; report in `/tmp/opencode/harness/harness_report.{json,md}`.
Dettagli e layer in [`docs/HARNESS-TEST.md`](docs/HARNESS-TEST.md).

La sola suite, se serve:

```bash
.venv/bin/python -m unittest discover -s tests -v
```

I test girano su due livelli:

- **unit/sintetici** (pymupdf genera le pagine al volo) e **end-to-end
  sintetici** — inclusi il **workflow completo dell'app**
  (`tests/test_workflow_e2e.py`: apri → estrai → cattura/interpreta → zone →
  export → reader → tema → lingue) e la **pipeline E2E** (`tools/e2e.py`):
  girano **sempre, anche in CI**;
- **gold sui PDF reali**: vengono **saltati** se i file non ci sono. Il corpus
  sta fuori dal repo; puntare `NOESIS_GOLD_PDF_DIR` alla cartella dei PDF
  (default: `../noesis-pdf-cloner-service/pdfs`) oppure lasciare i PDF nella
  radice del progetto.

Ogni push e ogni PR esegue l'harness headless in CI
(`.github/workflows/tests.yml`, `ubuntu-latest` + `QT_QPA_PLATFORM=offscreen`) e
pubblica il report come artifact.

## Build delle release (GitHub Actions)

Le release standalone sono compilate con **PyInstaller** su quattro runner
nativi (PyInstaller non fa cross-compile):

| Target            | Runner            |
| ----------------- | ----------------- |
| Windows x64       | `windows-latest`  |
| Linux x64         | `ubuntu-latest`   |
| macOS x86_64      | `macos-15-intel`  |
| macOS Apple Silicon (arm64) | `macos-15` |

Il workflow `.github/workflows/release.yml`:

- si avvia manualmente dalla scheda **Actions → build-releases → Run workflow**;
- oppure automaticamente al push di un tag `v*` (es. `git tag v1.0.0 && git push --tags`),
  creando una **GitHub Release** con gli artefatti.

Gli artefatti sono: `.exe` su Windows, **AppImage** su Linux, `.dmg` su macOS.
Gli eseguibili macOS non sono firmati: al primo avvio fare click destro →
**Apri** per aggirare Gatekeeper.

### Build locale (opzionale)

```bash
.venv/bin/pip install pyinstaller
.venv/bin/pyinstaller --onefile --windowed --name NoesisPDFReaderLite main.py
```

