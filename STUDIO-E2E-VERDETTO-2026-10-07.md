# VERDETTO FINALE — run G + H (500 pagine) — 2026-10-07

Due run E2E da 250 pagine con le metriche **stringenti** (Golden Rule #1
rafforzata, `REGOLE-TEST.md` R8–R12), arbitraggio visivo dell'agente:

- **G** — casuale (seed `20261011`), 100+100+50.
- **H** — stratificato per classe (seed `20261012`) + riempimento a 250
  (seed `20261013`).

## 1. Sintesi dei due run

| | run G | run H |
|---|---|---|
| pagine pulite (auto) | 245/250 | 242/250 |
| engine | ir=243, current=7 | ir=243, current=7 |
| recall media | 0.9693 | 0.9729 |
| flow media | 0.9956 | 0.9923 |
| tabelle | 58 · 0.9937 | 64 · 0.9937 |
| figure mancanti | **0** | **0** |
| flag ordine stringente | 4 pagine | 8 pagine |

## 2. Difetti REALI (arbitraggio severo, 500 pagine)

| # | pagina | run | tipo | descrizione |
|---|---|---|---|---|
| 1 | `pa19 p1274` | G | **ordine** | apertura capitolo, layout misto 2/3 colonne: `51.3` letto **dopo** KEY TERMS/ABBREVIATIONS |
| 2 | `cu25 p761` | G | **ordine** | titolo/autori emessi **dopo** la colonna sinistra |
| 3 | `co23 p877` | G | **ordine** | interleaving delle due colonne di testo (minore) |
| 4 | `ce24 p238` | H | **ordine** | lista referenze (destra) **interlacciata** con la prosa (sinistra) |
| 5 | `co26 p1316` | H | **ordine** | "References" (sinistra) interlacciata con la prosa EBV (destra) |
| 6 | `co26 p610` | H | **struttura** | md su **una sola riga** (testo presente, struttura persa) |
| 7 | `arxiv_2609.38133 p16` | G | **testo** | perdita su **equazioni**/tabelle |

**Ambigui** (ordine plausibile, possibile artefatto del riferimento):
`ha22 p2547`, `cu25 p1290`.

## 3. Qualità del verdetto automatico (target: 0 FP / 0 FN)

| | valore |
|---|---|
| difetti reali trovati | **7** (+2 ambigui) |
| **falsi negativi** | **0** |
| **falsi positivi** | **~6** (`fe22 p951`, `fe22 p1850/2267/1902`, …) |

→ Il verdetto automatico **non è ancora a 0 FP / 0 FN** (target non raggiunto):
la metrica stringente è **corretta sui difetti reali** ma produce **~6 falsi
positivi** su 500 pagine (ancore ripetute / riferimento geometrico in difetto su
pagine multi-colonna table-heavy).

## 4. Classi di difetti ricorrenti (root cause)

1. **Aperture di capitolo a layout misto** (2/3 colonne in bande diverse):
   il rilevatore di colonne del motore è **globale**, non per banda → ordine
   sbagliato (`pa19 p1274`, `cu25 p761`, sospetti `ha22 p2547`, `cu25 p1290`).
2. **Pagine multi-colonna con liste di referenze**: l'md **intreccia** la
   colonna delle referenze con quella della prosa (`ce24 p238`, `co26 p1316`,
   `co23 p877`).
3. **Struttura markdown persa**: testo concatenato in una sola riga
   (`co26 p610`).
4. **Equazioni/pagine scientifiche**: prosa/math persa (`arxiv p16`, `p19`,
   `p39`).

## 5. VERDETTO ESIGENTE

**Il motore NON è pronto per la produzione e NON va ancora fatto il merge su
`main`.**

Motivazione:
- Su 500 pagine restano **7 difetti reali**, di cui **5 di ordine di lettura** —
  esattamente l'asse che la Golden Rule #1 pone come **bloccante primario**.
- I difetti non sono casi isolati: appartengono a **classi strutturali**
  (aperture di capitolo a layout misto; liste di referenze multi-colonna;
  struttura markdown) che si ripresenteranno in produzione.
- Il **verdetto automatico non è a 0 FP/0 FN**: ~6 falsi positivi su 500 pagine
  rendono l'harness non ancora affidabile come gate automatico.

## 6. Cosa serve prima del merge (proposta)

1. **Ordine per banda nel motore**: rilevamento colonne **per fascia** (XY-cut /
   split per banda) per le aperture di capitolo a layout misto.
2. **Liste di referenze multi-colonna**: non intrecciare la colonna referenze
   con la prosa (estensione del rilevatore robusto).
3. **Struttura markdown**: garantire i confini di paragrafo (mai una sola riga).
4. **Equazioni/pagine formula**: recupero della prosa.
5. **Metrica a 0 FP/0 FN**: ridurre i falsi positivi (ancore ripetute;
   riferimento affidabile sulle pagine table-heavy) prima di promuoverla a
   **gate automatico**.
6. **Golden set** di pagine verificate a mano + **2 run di conferma** dopo i fix,
   con **0 difetti reali** e **0 FP/0 FN**.

## 7. Cosa è già solido (da preservare)

- **0 figure mancanti** su 500 pagine.
- **Tabelle**: recall medio 0.9937, nessuna malformata.
- **Nessun falso negativo** di contenuto rilevato (le pagine a recall 0 sono
  figure-only).
- La classe "blocco a ponte → colonne collassate" (run E) è **risolta**.

**Raccomandazione**: continuare sulla linea `layout-order`; **non** fare il merge
su `main` finché non si azzerano i 7 difetti reali e non si porta la metrica a
0 FP/0 FN su due run di conferma.

---

# Aggiornamento post-fix (2026-10-07)

Dopo il verdetto sono stati applicati fix mirati e validati con un **terzo run
(I)** (seed `20261014`, 250 pagine nuove):

## Fix applicati
- **`b12a86e` — rilevatore colonne robusto prima della proiezione a copertura.**
  `layout_proxies.column_splits` restituiva split **spuri** (dentro una colonna)
  → le colonne collassavano → interleaving. Risolve **4 difetti reali**:
  `co26 p1316` (inv 63→0), `ce24 p238` (17→0), `co26 p610` (18→0, struttura
  recuperata), `co23 p877` (1→0).
- **`9e5e3c1` — revert del tentativo "ordine per sotto-regione".** Ordinava bene
  `pa19 p1274` ma rendeva il **riferimento** (misura dell'ordine) troppo
  rumoroso (over-split su pagine a colonne omogenee → 16 flag su run I, quasi
  tutti falsi positivi). Ripristinato lo stato pulito.

## Run I (validazione, 250 pagine nuove)
| | valore |
|---|---|
| flow (LIS) | **1.0000** — 0 pagine < 0.95 |
| auto pulite | **245/250** (5 flag: 4 `text_order` + 1 `table_content`) |
| ordini reali (arbitro) | **1** (`ne17 p261`, passi numerati di una figura) |
| tabelle reali | 1 (`su19 p819`) |
| falsi positivi | 3 (inversioni spurie, flow 1.00) |

## Stato aggiornato
- **Difetti di flusso (intreccio colonne): 0** su run I — la classe "split
  spuri → colonne collassate" è **risolta**.
- **Restano**: `pa19 p1274` / `cu25 p761` (aperture di capitolo a **layout
  misto** 2/3 colonne) e `ne17 p261` (passi numerati di figura). La fix
  "sotto-regione" è pronta concettualmente ma richiede un **riferimento
  affidabile** per essere misurata senza falsi positivi.
- **Metrica**: il rilevamento colonne del riferimento va reso coerente col
  motore (o migliorato) per portare i falsi positivi a 0.

## Verdetto aggiornato
**Ancora NON pronto per il merge**, ma in **netto miglioramento**: 4 difetti
reali di ordine risolti, flusso perfetto su un run nuovo. Prima del merge:
1. ordine **per banda** per le aperture di capitolo a layout misto
   (`pa19 p1274`, `cu25 p761`);
2. passi numerati di figura (`ne17 p261`);
3. riferimento a **0 falsi positivi** (rilevamento colonne coerente col motore);
4. **2 run di conferma** con 0 difetti reali e 0 FP/0 FN.

