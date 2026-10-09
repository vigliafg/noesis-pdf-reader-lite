# Studio IR 150 — note per blocco (deadline 15:30)

## Blocco 1 — 15 pagine, 7 PDF
- current: 6.20 s/pag | recall_pdf 0.977 | recall_render 1.000 | order 0.952 (87% piene)
- ir:      1.25 s/pag | recall_pdf 0.978 | recall_render 0.998 | order 0.985 (93% piene)
- Arbitraggio visivo:
  - ce24 p212 (Bioterrorism, FIG 19-1 + 2 col): **IR corretto** (col-sx tutta poi col-dx);
    **current interlacciato** (sx → dx → torna a sx). IR vince l'ordine. ✅
  - pa23 p915: **indice** 3 colonne → **IR debole**: 9 righe (voci fuse) vs current 56 righe;
    recall ir 0.962 vs 0.996. Debolezza IR su indici/liste dense.
  - ce24 p3201: pagina quasi vuota (abstract?) → entrambe deboli (recall 0.83/0.92).
  - co23 p1557: tabella grande → IR la rende come tabella markdown.
- Tempo: IR ~5x più veloce in ogni pagina.

## Blocco 2 — 15 pagine, 7 PDF
- current: 11.21 s/pag | recall_pdf 0.950 | order 0.957
- ir:      2.09 s/pag | recall_pdf 0.953 | order 0.979
- Arbitraggio:
  - co23 p1386 (grafici WHO a pagina intera): recall basso **fisiologico** (testo = etichette
    dentro i grafici). IR incorpora le figure + didascalie; current 2 fig + rumore. OK.
  - pa19 p922: current order 0.67 -> ir 1.00 (IR vince ordine).
  - ha22 p3638: current 73.6 s vs ir 9.7 s (IR enormemente più veloce su pagina pesante).

## Blocco 3 — 15 pagine, 6 PDF
- current: 6.64 s/pag | recall_pdf 0.988 | order 1.000
- ir:      1.29 s/pag | recall_pdf 0.983 | order 1.000
- Arbitraggio:
  - ha22 p1456 (pag.1415): FIG 183-2 (4 foto) in fondo -> **current 0 figure (persa),
    IR 2 figure (recuperate)**. Guadagno P0 di IR. ✅
  - co23 p281: 2 immagini raster -> **entrambe** non le emettono (IR non perfetto ovunque).
- Blocco pulito: ordine 100% per entrambe.

## Blocco 4 — 15 pagine, 12 PDF (corpus2)
- current: 5.69 s/pag | recall_pdf 0.988 | order 1.000
- ir:      0.83 s/pag | recall_pdf 0.985 | order 1.000
- Arbitraggio:
  - to22 p377: c'è un **Figure 1** (flowchart vettoriale) -> **current lo cattura, IR NO**
    (il layout model lo classifica come testo/tabella). **Figure detection complementare**:
    current vince sui flowchart con didascalia "Figure n"; IR vince sui `picture` del modello.
  - cu25 p362: pagina-tabella; entrambe OK.

## Blocco 5 — 15 pagine, 8 PDF (corpus2)
- current: 3.25 s/pag | recall_pdf 0.984 | order 1.000
- ir:      0.70 s/pag | recall_pdf 0.944 | order 1.000
- Arbitraggio (regressi recall IR):
  - ox2 p38: **strutture chimiche** (Fig 1.10) -> IR le rende come 3 immagini (giusto),
    il testo delle formule non va nel md -> recall basso **fisiologico** (artefatto metrica).
  - mw15 p182/187: pagine piccole (~1 KB, handbook), differenze minori di intestazioni.
  - na25 p345: IR recupera figura (304 KB vs 2 KB).

## Blocco 6 — 15 pagine, 9 PDF (corpus3)
- current: 3.51 s/pag | recall_pdf 0.985 | order 0.990
- ir:      0.72 s/pag | recall_pdf 0.901 | order 1.000
- Arbitraggio:
  - ox16 p225: pagina **quasi vuota** ("Authorizations and approvals 203") classificata
    tutta `page-header` -> **IR la scarta (body=0)**. Edge case: IR droppa troppo i page-header.
  - fe22 p4446: **tabella complessa** (Appendix II) -> IR perde/merge celle (recall 0.889).
    Debolezza IR sulle tabelle.
  - fe23 p1236: current order 0.86 -> ir 1.00 (IR vince).
  - al17 p486 / oh2 p645: IR recupera figure (170 KB / 52 KB vs <1 KB).

## Blocco 7 — 15 pagine, 13 PDF (corpus3)
- current: 2.88 s/pag | recall 0.954 | order 0.978
- ir:      0.80 s/pag | recall 0.871 | order 1.000
- Arbitraggio:
  - ox2 p809: IR body=0 (come ox16 p225): **pagine quasi vuote droppate** (page-header). Pattern.
  - biorxiv p8: IR recall 0.766 ma è **artefatto**: IR scarta la riga chrome
    "bioRxiv preprint doi:…" che current emette. Contenuto pari.
  - IR vince ordine ne17 p260 (0.67->1.00) e figure (fe23 p332/412, al17 p187).

## Blocco 8 — 15 pagine, 12 PDF (preprint arxiv/plos/zenodo)
- current: 3.49 s/pag | recall 0.986 | order 0.981
- ir:      0.75 s/pag | recall 0.982 | order 1.000
- Molto vicini; IR vince ordine su arxiv_2609.33753 p11 (0.71->1.00).
