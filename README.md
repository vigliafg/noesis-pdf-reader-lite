# noesis-pdf-reader-lite

Versione **semplificata/purificata** di Noesis PDF Reader: un solo motore di
rendering (**PyMuPDF**), un solo motore di estrazione (**PyMuPDF4LLM**) e
l'**engine adattativo dei fix di layout** sempre attivo (profilo → piano →
pipeline, `layout_engine.py`). Nessun dropdown a runtime.

## Cosa include

- Vista affiancata: pagina renderizzata (sinistra) + testo estratto in
  markdown (destra).
- Engine adattativo sempre attivo (riordino colonne, tabelle, box, legende,
  de-duplicazione titoli, spaziature) — il piano viene scelto automaticamente
  per ogni pagina.
- Navigazione (prec/succ, spin, zoom), indice (TOC), toggle Markdown.
- Tab testo: Originale / Traduzione / 🖼️ Immagini. L'Originale mostra un
  unico testo: l'output del motore adattativo (auto) oppure, quando ci sono
  zone manuali, il risultato ricostruito da esse; la tab di traduzione
  (bandiera + nome della lingua di destinazione scelta, es. "🇫🇷 Français")
  traduce la versione mostrata nella lingua impostata da ⚙️ Impostazioni
  (traduzione rinviata a dopo una pausa di 600 ms durante il disegno delle
  zone). Cache su disco per (pagina, lingua di destinazione).
- Estrazione immagini: selezione a mouse di una zona (🖱️ Seleziona zona) che
  salva la figura ritagliata nella tab 🖼️ Immagini.
- Esclusione manuale di zone (🚫 Escludi zona): header, footer, immagini,
  didascalie… il motore adattativo riordina il testo rimanente. È aggiuntiva
  al sistema automatico (che resta il default). Se la zona disegnata contiene
  un'immagine, la stessa trascinata la estrae anche nella tab 🖼️ Immagini
  (escludi + estrai in un solo gesto).
- Inclusione manuale di zone (🟩 Includi zona): i box verdi numerati (1, 2,
  3…) definiscono l'ordine di lettura. Il testo viene ricostruito seguendo
  la numerazione; ciò che è fuori dai box verdi viene scartato (whitelist).
  Un box verde = una colonna/regione. Rosso e verde compongono: il rosso
  toglie il rumore, il verde ordina; dove si sovrappongono vince il rosso.
- 🧹 Reset zone: rimuove tutte le zone (rosse e verdi) della pagina corrente.
- ⚙️ Impostazioni (menu in cima a destra): lingua UI (it/en/fr/de/es),
  lingua del documento (origine, default "auto") e lingua della traduzione
  (destinazione), più le preferenze: zoom di avvio, rendering Markdown,
  header di estrazione, dimensione font del testo, "riprendi dall'ultima
  pagina" (per documento) e "ricorda l'ultima tab". Il menu è mostrato nella
  lingua UI scelta, con anteprima dal vivo dentro il dialogo. Tutto è salvato
  in `config.json` nella cartella dati dell'app (creato al primo avvio con la
  lingua dell'OS o italiano) e persiste tra gli aggiornamenti. Cambia solo il
  "chrome" UI: il testo estratto del PDF resta nella lingua originale del
  documento.

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
it/en/fr/de/es, 14 sezioni: features, uso, installazione/disinstallazione per
piattaforma, disclaimer uso lecito, scorciatoie e FAQ) pubblicato su
**GitHub Pages** dal workflow `.github/workflows/pages.yml` a ogni push su
`main`:

```text
https://vigliafg.github.io/noesis-pdf-reader-lite/help/
```

Per modificare la guida si edita `docs/help/<lingua>/index.html` (CSS e JS
condivisi in `docs/help/css/` e `docs/help/js/`); la pubblicazione è
automatica al push. Il documento tecnico interno `docs/PDF-reflow-tecnica.md`
resta nel repo ma non viene pubblicato sul sito.

## Test

```bash
.venv/bin/python -m unittest discover -s tests -v
```

> I test di regressione sui PDF reali puntano al repo padre
> (`../noesis-pdf-reader/`); vengono saltati automaticamente se i file non
> sono presenti.

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

