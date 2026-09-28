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

- **Nessun carattere di sostituzione (`\ufffd`) né CID** in nessuno dei due motori:
  siamo su born-digital e il text layer è affidabile per entrambi.
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
