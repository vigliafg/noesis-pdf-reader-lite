# Studio Indici — Stadio D.5 · 2026-10-04

> Metodo: fix + test + **advisor = agente** (pagine PNG lette a vista).
> Artefatti in `/tmp/opencode/index/`. Test entro 5 minuti dal lancio.

## Problema

La content map di PyMuPDF4LLM **fonde** le voci d'indice in un unico paragrafo:
`ha22 p4077` passava da ~185 voci (righe fisiche) a **5 righe**. Il gate
`main._ir_gate` rilevava l'indice e **forzava il fallback** alla pipeline
`current`, che è meglio (22–33 righe) ma comunque lontana dalla struttura reale.

Misura (recall parole lunghe = 1.0 in entrambi):

| pagina | righe GT (voci) | IR | current |
|---|---|---|---|
| ha22 p4077 | 217 (185) | 5 | 22 |
| ha22 p4102 | 218 (169) | 4 | 33 |
| na25 p859 | 136 (122) | 4 | 19 |

## Fix — `ir_layout.index_markdown`

Percorso **dedicato** attivato quando la pagina è un indice e **non ha**
tabelle/figure:

1. prende le **righe fisiche** da `page.get_text("dict")`;
2. le ordina per **colonna** (confini da `_column_splits`, sx→dx) e poi per y;
3. ricostruisce lo **stile** (grassetto `**`, corsivo `_`) dagli span;
4. riunisce alla voce precedente le continuazioni di **soli numeri di pagina**
   (es. `2185`);
5. esclude il **chrome** (header/footer) e le etichette di margine.

Il rilevamento `_is_index_page(page, elements)` è robusto: usa il rilevatore
puro sui box e, se la content map ha fuso le voci, ricade sulle righe fisiche.

`main._ir_gate` non forza più il fallback a `current` sugli indici.

## Risultato

| pagina | righe | voci | gate |
|---|---|---|---|
| ha22 p4077 | **214** | 183 | ok |
| ha22 p4102 | **214** | 164 | ok |
| na25 p859 | **134** | 122 | ok |

Verifica a vista (advisor):
- `ha22 p4077` (3 colonne): ordine colonne corretto; passaggio
  `in heart failure, 1917, 1938` → `in hypertrophic cardiomyopathy, 1917`.
- `na25 p859` (2 colonne): output **identico** alla pagina, inclusi i titoli
  `**U**`/`**V**` e le voci `_(Continued)_`.

## Test

- Nuovo `tests/test_ir_index.py` (4 test: rilevamento, voce-per-riga, ordine
  colonne, gate).
- Aggiornato `tests/test_e2e_harness.py`: l'indice ora è attribuito a `ir`
  (prima `current`).
- **Suite completa: 431 OK** (178 + 253), due metà < 5 min.

## Non risolto (minore)

- Continuazioni non numeriche (es. `molar. _See_ ...` + `in MS, 3468, 3767`)
  restano su due righe: la regola di join copre solo i numeri di pagina, per
  non fondere sotto-voci legittime.
