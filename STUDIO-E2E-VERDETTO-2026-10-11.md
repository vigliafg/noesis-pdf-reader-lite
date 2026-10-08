# VERDETTO E2E — 2026-10-11 (gate di conferma 2×250, Fase 5)

Aggiorna `STUDIO-E2E-VERDETTO-2026-10-09.md`. Due run **nuovi held-out**
(seed nuovi), `--mode auto --via-app`, pagine mai usate nelle validazioni
precedenti.

Riferimenti: `STUDIO-FASE3-REGOLA-TABELLA-RASTER-2026-10-10.md`,
`STUDIO-INVARIANTI-2026-10-09.md`, piano §Fase 5.

## 1. Esecuzione

| run | seed | pagine | dir |
|---|---|---|---|
| **N** | `20261022` | 250 (100+100+50) | `~/.local/share/opencode/e2e250n` |
| **P** | `20261023` | 250 (100+100+50) | `~/.local/share/opencode/e2e250p` |

## 2. Esito automatico

| run | pagine pulite | difetti reali (auto) | tipi |
|---|---|---|---|
| **N** | 248/250 | 3 | `text_order` 2, `figure_missing` 1 |
| **P** | 245/250 | 5 | `text_order` 2, `text_missing` 3 |

**Totale: 7 pagine distinte segnalate su 500.**

## 3. Arbitraggio (difetti reali vs falsi positivi)

| pagina | tipo | dettaglio | giudizio |
|---|---|---|---|
| `ce24 p480` | order | **inversioni 124**, flow 0.46, `order_extra 204` | **REALE — grave**: pagina-tabella complessa (`TABLE 46-2`, celle unite e sotto-sezioni "WHY?/HOW TO USE?/…") con ordine stravolto |
| `fe22 p1038` | order | inversioni 13, fuori-ordine 5 | **REALE**: colonne interlacciate (testo mescolato) |
| `su19 p1050` | figure | 3 figure attese, **1 figura vuota** | **REALE (minore)**: un'immagine resa vuota |
| `arxiv_2609.37412 p30` | text | recall 0.67 (render 0.94) | **REALE (nota)**: pagina formula, prosa persa (classe già nota) |
| `mw15 p166` | text | recall 0.90, engine **`current`** | **NON-IR**: la pipeline è `current` (fallback), non IR |
| `ce24 p1417` | order | inversioni 4, fuori-ordine 1 | **probabile FP/minore** (2 colonne) |
| `zenodo_21644463 p3` | text | recall_pdf 0.50, **recall_render 1.0** | **probabile FP** (riferimento/metrica; il render è completo) |

**Difetti reali stimati: ~4** (1 grave, 2 medi, 1 noto). Il resto è FP o non-IR.

## 4. VERDETTO

**Il gate NON è superato.** Su **500 pagine nuove** emergono classi di difetto
che i run L/M (semi precedenti) non avevano mostrato:

1. **Ordine su tabelle complesse** (`ce24 p480`): la classe più grave, non coperta
   dagli invarianti I1/I3 (non è un box a tutta larghezza né una banda semplice).
2. **Interlacciamento di colonne residue** (`fe22 p1038`).
3. **Figura resa vuota** (`su19 p1050`).
4. **Pagine formula** (`arxiv p30`): già nota.

Conseguenze:

- **Merge su `main` NON consigliato** allo stato attuale.
- Il criterio "0 difetti su 500 pagine" era **dipendente dal campione**: serve
  copertura di **classi** (tabelle complesse, box-liste), non solo volume.
- I nuovi casi vanno aggiunti alla **classe** di lavoro (regola deterministica +
  test), non patchati per pagina.

## 5. Prossimi passi

- **Nuova classe "tabella complessa"** (`ce24 p480`): capire la struttura
  (sezioni interne + celle unite) e progettare l'ordine deterministico; test.
- **Interlacciamento colonne** (`fe22 p1038`): rafforzare il rilevatore colonne /
  la banda.
- **Figura vuota** (`su19 p1050`): perché un'immagine resa è vuota (regione
  figura degenere?).
- **Formula** (`arxiv`): recupero prosa.
- Promuovere gli invarianti che catturano queste classi (nuovo **I6 — ordine
  dentro tabelle a sezioni**?) dopo calibrazione.
