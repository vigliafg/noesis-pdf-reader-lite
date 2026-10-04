# Studio Figure/Didascalie/Testo — Stadio D · 2026-10-04

> Metodo: fix + test + **advisor = agente**. Artefatti in `/tmp/opencode/proof/`.
> Tutti i test mirati entro 5 minuti dal lancio.

## D.1 — Ordine figure impilate (`ox2 p384`)

**Diagnosi.** Due figure distinte (Fig. 6.7 sopra, Fig. 6.8 sotto) venivano
fuse in un unico `_figure_block` perché il gap verticale (31 pt) < soglia (40).
L'emissione metteva **tutte le immagini prima di tutte le didascalie**, con i
crediti (`panel`) prima delle didascalie → “Fig. 6.7” dopo il credito p98.

**Fix** (`ir_layout`, ramo `_figure_block`): emissione **per riga** — immagini e
didascalie in ordine di y. I pannelli **affiancati** (stessa riga) restano uniti.

| | ordine emesso |
|---|---|
| prima | [IMG] [IMG] · credito p98 · Fig 6.7 · Fig 6.8 |
| dopo | [IMG] · **Fig 6.7** · credito p98 · [IMG] · **Fig 6.8** |

## D.2 — Didascalia figura nel footer (`ce24 p1775`)

**Diagnosi.** `FIGURE 154-1` era classificata `page-footer` → **scartata** dal
filtro chrome (figura “mancante”).

**Fix**: un chrome che contiene una didascalia di figura viene **recuperato come
`caption`** invece di essere scartato. Ora entrambe `FIGURE 154-1` e
`FIGURE 154-2` sono presenti e ancorate alla figura.

## D.3 — Guardia d'integrità testo vs bleed (`ce24 p1775`)

**Diagnosi.** La `picture` gigante ingloba un paragrafo; la pulizia del bleed
(parole interne alla figura) **perdeva testo**: “…special stains for
infec**complete** ear, nose…” (fusione di due frasi).

**Fix**: se la pulizia del bleed perde **>15%** delle parole lunghe rispetto
all'originale, si tiene il **testo originale**. Il paragrafo ora è integro.

## Non risolto (documentato, non grave)

- `to22 p780`: `Dif f erences` (spazio spurio da split di span con `~~`). La
  de-spaziatura generalizzata è **rischiosa** → rimandata.
- `ce24 p1775`: la `picture` gigante fonde le due figure in **una** immagine;
  bleed residuo `rti‘éCSS` (basso).
- `ha22 p1977`: l'header `PHYSICAL EXAMINATION` era già corretto dal fix
  d'ordine (Stadio B) — nessun intervento necessario.

## Test

- Nuovo `tests/test_ir_figures.py` (3 test: ordine figure impilate + recupero
  didascalia footer).
- **Suite completa: 427 OK** (174 + 253), in **due metà** entrambe < 5 min.
- Regressione corpus pinnata: OK.
