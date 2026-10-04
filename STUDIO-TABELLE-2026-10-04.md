# Studio Tabelle — Stadio C · 2026-10-04

> Metodo: fix + test + **advisor = agente** (arbitro visivo). Diagnosi con
> `tools/table_diag.py` (nuovo). Artefatti: `/tmp/opencode/table_diag/`,
> `/tmp/opencode/proof/`.

## C.0 — Diagnosi strumentata

`tools/table_diag.py` esporta, per pagina, la **matrice** di `page.find_tables`
(`{lines, lines_strict, text} × {use_layout True/False} × {union, refine}`),
le griglie GNN raw (`h_lines`/`v_lines`), le tabelle della content map e la
nostra griglia a copertura parole, con word-recall.

### Risultati chiave

1. **Le tre strategie di `find_tables` NON sono la leva** per queste tabelle
   mediche: recall 0.15–0.64, mentre la **nostra griglia a copertura** dà 1.0.
2. **`ox16 p506` e `ha22 p231` sono già corretti**: il “recall 0.14” dello studio
   a 60 pagine era **pre-fix** (risolto dalle Fasi 4/4b precedenti). `ox16 p506`
   rende una tabella 2 colonne completa.
3. La **griglia raw del GNN non è verità** sul numero di colonne: su `ha22 p231`
   dice 3 (conta i bullet) mentre le colonne reali sono 2; su `ox16 p506` dice 3
   ma le colonne reali sono 2.
4. **Difetto reale trovato**: `fe22 p1101` (4 colonne, celle lunghe) →
   celle **disallineate**. Causa: la **content map è perfetta**, ma il nostro
   selettore sceglieva la griglia a copertura perché il word-recall era falsato
   dal **titolo spezzato tra celle** della content map.

## C.1 — Fix: fidarsi della content map salvo che sia rotta

In `ir_layout.build_markdown`, ramo `table` non-single-col: la griglia a
copertura si usa **solo se la content map è davvero rotta** (`base_r < 0.90`),
non se è solo imperfetta. Prima: `grid_r > base_r + 0.02` (falsato dai `<br>`
e dal titolo spezzato).

| pagina | base recall | grid recall | prima | dopo |
|---|---|---|---|---|
| fe22 p1101 | 0.962 | 1.000 | **griglia** (disallinea) | **content map** (allineata) |
| ha22 p231 | 0.784 | 1.000 | griglia | griglia (invariato) |
| ha22 p1977 (sx) | 1.000 | 1.000 | content map | content map (invariato) |

### Prova (advisor = agente)

`fe22 p1101`, **prima**:
```
| Feature | Idarucizumab | Andexanet Alfa Ciraparantag |  |
| Structure | Humanized antibody fragment | Recombinant human Synthetic, small cationic |  |
```
**dopo**:
```
| **Feature** | **Idarucizumab** | **Andexanet Alfa** | **Ciraparantag** |
| Structure | Humanized antibody fragment | Recombinant human<br>factor Xa variant | Synthetic, small cationic<br>molecule |
```
Le 4 colonne sono **allineate**; “Andexanet Alfa” e “Ciraparantag” separate.

## Test (tutti < 5 min)

- Nuovo `tests/test_ir_tables.py` (2 test).
- 40 test mirati (tabelle + ordine + pack + pipeline + golden): **OK, 73 s**.
- Regressione corpus pinnata: **OK, 21 s**.

## Cosa resta nello Stadio C (minore, non grave)

- Titolo tabella spezzato tra celle (`ha22 p231`: “Differential Diagnosis | of Dementia”).
- Intestazioni di sezione non **spanning** (`ha22 p231`, `ha22 p1977`).
- Tabelle a **1 colonna** rese come testo semplice (`ha22 p1977` TABLE 257-5).
- Artefatti di bleed nelle celle (`ox16 p506`: “…cause i”, “…occurring. i”).
- Recupero di tabelle **scartate** dal motore: i casi trovati (`ce24 p2480`)
  sono artefatti 1×1 degeneri, non contenuto reale → basso valore.
