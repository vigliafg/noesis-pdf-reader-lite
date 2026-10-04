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

---

## E.1 — Header spezzati, tabelle affiancate, metrica affinata

### a) Header spezzato a confine di decorazione (`to22 p780`)
Il motore divide "Differences" in `Dif~~ f ~~erences` mentre la **pagina ha un
unico span pulito**. `ir_layout._heading_from_page` ricostruisce l'header dal
testo di pagina **solo se** una parola di pagina corrisponde alla concatenazione
di ≥2 token del motore (allineamento token): decorazioni/markdown non fanno
scattare nulla, il grassetto resta. `Dif f erences` → `Differences`.

### b) Due tabelle affiancate fuse (`fe22 p2685`)
La content map (e la griglia) fondevano TABLE 2 e TABLE 4 in una griglia a 6
colonne. `ir_layout._split_side_by_side_tables` taglia al **secondo** marker
`TABLE/FIG N` della prima riga → due tabelle distinte (`empty_cell` 71→53 sulla
pagina).

### c) Metrica affinata
`glued_digit` catturava anche gli **apici di citazione** (`Nifurtimox1`,
`Echinacea7`), che sono contenuto legittimo: ristretto a `[a-z]{4,}\d+[a-z]`
(digit **dentro** la parola). I 12 eventi di E.0 erano falsi positivi.

### Risultato

| | E.0 (dopo) | E.1 |
|---|---:|---:|
| empty_cell | 417 | **399** |
| glued_digit | 12 (falsi positivi) | **0** |
| spaced_frag_heading | 2 | **1** |
| **totale** | 433 | **402** (5,03/pagina) |

Dal baseline reale (~461): **−59 eventi (−13%)**.

### Test
- `tests/test_ir_tables.py` +1 (split affiancate);
  `tests/test_noise_census.py` +2 (header split, apici non rumore).
- **Suite completa 443 OK** in due metà (< 5 min).

