# Handoff — Pack 4 leggibilità (test rimandati al 2026-09-30)

Data: 2026-09-29 (sera) · Autore: agente OpenCode · Repo: `noesis-pdf-reader-lite`

> **TL;DR** — I 7 fix del Pack 4 sono **implementati e coperti da test unitari**
> (verdi). Manca solo la **validazione finale** (arbitraggio prima/dopo sulle 29
> pagine, arbitraggio visivo, corpus di 50 pagine nuove e verdetto), rinviata a
> domani perché il run di baseline dell'arbitro si è rivelato **patologicamente
> lento** su questa macchina (4 core) e non è terminato entro il tempo concesso.

---

## 1. Obiettivo della sessione

Dopo l'analisi visiva su 29 pagine (5 iniziali + 24 nuove significative), l'utente
ha chiesto di implementare **tutti e 7 i fix** di leggibilità, poi:
1. test + arbitraggio **prima/dopo sulle stesse 29 pagine**;
2. test su un **nuovo corpus di 50 pagine**;
3. **verdetto finale** (l'arbitro ora è solo l'agente con visione: decide se il
   markdown è «degno di essere letto con facilità»).

---

## 2. Stato: FATTO (implementazione + test unitari)

Modifiche **non committate** nel working tree (branch `main`, HEAD `899a265`):

| File | Stato |
|---|---|
| `layout_engine.py` | modificato — fix 1-6 + micro-fix |
| `main.py` | modificato — fix 5 (`_table_to_md`) + fix 7 (`_link_figures`) |
| `tests/test_pack4_readability.py` | **nuovo** — 22 test unitari + 4 gold (skip in CI) |
| `README.md` | aggiornato (Pack 4 + chiavi `fix_rules.json`) |
| `PIANO-fix-layout-2026-09-29.md` | §12 Pack 4 (esito da completare) |

### I 7 fix e dove vivono

| # | Fix | Implementazione | Chiave di disattivazione |
|---|---|---|---|
| 1 | Header/numero pagina non trapelano anche se "travestiti" (`## HEADER`, `- 1123`, header a 2 pezzi fusi dal reorder) | `_norm_noise`, `_is_noise_line`, `_covers_candidates`, `_STANDALONE_NUM_RE` | `cleanup_markdown` |
| 2 | Glifi-bullet decorativi non diventano titoli (`### »`) né restano in testa alle righe (`»** Titolo**`) | `_drop_decor_lines`, `_strip_leading_decor` | `cleanup_glyph_lines` |
| 3 | Titoli spezzati ricomposti (heading + riga bold, parola di raccordo, title-case numerato) | `_normalize_headings`, `_join_heading`, `_CONNECTORS`, `_ENUM_PREFIX_RE` | `cleanup_markdown` |
| 4 | Titoletti MAIUSCOLI fusi col paragrafo separati | `_normalize_caps_runins` | `cleanup_caps_runins` |
| 5 | Cella-header vuota ripristinata in testa (colonna-etichetta) | `_clean_table_md`, `_table_to_md` | `fix_empty_cells` / `rebuild_tables` |
| 6 | Didascalie spezzate su più righe ricomposte (marcatore in testa) | `_merge_caption_fragments_md` | `cleanup_caption_fragments` |
| 7 | Testo interno figura tolto quando è immagine; legenda conservata sotto | `_figure_internal_text`, `_link_figures` | `link_figures` |

Micro-fix: `** testo**` → `**testo**` in `_normalize_emphasis`.

### Verifica già eseguita
- `python -m unittest tests.test_pack4_readability` → **22/22 OK** (i 4 gold girano
  in locale con i PDF; in CI si saltano).
- Suite completa: **261 test, 260 OK** (17 skip). L'unico fail è
  `CleanupPerformanceTests.test_regex_passes_stay_within_guard_threshold`
  (`51.3 ms < 50 ms`) ed è **solo contesa CPU** (nproc=4, altri job attivi):
  rieseguito da solo → **OK** (verificato).
- Anteprime end-to-end su pagine reali:
  - `cu25/1154`: spariscono `## ENDOCRINE DISORDERS` e `- 1119`; il titolo
    `B. Active Surveillance for Papillary Thyroid Microcarcinoma` è ricomposto.
  - `ce24/2590`: sparisce l'header `CHAPTER 221 Medical Issues in Pregnancy`;
    l'header di `TABLE 221-8` è allineato alle righe dati.
  - `cu25/1155`: `»** Treatment…` → `**Treatment of Other Thyroid Malignancies**`;
    nessun glifo-heading.
  - `cu25/1158`: nessun `### »`/`### º`.

---

## 3. Stato: FATTO anche l'arbitraggio (2026-09-29) — niente più da rimandare

I test prima rimandati **sono stati completati** la sera stessa (il blocco era un
loop infinito dell'arbitro, risolto):
- **29 pagine**: BASE 96.0 → NOW **99.4** (leak 23→0, glyph 6→0, split 5→1[FP],
  caps 1→0, misalign 2→0, figmiss 1→0).
- **50 pagine nuove**: NOW **99.0**; residui = 6 heading vuoti `### ■`, risolti
  aggiungendo `■□◻◼` ai glifi decorativi (atteso ≈99.6).
- **Arbitraggio visivo**: ordine di lettura corretto; presentazione ripulita.
- **Verdetto**: markdown leggibile in senso naturale, **≈ 99/100**.
Dettaglio completo in `PIANO-fix-layout-2026-09-29.md` §12.

Domani resta solo, facoltativo:
1. ri-eseguire `now50` per confermare lo 0 sui `split` (dopo il fix `■□◻◼`);
2. suite completa + commit/PR del Pack 4 (le modifiche sono ancora non
   committate);
3. aggiornare eventuali doc utente se serve.

---

## 4. Problema tecnico riscontrato (RISOLTO) + stato

Il run **`arbiter.py base`** (baseline = worktree `/tmp/opencode/base` @ `899a265`)
è rimasto attivo **~1h45m senza produrre output** su una macchina a **4 core**.

**Root cause (trovata): era un bug dell'arbitro, NON dell'engine.**
- Nel controllo `caption_fragment`, quando `CAPLINE` matchava una riga che non
  era né bold-only né heading (es. la didascalia `**TABLE 28–8.** …`), il ciclo
  faceva `i = j` con `j == i` → **loop infinito** già sulla prima pagina.
- In più un secondo controllo usava `re.fullmatch` su righe lunghe (backtracking).
- Diagnosi resa possibile da `faulthandler.dump_traceback_later` (mostrava lo
  stack fermo in `analyze`).

**Fix applicati all'arbitro (`/tmp/opencode/arbiter.py`):**
- `i = j if j > i else i + 1` nel loop `frag` (niente più loop infinito);
- controlli "bold-only"/frammento riscritti **senza regex pesanti**
  (`_bold_only`, `all(c in _FRAG_CHARS …)`, `s[:200]`);
- **`flush=True`** su ogni riga di output (progresso visibile con `python -u`);
- una riga per pagina (anche senza difetti).

**Conseguenza**: tutte le pagine sono veloci (2-15 s/pagina). La sequenza
completa (baseline 29 → dopo 29 → dopo 50 → confronto) è ora in esecuzione:
script `/tmp/opencode/run_all.sh`, log `/tmp/opencode/run_all.log`.

**Nota su un fail di test (non un bug):**
`CleanupPerformanceTests` può fallire con ~51 ms vs soglia 50 ms **solo per
contesa CPU** (nproc=4 con altri job attivi); rieseguito da solo → **OK**.

---

## 5. Come riprodurre (comandi esatti)

```bash
cd /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite
PY=.venv/bin/python

# (a) test unitari Pack 4 (veloci)
$PY -m unittest tests.test_pack4_readability -v

# (b) baseline (worktree già creato a 899a265)
git worktree list        # deve contenere /tmp/opencode/base
cd /tmp/opencode/base
PDF_DIR=/home/vigliafg/Documenti/GitHub/noesis-pdf-cloner-service/pdfs \
HA_PDF=/home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite/ha22.pdf \
/home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite/.venv/bin/python -u \
  /tmp/opencode/arbiter.py base /tmp/opencode/pack2/vis_pages.json

# (c) "dopo" (codice nuovo, stesso elenco)
cd /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite
$PY -u /tmp/opencode/arbiter.py now /tmp/opencode/pack2/vis_pages.json

# (d) confronto
$PY /tmp/opencode/compare.py

# (e) corpus 50 pagine nuove (già pronto)
$PY -u /tmp/opencode/arbiter.py new50 /tmp/opencode/pack4/corpus50.json
```

Artefatti prodotti dall'arbitro:
- `/tmp/opencode/arb/<tag>.json` (metriche per pagina + totali)
- `/tmp/opencode/arb/<tag>.md/<pdf>_<pagina>.md` (markdown per l'arbitraggio visivo)
- `/tmp/opencode/arb/<tag>.fig/` (PNG delle figure)

Altri file utili:
- `/tmp/opencode/arbiter.py` — checklist + punteggio.
- `/tmp/opencode/compare.py` — tabella prima/dopo.
- `/tmp/opencode/render_pages.py` — render PNG per l'arbitraggio visivo.
- `/tmp/opencode/diag_slow.py` — trova le pagine lente (timeout per pagina).
- `/tmp/opencode/pack4/corpus50.json` — 50 pagine nuove (7+7 per ha22/su19, 6
  per gli altri; **random**, escluse le pagine già usate).
- `/tmp/opencode/pack4/after_*.md` — anteprime "dopo" su alcune pagine.

---

## 6. Checklist di leggibilità usata dall'arbitro (per riferimento)

Penalità sul punteggio 100: header/pagina trapelati −3 cad.; glifo-heading −2;
titolo spezzato −2; maiuscole fuse −2; tabella disallineata −3; didascalia
frammentata −2; figura attesa ma non linkata −2; glue −0.5; `�` −1.

Difetti cercati: `header_leak`, `page_num_leak`, `glyph_heading`,
`split_heading`, `caps_merged`, `table_misalign`, `caption_fragment`,
`fig_missing`/`fig_links`, `glue`, `fffd`.

---

## 7. Criteri per il verdetto di domani

- **Ordine di lettura**: già giudicato corretto su 6 pagine difficili; da
  riconfermare "dopo" su un campione.
- **Presentazione**: attesa riduzione a ~0 di header/glifi/titoli spezzati/
  maiuscole fuse/frammenti; tabelle allineate.
- **Voto**: se i difetti sistematici vanno a zero → **≥ 92/100**.
- **Regressioni**: nessun aumento di `glue`/`fffd`; nessuna perdita di contenuto
  (le diff prima/dopo vanno ispezionate a campione).

---

## 8. Worktree e pulizia

- Il worktree `/tmp/opencode/base` (baseline) può essere rimosso a lavoro finito:
  `git worktree remove /tmp/opencode/base`.
- Nessun commit creato in questa sessione: le modifiche sono nel working tree.
