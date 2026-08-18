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
- Traduzione automatica (Originale / 🇮🇹 Italiano).
- Estrazione immagini: selezione a mouse di una zona (🖱️ Seleziona zona) che
  salva la figura ritagliata nella tab 🖼️ Immagini.

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
