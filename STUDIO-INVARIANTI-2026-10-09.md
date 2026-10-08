# Invarianti strutturali + fix box/figura — 2026-10-09

Riferimenti: `STUDIO-E2E-VERDETTO-2026-10-08.md`, piano
`piano-implementazione-rilevamento-2026-10-09`.

## 1. Il problema: cecità correlata del verdetto

`su19 p383` (stampata 356) risultava **pulita** (`flow=1.0, order=0`) pur essendo
sbagliata: box "Key Points" a tutta larghezza con sotto-colonne **interlacciato
col corpo**, figura in basso emessa **nel mezzo**, didascalia **staccata**.

Causa radice: **motore e riferimento condividevano lo stesso modello di layout**
(`_column_splits_robust`, `_reference_units`) → quando il modello sbaglia,
sbagliano insieme. Le unità del metro, inoltre, erano solo
`text`/`section-header`/`title`: i **`list-item`** (i punti del box) non erano
misurati.

Diffusione: **45/54** pagine su19 con "Key Points" violate (su19 ha 54 pagine con
"Key Points"); il box è un rettangolo pieno a tutta larghezza
(`get_drawings`, es. `(-21,51,557,198)`).

## 2. Fase 0 — invarianti indipendenti (`tools/invariants.py`)

Proprietà **generali** del markdown rispetto alla geometria, senza enumerare
combinazioni:

| id | invariante | esito |
|---|---|---|
| **I1** | contiguità dei box a tutta larghezza (rettangoli pieni full-width) coi soli blocchi **adiacenti** | cattura `su19 p383` |
| **I3** | monotonia di banda (blocco sotto → dopo, se sovrapposto in x) | complementare |

Integrati in `tools/e2e.py::_checks` come check **`structure`** (bloccante) e
mappati in tassonomia (`text_order`). `tools/measure_invariants.py` misura la
classe su un run.

**Calibrazione (report mode → bloccante):**
- **0 falsi positivi** su 100 pagine casuali di L4 (dopo aver reso I1 robusto:
  solo i blocchi immediatamente sopra/sotto il box, per non contaminarsi con
  box vicini che condividono abbreviazioni, es. `co23 p88`);
- **45/54** pagine su19 "Key Points" violate.

## 3. Fase 1 — fix motore (`ir_layout.py`)

1. **Box a tutta larghezza come banda**: `_full_width_boxes`/`_box_index`;
   `reorder_boxes(..., boxes=...)` emette il contenuto del box **contiguo**, con
   colonne interne rilevate dai **suoi** elementi (`list-item` inclusi).
2. **Banda finale**: in `_order`, gli elementi sotto il fondo delle colonne di
   prosa (figura in basso **non** a tutta larghezza + didascalia) sono emessi
   **dopo** le colonne (non più nel mezzo).
3. **Riferimento box-aware**: `_reference_units` (e `_flow_score`/`_order_report`)
   trattano i box come banda a sé, per non generare falsi positivi d'ordine.

## 4. Esito

- `su19 p383` → **pulita** (`order` 0, `flow` 1.0, `structure` ok, recall 1.0).
- **0 KO di struttura** su tutte le 54 pagine su19 "Key Points".
- **L6** (250 casuali) e **M4** (250 stratificate): **250/250 pulite, 0 difetti**
  con il check `structure` attivo; **0 regressioni** vs L4/M3; **0 cambi engine**.
- Le 15 pagine con md diverso vs L4 sono riordini previsti (es. `su19 p759`:
  figura spostata dal mezzo alla fine) — stessa dimensione, nessuna perdita.

## 5. Nota: il metro non condivide più i buchi del motore

I box sono un **fatto geometrico** (rettangolo pieno full-width), non un'euristica
del motore: usarli sia nel motore sia nel riferimento è legittimo. La garanzia
di indipendenza resta affidata agli **invarianti** (I1/I3), che verificano
proprietà senza replicare l'ordine emesso.

## 6. Residui / prossime classi

- **I2** (adiacenza figura↔didascalia) e **I4** (no duplicazione) non ancora
  formalizzati; il fix della banda finale copre il caso osservato.
- Il diagramma a zone indipendente (pdf2zh v2) resta la Fase 2 del piano, per
  allargare la copertura oltre i box pieni (box senza `fill`, sidebar, ecc.).
