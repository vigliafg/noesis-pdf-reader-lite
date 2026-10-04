# Studio Rumore — Stadio E.0 · 2026-10-04

> Obiettivo: trasformare "sembra più pulito" in un **numero**. Metrica oggettiva,
> deterministica (seed), CI-safe (nessuna rete/advisor). Tool nuovo:
> `tools/noise_census.py`. Artefatti: `/tmp/opencode/noise*/`.

## Metodo

Campione **held-out**: 4 pagine casuali per PDF su `corpus1`+`corpus2`
(20 PDF → **80 pagine**, seed `20261004`). Per ogni pagina: markdown IR
(senza embed figure, per velocità) + `main._cosmetic_ir`, poi conteggio di
pattern-spia **non ambigui**:

`control_char`, `glued_digit` (bleed), `empty_cell`, `table_ragged`,
`orphan_bullet`, `spaced_frag_heading`, `double_space`, `dup_line`,
`stray_hash`, `html_residue`, `ligature_split`.

Report: `noise.json` + `noise.md` con breakdown **per classe** e peggiori offender.

## Baseline (prima delle fix di E.0)

| pattern | eventi | pagine |
|---|---:|---:|
| empty_cell | 417 | 11 |
| glued_digit | 12 | 7 |
| orphan_bullet | 11 | 7 |
| double_space | 2 | 2 |
| spaced_frag_heading | 2 | 2 |
| **totale** | **444** (5,55/pagina) | |

Non ancora metrate: **17** artefatti-legatura (misurati a parte) → **~461** reali.

## Fix di E.0

1. **Item di elenco vuoti** (`layout_engine._drop_orphan_bullets`): `- ` senza
   testo tra due voci reali (ox2 p584: 5→0). Chiamata in `main._cosmetic_ir`.
2. **Legature + spazio spurio** (`ir_layout._expand_ligatures` /
   `_ligature_fixes`): PyMuPDF emette `Deﬁ nition` (legatura `ﬁ` + spazio); la
   content map la espande in `Defi nition`. Si ricostruisce la parola unita
   (`Definition`). La rimozione dello spazio avviene **solo** se il testo
   conteneva davvero una legatura → `staff in` non è toccato.

## Dopo E.0

| pattern | eventi | pagine |
|---|---:|---:|
| empty_cell | 417 | 11 |
| glued_digit | 12 | 7 |
| orphan_bullet | **0** | 0 |
| ligature_split | **0** (17 risolti) | 0 |
| double_space | 2 | 2 |
| spaced_frag_heading | 2 | 2 |
| **totale** | **433** (5,41/pagina) | |

**Riduzione: −28 eventi** (11 bullet + 17 legature).

## Il residuo `empty_cell` non è un difetto generico

417/433 sono celle vuote in **tabelle**, concentrate in 3 pagine patologiche:

| pdf | pag | causa | tipo |
|---|---:|---|---|
| fe23 | 342 | **tabella ruotata 90°** (testo verticale) | caso limite |
| fe22 | 2685 | **due tabelle affiancate** fuse dalla content map | caso limite |
| na25 | 567 | padding del titolo a più colonne | in gran parte legittimo |

Le altre 8 pagine con celle vuote hanno 1–17 eventi ciascuna (padding
strutturale del markdown). Il numero è quindi **informativo**: isola i casi
difficili, non un difetto di massa.

## Efficienza (stessa passata)

- 1 sola passata di layout per pagina; **~1,0 s/pagina** (no figure).
- Con figure: ~1,3 s/pagina medio, ~5× più veloce di `current`.

## Come ripetere

```bash
.venv/bin/python tools/noise_census.py --per-pdf 4 --seed 20261004 \
    --out /tmp/opencode/noise
```

## Resta aperto

- Tabelle **ruotate** (`fe23 p342`) e **affiancate fuse** (`fe22 p2685`).
- `glued_digit` (12): bleed residuo di figure su 7 pagine.
- `to22 p780` (`spaced_frag_heading`): split da `~~`, de-spaziatura rischiosa.
