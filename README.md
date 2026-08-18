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
- Tab testo: Originale / 🇮🇹 Italiano / 🖼️ Immagini. Nell'Originale i bottoni
  "↺ Reset" e "🚫 Applica esclusioni" mostrano la versione automatica oppure
  quella ripulita; "Italiano" traduce la versione mostrata. Cache su disco.
- Estrazione immagini: selezione a mouse di una zona (🖱️ Seleziona zona) che
  salva la figura ritagliata nella tab 🖼️ Immagini.
- Esclusione manuale di zone (🚫 Escludi zona): header, footer, immagini,
  didascalie… il motore adattativo riordina il testo rimanente. È aggiuntiva
  al sistema automatico (che resta il default).

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
  creando una **GitHub Release** con gli artefatti `NoesisPDFReaderLite-*.zip`.

Gli artefatti sono zip che contengono l'eseguibile (`.exe` su Windows, binario
su Linux, `.app` su macOS). Gli eseguibili macOS non sono firmati: al primo
avvio fare click destro → **Apri** per aggirare Gatekeeper.

### Build locale (opzionale)

```bash
.venv/bin/pip install pyinstaller
.venv/bin/pyinstaller --onefile --windowed --name NoesisPDFReaderLite main.py
```

