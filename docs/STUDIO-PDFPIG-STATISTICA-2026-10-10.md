# STUDIO — L'approccio statistico di PdfPig (soglie auto-calibrate)

> **Oggetto.** Approfondimento mirato sulla parte **statistica/auto-calibrante**
> di PdfPig: come deriva le soglie **dalla geometria della pagina stessa**
> invece di usare costanti in punti.
>
> **Sorgente.** `UglyToad/PdfPig` — commit `91ddd23`, licenza **Apache-2.0**,
> C#/.NET. KB locale: `other-engines/pdfpig/` (ricostruibile con
> `tools/fetch-other-engines.sh --lean`).
>
> **File letti integralmente** (`src/UglyToad.PdfPig.DocumentLayoutAnalysis/`):
> `PageSegmenter/DocstrumBoundingBoxes.cs` (761),
> `PageSegmenter/RecursiveXYCut.cs` (401), `WordExtractor/NearestNeighbourWordExtractor.cs`
> (239), `Clustering.cs` (522), `Distances.cs` (227), `MathExtensions.cs` (60),
> `ReadingOrderDetector/UnsupervisedReadingOrderDetector.cs` (263),
> `ReadingOrderDetector/IntervalRelationHelper.cs` (279), `KdTree.cs` (617,
> parziale: build/quickselect), `WhitespaceCoverExtractor.cs` (parziale),
> `NearestNeighbourWordExtractor-review.md` (231).
>
> **Contesto.** `STUDIO-MOTORI-GEOMETRICI-ESTERNI-2026-10-10.md` §4,
> `STUDIO-IDEE-MOTORI-ESTERNI-2026-10-10.md` (C.2/C.4),
> `PIANO-migliorie4engines-2026-10-10.md` (voce **M4E-6.1**).

---

## 0. Sintesi in una riga

PdfPig **non** usa un modello probabilistico né ML: usa **statistica descrittiva
non supervisionata e per-pagina** per stimare *spaziature* e *dimensioni di
carattere tipiche*, e ne ricava le soglie con un moltiplicatore di sicurezza.
È **auto-calibrazione geometrica**, non tuning su etichette.

---

## 1. Cos'è (e cosa non è) "statistico"

| è | non è |
|---|---|
| **istogrammi** di distanze nearest-neighbour tra parole | un modello di probabilità addestrato |
| **moda** di larghezze/altezze dei glifi | media/varianza su tutta la pagina |
| **picco + media del picco** (media robusta) | soglie assolute in pt |
| **moltiplicatori** relativi alla misura (×3, ×1.3, ×1.5, ×1.25) | regressione / classificatore |
| nessuna dipendenza esterna, deterministico *per costruzione* | GPU / rete neurale |

Il resto del progetto (clustering nearest-neighbour, Allen-relations, kd-tree) è
**geometria discreta e grafi**, non statistica. Vedi §4.

---

## 2. Il cuore statistico — stima delle spaziature (Docstrum, 1° passo)

`PageSegmenter/DocstrumBoundingBoxes.cs:148` — `GetSpacingEstimation(words, …)`.
È il 1° dei tre passi del *Document Spectrum* (O'Gorman). Restituisce
`withinLineDistance` e `betweenLineDistance` in **punti**, stimati dalla pagina.

### 2.1 Raccolta delle distanze

- Indice **kd-tree** dei candidati per `BoundingBox.BottomLeft` (`:160`).
- Per ogni parola (pivota), **2 vicini più prossimi**, in due query distinte:
  - *within-line*: query sul `BottomRight` della pivota (`:170`);
  - *between-line*: query sul `TopLeft` della pivota (`:181`).
- Filtro **angolare** (le due distanze non si mescolano):
  - `AngleWL` (`:358`): angolo `BottomRight → BottomLeft`, tolta la rotazione,
    in **[-90, 90]**; ammesso se in `WithinLineBounds` = **[-30°, 30°]** (`:718`).
  - `AngleBL` (`:633`): angolo tra i **centroidi**, tolta la rotazione, in
    **[0, 180]**; ammesso se in `BetweenLineBounds` = **[45°, 135°]** (`:738`).

### 2.2 Le due distanze (formule)

- **Within-line** (`:175`): distanza **euclidea** `BottomRight → BottomLeft`
  (spazio di avanzamento tra parole consecutive).
- **Between-line** (`:189-204`): **distanza perpendicolare** tra i due
  centroidi, al netto delle mezze altezze:
  ```text
  ipo  = Euclidea(centroid_pivot, centroid_cand)
  θ    = angolo(centroid_pivot, centroid_cand) − rotazione_pivot   (clamp → [0,180])
  dist = |ipo · cos((90 − θ)·π/180)| − h_pivot/2 − h_cand/2
  tieni dist solo se dist ≥ 0            # scarta gli overlap
  ```
  (`cos(90−θ) = sin θ`: è la componente perpendicolare alla linea di testo.)

### 2.3 Dalle distanze alla soglia: istogramma → picco → media

`GetPeakAverageDistance(distances, binLength)` (`:238-318`):

1. `maxDbl = ceil(max(distances))`; `binLength = min(binLength, max)` (o `= binLength`
   se `max == 0`) — **bin size = 10** di default (`:732`, `:752`).
2. `binCount = ceil(max / binLength) + 1`; ogni distanza va nel bin
   `floor(d / binLength)`.
3. Si sceglie il **bin più popoloso** (la *moda dell'istogramma*, `:297-305`).
4. Si restituisce la **media dei valori dentro quel bin** (`:312-317`).

Quindi: **media robusta = media del bin modale** (ignora code e outlier).
Se una delle due liste è vuota → `null` → `NaN`.

### 2.4 Dalla stima alla soglia operativa

```csharp
maxWithinLineDistance  = 3.0 × withinLineDistance    // :124, WithinLineMultiplier (:726)
maxBetweenLineDistance = 1.3 × betweenLineDistance   // :128, BetweenLineMultiplier (:746)
```

Il 2° passo raggruppa le parole in righe con `maxWithinLineDistance` (`GetLines`,
`:334`); il 3° passo fonde le righe in blocchi con `maxBetweenLineDistance` +
`AngularDifferenceBounds` = **[-30°, 30°]** (`GetStructuralBlocks`, `:397`).

**Perché è potente:** le soglie sono **relative** a una misura della pagina →
scalano con font, colonna, DPI, senza numeri assoluti. **Perché è fragile:** se la
stima fallisce, `NaN` viene forzato a **0** (`:112-121`) e con soglia 0 **non si
raggruppa nulla**.

---

## 3. La statistica "discreta" — la Moda

`MathExtensions.cs:16-37` — `Mode()`: valore **più frequente**, e restituisce
**`NaN` se non è unico** (pareggio). È una misura **robusta** per grandezze
quasi-discrete (le larghezze/altezze dei glifi cadono su pochi valori distinti;
la media è trascinata da accenti, legature, maiuscole).

Tre usi:

| dove | formula | default | fallback |
|---|---|---|---|
| `RecursiveXYCut.cs:372-382` `DominantFontWidthFunc` | `mode(max(round(letter.Width,3), round(bbox.Width,3)))` | — | se `NaN`/0 → `average` |
| `RecursiveXYCut.cs:389-399` `DominantFontHeightFunc` | `mode(round(bbox.Height,3)) × 1.5` | — | se `NaN`/0 → `average` |
| `WhitespaceCoverExtractor.cs:28-29` | `minWidth = mode(bbox.Width) × 1.25`, `minHeight = mode(bbox.Height) × 1.25` | fuzziness 0.15 | — |

In `RecursiveXYCut.VerticalCut` la **soglia di taglio** è esattamente la moda:
si taglia se `gap = next.Left − current.UpperBound > dominantFontWidth`
**e** la proiezione corrente è ≥ `MinimumWidth` (default **1**, `:365`); altrimenti
si fonde (`:165-185`). Idem in `HorizontalCut` con `dominantFontHeight` (`:262`).

---

## 4. Cosa *non* è statistico (per non confondere)

- **Clustering `NearestNeighbours`** (`Clustering.cs:36-120`): combinatorio. Ogni
  elemento punta al **vicino più prossimo** entro una distanza-massima che è una
  **funzione** (non un numero); i gruppi sono **componenti connesse** (DFS, `:282`;
  `GroupByLinks`, `:384`).
- **`NearestNeighbourWordExtractor`** (`WordExtractor/NearestNeighbourWordExtractor.cs:189`):
  distanza massima = **20% di `max(width, pointSize)`** dei due glifi, **raddoppiata**
  per orientamento `Other`; misura **Manhattan** per orientamenti axis-aligned,
  **Euclidea** altrove (`:216`/`:209`). Anche qui: soglia **font-relativa**, non
  statistica di pagina.
- **Ordine di lettura** (`UnsupervisedReadingOrderDetector.cs`): **Allen/TBRR**
  (`IntervalRelationHelper.cs:27,112`), relazioni **qualitative**, con tolleranza
  **T = 5** (`:60,72`, "due coordinate più vicine di T sono uguali"); scelta
  **greedy max out-degree** (`:126-142`). Geometria qualitativa + grafo.
- **kd-tree** (`KdTree.cs:121-128,148`): *quickselect* con pivot **median-of-three**
  per bilanciare l'albero (con fallback `Array.Sort`). Struttura dati, non statistica.

**Conclusione:** lo "statistico" in PdfPig è **limitato alla stima delle soglie**
(§2–§3). Il resto è geometria e grafi — ed è esattamente la parte che conviene
riusare **separatamente** come *riferimento d'ordine* (M4E-5.1).

---

## 5. Formule e default (tavola riassuntiva)

| grandezza | formula | default | file:riga |
|---|---|---|---|
| bin istogramma | ampiezza bin | `10` | `Docstrum…:732,752` |
| within-line | media del bin modale di `Euclidea(BR, BL)` | — | `:238-317` |
| between-line | media del bin modale di `\|ipo·sin θ\| − h/2 − h/2` | — | `:189-204,238-317` |
| bounds within-line | angolo `[-30, 30]` | — | `:718` |
| bounds between-line | angolo `[45, 135]` | — | `:738` |
| soglia riga | `3.0 × within` | `3.0` | `:124,726` |
| soglia blocco | `1.3 × between` | `1.3` | `:128,746` |
| parallelismo righe | `[-30, 30]` | — | `:759` |
| font width dominante | `mode(larghezze)` | fallback media | `RecursiveXYCut:372` |
| font height dominante | `mode(altezze) × 1.5` | fallback media | `RecursiveXYCut:389` |
| spazio tra parole (cover) | `mode × 1.25` | — | `WhitespaceCover:28` |
| unione glifi in parola | `0.2 × max(width, pointSize)` | ×2 se `Other` | `NNWordExtractor:189` |
| tolleranza "uguale" (Allen) | `T` | `5` pt | `Unsupervised…:72` |

---

## 6. Robustezza, guardie e limiti (da imitare / da evitare)

**Guardie già presenti (da imitare):**
- Moda non unica → `NaN` → **fallback alla media** (`RecursiveXYCut:377,394`).
- Distanze negative (overlap parola) → **scartate** (`Docstrum…:202`).
- Lista vuota → `null` → soglia forzata a `0` (`Docstrum…:112-121`).

**Limiti dichiarati / trappole (da evitare):**
- **Semplificazione**: il commento (`Docstrum…:14`) dice che *non replica
  esattamente* l'originale. Non c'è media±σ né test di unimodalità: solo il
  picco. Su pagine con distribuzione bimodale la moda è ambigua.
- **Bin size 10** resta un numero assoluto (opzione sì, ma costante).
- **Soglia 0 = nessun raggruppamento**: il fallback silenzioso è pericoloso.
- **I moltiplicatori 3.0 / 1.3 / 1.5 / 1.25** sono i numeri "magici" residui —
  però **relativi** a una misura, quindi molto meglio di pt fissi.
- **Bug e nondeterminismo** (documentati nel repo stesso,
  `NearestNeighbourWordExtractor-review.md`):
  1. l'auto-vicino nel kd-tree può far combaciare una lettera con sé stessa →
     **spezza le parole** (`KdTree` nodo interno);
  2. l'ordine dei glifi in un word segue il **DFS**, non l'ordine di lettura
     (`Clustering.cs:85`);
  3. i **bucket di orientamento** elaborati in parallelo danno **ordine non
     deterministico** tra run (144/200 run diversi nel repro).

→ **Conseguenza per noi:** adottare l'**idea** (stime auto-calibrate + guardie +
soglie relative), **non** il codice. E implementarla in modo **deterministico**.

---

## 7. Idee per noi (mappatura sui difetti) e come re-implementarla

Il nostro difetto noto è **soglie "magiche" in pt** (`STUDIO-IDEE…` C.2/C.4).
PdfPig mostra la via: **misura la pagina, poi soglia = k × misura**, con guardia.

| idea PdfPig | difetto nostro | voce |
|---|---|---|
| istogramma within-line → soglia di contiguità | **glue** a livello parola | M4E-2.1 `glyph_vocab_grounded`, M4E-6.1 |
| between-line = distanza **perpendicolare** | righe/paragrafi, **colonne** | M4E-6.1 |
| **moda** font height × 1.5 | heading, taglio blocchi | M4E-4.1, M4E-6.1 |
| `0.2 × max(width, pointSize)` | unire lettere in parola (split/glue) | M4E-2.1 |
| tolleranza **T = 5** + Allen | metro d'ordine indipendente | M4E-5.1 `ref_order_allen` |
| **guardia** moda `NaN` → media | evitare FP su pagine piatte | trasversale |

**Re-implementazione deterministica su `pymupdf` (vincoli del piano §1):**
1. **Input** da `rawdict` (glifi con bbox + `origin`/baseline), **ordinati** (no
   parallelismo o parallelismo *riducibile* a ordine fisso).
2. **NN** per distanza: sorting su un asse + finestra, oppure griglia spaziale;
   niente dipendenze (no scipy). Il kd-tree si può omettere: le pagine nostre
   sono piccole (≤ qualche migliaio di glifi).
3. **Istogramma** delle distanze, **picco** = argmax del conteggio, **stima** =
   media del picco. Stessa aritmetica di `GetPeakAverageDistance`.
4. **Moda** con **tie-break esplicito e deterministico** (es. il valore più
   piccolo tra quelli a pari frequenza) **invece** di `NaN`: niente rami ambigui.
5. **Guardie**: se `N` glifi < soglia minima, o distribuzione piatta/degenere, o
   stima `0`/`NaN` → **default conservativo** (nessuna modifica), non "soglia 0".
6. **Separazione dei due strati** (piano §1.3): le stime sono **scheletro
   geometrico**, l'uso è il fix; l'output con flag OFF resta **byte-identico**.

---

## 8. Riferimenti (file:riga) e caveat

- `PageSegmenter/DocstrumBoundingBoxes.cs` — `GetSpacingEstimation`:148,
  `GetPeakAverageDistance`:238, opzioni:688-760, `WithinLineMultiplier`:726,
  `BetweenLineMultiplier`:746.
- `MathExtensions.cs` — `Mode`:16,37.
- `PageSegmenter/RecursiveXYCut.cs` — `DominantFontWidthFunc`:372,
  `DominantFontHeightFunc`:389, taglio:165,262.
- `WhitespaceCoverExtractor.cs` — mode×1.25:28-29, fuzziness:47.
- `WordExtractor/NearestNeighbourWordExtractor.cs` — `MaximumDistance`:189,
  distanze:209,216.
- `ReadingOrderDetector/IntervalRelationHelper.cs` — `GetRelationX`:27,
  `GetRelationY`:112, enum Allen:193.
- `ReadingOrderDetector/UnsupervisedReadingOrderDetector.cs` — `T`:72,
  greedy:126.
- `KdTree.cs` — build quickselect median-of-three:121-165.
- `Clustering.cs` — `NearestNeighbourGroups`:53, `GroupByLinks`:384.
- `NearestNeighbourWordExtractor-review.md` — bug 1/2/3 e performance.

**Caveat.** Apache-2.0: riutilizzo con attribuzione; per noi è **riferimento di
lettura**, ogni idea va **re-implementata** su `pymupdf` (nessuna nuova
dipendenza). Il codice PdfPig ha **nondeterminismo noto** (§6): non va incollato.

---

*Fine studio. Documento di analisi: nessuna modifica al motore.*
