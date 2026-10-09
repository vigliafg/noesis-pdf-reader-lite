# Studio E2E — campione 10 pagine a blocchi (advisor) · 2026-10-04

> Run: `tools/e2e_blocks.py` (nuovo), blocchi da 2 pagine, **advisor su tutte le
> pagine**, dati parziali per blocco e riprendibilità (`--resume`).
> Artefatti: `/tmp/opencode/e2e10/` (`summary_all.md`, `summary_blocks.md`,
> `defects_all.jsonl`, `verdict.json`, `blocks/block_XX/`, `records_partial.jsonl`).
> Campione: seed `20261004`, 5 pagine/corpus da **corpus1 + corpus2**.

## Metodo

- 10 pagine **casuali** (5 corpus1 + 5 corpus2), spezzate in **5 blocchi da 2**.
- Ogni blocco: `--via-app --mode advisor --pipelines ir`; l'advisor giudica **tutte**
  le pagine (non solo le flaggate).
- Ogni blocco salva i propri dati (`blocks/block_XX/report.jsonl` + `done.json`);
  il cumulativo parziale è in `records_partial.jsonl`. Il run è **riprendibile**.

### Campione

| corpus1 | corpus2 |
|---|---|
| ce24 p1775, co23 p1271, ha22 p1048, ha22 p1977, pa19 p796 | fe22 p443, mo21 p818, ox16 p371, ox2 p384, to22 p780 |

## Risultato complessivo

- **Pagine senza difetti reali: 4/10** (co23 p1271, fe22 p443, pa19 p796, ox16 p371).
- **Difetti reali: 15** · marginalia (escluse): 7.
- Tempi: **estrazione 30 s** (≈3 s/pagina) · **advisor 530 s** (≈53 s/pagina, 10 chiamate).
- Engine: **IR 10/10** (nessun fallback a `current`).

### Difetti per tipo

| tipo | n | | tipo | n |
|---|---|---|---|---|
| `text_missing` | 3 | | `figure_missing` | 1 |
| `figure_caption` | 2 | | `figure_text_bleed` | 1 |
| `text_order` | 2 | | `figure_order` | 1 |
| `table_content` | 2 | | `table_structure` | 1 |
| `header_content` | 2 | | | |

### Per corpus

| corpus | pagine | engine | text ok | fig ok | tbl ok | flag auto | difetti reali |
|---|---|---|---|---|---|---|---|
| corpus1 | 5 | ir=5 | 5/5 | 5/5 | 4/5 | 1 | 10 |
| corpus2 | 5 | ir=5 | 5/5 | 5/5 | 5/5 | 0 | 5 |

## Report per blocco

| blocco | pagina | conf | recall | order | fig | tbl | difetti reali |
|---|---|---|---|---|---|---|---|
| 00 | ce24 p1775 | 0.40 | 0.928 | 1.00 | 1/1 | ok | 4 |
| 00 | co23 p1271 | 0.40 | 0.984 | 1.00 | 0/0 | ok | 0 |
| 01 | ha22 p1048 | 1.00 | 0.935 | 1.00 | 4/6 | ok | 1 |
| 01 | ha22 p1977 | **0.30** | 0.962 | 1.00 | 0/0 | KO | **5** |
| 02 | fe22 p443 | 0.73 | 1.000 | 1.00 | 2/2 | ok | 0 |
| 02 | pa19 p796 | 0.70 | 0.935 | 1.00 | 2/2 | ok | 0 |
| 03 | mo21 p818 | 0.97 | 0.988 | 1.00 | 0/0 | ok | 2 |
| 03 | ox16 p371 | 1.00 | 0.985 | 1.00 | 0/0 | ok | 0 |
| 04 | ox2 p384 | 1.00 | 0.947 | 1.00 | 2/2 | ok | 2 |
| 04 | to22 p780 | 0.97 | 0.973 | 1.00 | 1/2 | ok | 1 |

## Difetti reali (dettaglio advisor)

| pagina | tipo | sev | nota (sintesi) |
|---|---|---|---|
| ce24 p1775 | text_missing | high | paragrafo "Studies for infectious agents…" troncato |
| ce24 p1775 | figure_missing | high | Figura 154-1 (linfonodi) non rappresentata |
| ce24 p1775 | figure_caption | medium | didascalia FIGURE 154-1 incompleta |
| ce24 p1775 | figure_text_bleed | low | artefatto OCR `rti‘éCSS` prima della didascalia |
| ha22 p1048 | text_order | medium | titolo prima della didascalia FIGURE 125-9 |
| ha22 p1977 | table_content | high | celle recall 0.27 |
| ha22 p1977 | table_structure | high | TABLE 257-4/257-5 fuse/interlacciate |
| ha22 p1977 | text_order | high | corpo a 2 colonne letto riga per riga, mescolato alle tabelle |
| ha22 p1977 | header_content | medium | "PHYSICAL EXAMINATION" frammentato e duplicato |
| ha22 p1977 | table_content | medium | frasi di cella spostate nel corpo |
| mo21 p818 | text_missing | low | etichette "PT" iniziali omesse |
| mo21 p818 | text_missing | low | `thyroidblocking` invece di `thyroid-blocking` |
| ox2 p384 | figure_order | medium | marcatori [FIGURA] prima delle didascalie |
| ox2 p384 | figure_caption | medium | didascalia Fig. 6.7 invertita |
| to22 p780 | header_content | low | "Dif f erences" invece di "Differences" |

## Verdetto

- **L'IR non è ancora pronto come default silenzioso**: 6/10 pagine hanno almeno
  un difetto reale; i cluster sono **ordine** (`ha22 p1977`, `ox2 p384`) e
  **tabelle** (`ha22 p1977`), più figure/didascalie (`ce24`, `ox2`) e header
  (`ha22`, `to22`).
- **L'advisor resta indispensabile**: il gate automatico ha flaggato **1 sola**
  pagina (`ha22 p1977`) contro **6** con difetti reali → recall gate ≈ **17%**.
  Su `ce24` il gate segnala tabelle KO mentre l'advisor le giudica **ok**
  (falso positivo del gate: recall celle 0.42 ma contenuto completo).
- **Confidenza/escalation non ancora calibrata**: soglia 0.75 → 5 pagine escalate,
  di cui solo 2 con difetti reali (**recall 2/6 ≈ 33%**, precision 2/5 = 40%).
  Le pagine peggiori sono però correttamente in cima (`ha22 p1977` 0.30,
  `ce24` 0.40). Serve la calibrazione sul golden (Fase 0.1b/0.3).
- **Caso guida d'oro**: `ha22 p1977` = ordine 2 colonne + tabelle multi +
  header frammentato → copre Fase 1 **e** Fase 2.

## Prossimi passi (dal piano)

1. **Fase 0.1b**: arbitraggio advisor sul golden; etichette di qualità; calibrare
   la confidenza per portare l'escalation ≥80% (oggi 33%).
2. **Fase 1**: `ha22 p1977` (ordine riga-per-riga su 2 colonne) e `ox2 p384`
   (ordine figure/didascalie).
3. **Fase 2**: `ha22 p1977` (tabelle fuse/interlacciate, celle recall 0.27).
4. **Fase 4**: didascalie figure (`ce24`, `ox2`) e header frammentati (`ha22`,
   `to22`).
