# Verdetto — PyMuPDF4LLM vs Xberg (arbitro visivo)

Arbitro: l'agente AI (modello visivo), confrontando il PNG di ogni pagina con i
markdown prodotti dai due motori. Corpus: **9 bunch / 92 pagine** — 2 bunch fissi
su `ha22.pdf` (A: 1230–1240 tabelle+figure, B: 2230–2240 due colonne) + 10 pagine
casuali >300 per `ce24`, `co23`, `cu25`, `pa19`, `pa23`, `su18`, `su19`.
Dati grezzi: `out/compare.md`, `out/summary.md`, `out/metrics/`, `out/report.html`.

## 1. Sintesi (una riga)

- **PyMuPDF4LLM**: vince su **fedeltà e reading order** in questo corpus; veloce
  (~0.8–1.3 s/pagina) e leggero (~430 MB RSS). Debolezza: il `layout_engine` che
  ci gira sopra può **danneggiare la spaziatura** su alcune pagine (vedi §7).
- **Xberg+layout**: robusto sul testo semplice ma **fragile quando una tabella/
  diagramma sta accanto al testo** (interleave le colonne, testo illeggibile);
  frammenta paragrafi e liste; **~4–5× più lento** e **3–8× più RAM**. In cambio,
  sa ricostruire alcune tabelle/celle che PyMuPDF4LLM rende male.

## 2. Velocità e RAM (cold, da `out/summary.md`)

| | PyMuPDF4LLM | Xberg (+layout) |
|---|---|---|
| ms/pagina (ammortizzati) | **~0.8–1.3 s** | **~3.4–6.4 s** |
| import backend | ~0.7 s | ~0.13 s (import lazy) |
| picco RSS | **0.41–0.45 GB** | **1.3–3.7 GB** |
| cold vs warm | ~uguali | ~uguali (nessun beneficio di cache misurabile) |

Xberg è **document-level**: una sola chiamata per l'intero ritaglio (la prima
pagina paga tutto). La passata `warm` (cache di contenuto abilitata) non ha dato
guadagni misurabili. Nessun caso di crash/timeout/errore in 92 pagine × 2 motori.

## 3. Fedeltà del testo

- **Glifi matematici**: PyMuPDF4LLM emette **9 `�` (U+FFFD) su 2 pagine** per
  simboli ≥/≤ (pa23/p743 `glucose �126 mg/dL`, ce24/p4210 `( � 4 METs)`).
  Xberg **non mette `�` ma cancella il simbolo** (`glucose 126 mg/dL`, `( 4 METs)`):
  entrambi sbagliano, in modo diverso (uno lo segnala, l'altro lo nasconde).
- **PyMuPDF4LLM**: paragrafi coesi, spazi corretti, de-sillabazione discreta.
- **Xberg**: testo corretto ma **paragrafi spezzati** in molte righe separate
  (hard break a fine riga), es. p2230 e p1232. Su p2240 il testo è
  **illeggibile** perché righe di tabella e prosa sono interlacciate.

## 4. Reading order

- Pagine a **due colonne pulite** (B/p2230): entrambi ordinano bene; PyMuPDF4LLM
  più coeso, Xberg più frammentato.
- Pagine con **box/tabella/diagramma accanto al testo** (A/p1232, B/p2240,
  pa23/p602): **Xberg interleave le colonne** (righe della tabella mischiate alla
  prosa). È il difetto più grave osservato. PyMuPDF4LLM resta in ordine.
- **Sidebar/header di capitolo**: Xberg li emette in fondo e spezzati
  (`**CH** **APTER** **148 Str** **eptococcal Infections**`); PyMuPDF4LLM li
  sopprime meglio (ma emette il numero di pagina stampato).

## 5. Tabelle

- Tabella complessa a tutta larghezza (A/p1230, TABLE 148-1): PyMuPDF4LLM
  ricostruisce header/caption e ordine; Xberg perde la cella "C, G" e mette la
  caption **dopo** la tabella.
- Tabella accanto al testo (A/p1232, B/p2240): **Xberg ingloba le note dentro la
  tabella** e interleave col testo → inutilizzabile. PyMuPDF4LLM produce una
  tabella a una colonna ordinata.
- Xberg a volte **ricostruisce come tabella i label di un diagramma** (pa23/p602):
  struttura in più, ma anche rumore.
- Tabella con celle unite/liste annidate (B/p2240): PyMuPDF4LLM perde la griglia
  ma mantiene l'elenco numerato; Xberg distrugge l'ordine.

## 6. Figure e didascalie

- Entrambi emettono le **didascalie** correttamente.
- PyMuPDF4LLM estrae i testi dentro l'immagine ("picture text") in modo pulito
  (A/p1230 legenda mappa; pa23/p602 label del grafico).
- Xberg a volte unisce male le label (`1.01.3` invece di `1.0 1.3`) e fonde il
  testo delle figure con la didascalia.

## 7. Interazione col `layout_engine` (importante)

Il fix di **riordino colonne** (`reorder_columns`) scatta su quasi tutte le
pagine a 2 colonne per PyMuPDF4LLM. Su **pa23/p301** il risultato **rimuove gli
spazi tra le parole** (`theyincreasewithareductioninobesity`), perdendo ~11% dei
caratteri: il raw era ottimo (ratio 0.99) e l'engine lo peggiora (ratio 0.19).
Xberg, a cui il riordino è **saltato** (forma B), non viene toccato.

→ È un **bug del `layout_engine`**, indipendente da Xberg: va corretto perché è
nel percorso di produzione "lite".

## 8. Quando usare quale

- **Default (tier lite)**: PyMuPDF4LLM — più fedele, più veloce, più leggero.
- **Xberg utile come oracolo/tabella**: pagine con tabelle strutturate o per
  estrarre label di figure; da usare **opt-in e su pagina singola**, con verifica
  che non ci sia interleave di colonne.
- **Da NON fare**: Xberg come motore predefinito su prosa a due colonne con box.

## 9. Raccomandazione operativa

1. **Non integrare Xberg come default**. Su questo corpus Harrison's + 6 volumi
   non batte PyMuPDF4LLM né in qualità né in costo.
2. **Correggere il bug di spaziatura** del `reorder_columns`/`spacing` (priorità
   alta, tocca il prodotto lite).
3. Mantenere Xberg **opt-in** (già fatto, Fase 1) e usarlo al più come **oracolo
   offline** per validare euristiche, o per pagine segnalate `has_tables` con
   griglia PyMuPDF malformata (Fase 2 del piano).
4. Riprovare Xberg con **`LayoutStrategy`/config diverse** e versioni future: la
   fragilità osservata può dipendere dalla nostra config (`strategy="always"` +
   `reading_order=True`).
5. La differenza di velocità (~4–5×) e RAM (3–8×) rende **Marker/MinerU** ancora
   meno giustificabili sul born-digital (Fasi 3/4: da non fare).

## 10. Tabella sintetica per pagina (pagine arbitrate visivamente)

| pagina | layout | fedeltà | ordine | tabelle | figure | verdetto |
|---|---|---|---|---|---|---|
| A/p1230 | tabella larga + mappa | py | py | py | py | **PyMuPDF4LLM** |
| A/p1232 | tabella + 2 colonne | py | **py grande** | **py grande** | py | **PyMuPDF4LLM** |
| B/p2230 | prosa 2 colonne + ref | py | py | — | — | **PyMuPDF4LLM** |
| B/p2240 | tabella box + prosa | py | **py grande** | **py grande** | — | **PyMuPDF4LLM** |
| pa23/p301 | prosa | py (raw pari) | pari | — | — | pari (bug engine) |
| pa23/p602 | grafico + prosa | pari | pari | xb (label as table) | py | pari |

> Le righe sopra sono arbitrate visivamente sul PNG; il resto del corpus è
> riassunto dalle metriche automatiche in `out/compare.md`.

## 11. Debolezze di PyMuPDF4LLM (dettaglio)

Dati da `out/pymupdf_weaknesses.md` + ispezione dei markdown.

### Header, footer e rumore di layout — 57/92 pagine, 72 occorrenze
- **Header di capitolo e numero di pagina emessi come testo**: `**275**` +
  `CHAPTER **46**` + `**HEART FAIluRE: TREATMEnT AnD pRoGnoSIS**` (ce24/p489);
  `656` + `CHAPTER 18 Endocrine System` (pa23/p743); `**1196**` (A/p1237).
- I **footer di stampa** (`HPIM21e_…indd`, orari) sono invece ben soppressi
  (Xberg li emette) → PyMuPDF4LLM è più pulito sui footer, peggiore su
  header/numero di pagina. I titoli di capitolo correnti non sono deduplicati.

### Tabelle — 21/92 pagine
- **Griglia persa**: tabelle a 1 colonna con `<br>` dentro la cella
  (ce24/p489 TABLE 46-10, pa23/p743 Table 18.5, B/p2240 57 righe a 1 colonna).
- **Colonne disallineate** su tabelle numeriche: co23/p931 TABLE 2 PSI → punti
  nella cella sbagliata e **22 celle vuote adiacenti** (`||`).
- **Caption incoerente**: a volte riga di tabella, a volte promossa a heading
  (`# **TABLE 46-10**`, `## Table 18.5`); caption spezzata a metà parola tra due
  celle (A/p1232 `|**TABLE 148-3 Treat**|**ment of …**|`).

### Immagini e figure
- **0 link immagine** in tutto il corpus: le figure raster non diventano
  `![](...)`. Restano solo didascalia + eventuale `<!-- Start of picture text -->`
  (testo dentro l'immagine), che può sparire nel rendering Markdown.
- Valori di legenda a volte incollati (`1.01.3` invece di `1.0 1.3`).

### Box embedded
- Box/tabelle incorniciati → 1 colonna: **ordine mantenuto, griglia persa**
  (B/p2240). Le sidebar di capitolo sono soppresse bene (meglio di Xberg), ma
  l'header di capitolo resta una riga isolata.

### Multicolonna e senso di lettura
- Ordine di lettura **corretto** nel raw su prosa a due colonne (B/p2230, pa23/p743).
- **Liste fragili**: voci spezzate o continuazioni promosse a nuovo bullet
  (co23/p931: PSI e CURB-65 fusi dentro un solo bullet, continuazione
  `- short-term mortality…` separata).
- **13/92 pagine senza alcun heading**, incluse pagine con titoli evidenti
  (B/p2231, B/p2240, pa23/p602).

### Titoli inframmezzati (heading detection)
- **Over-detection**: A/p1237 → 13 heading, con run-in `###` e titoli spezzati
  su due righe (`### ■ ABIOTROPHIA AND` + `### (NUTRITIONALLY VARIANT STREPTOCOCCI)`).
- Artefatti: `## **~~TREATMENT~~**` (barratura), e lo stesso tipo di titolo reso
  a volte `#`, a volte bold inline.

### Estrazione corretta / glifi
- **9 `�` su 2 pagine** per ≥/≤ (pa23/p743, ce24/p4210); Xberg cancella il
  simbolo invece di segnalarlo.
- Dash/glifi matematici resi come `d` (`modificationdspecifically`, `beef)dcan`,
  pa23/p301) e parole incollate (`obesityrelated`, `Selfdetection`).

### Post-`layout_engine` — 13/92 pagine con perdita di spazi > 1/100
- Il fix di riordino **rimuove gli spazi** (pa23/p431, p301, p909, p743):
  è il difetto più impattante del percorso di produzione.
