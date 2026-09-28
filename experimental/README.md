# Branch `experimental` — motori di estrazione alternativi

Questo branch esiste per **provare** il piano descritto in
[`../PIANO-motori-estrazione-vlm.md`](../PIANO-motori-estrazione-vlm.md)
(Xberg, MinerU, Marker e VLM mirato) **senza sporcare `main`**.

Regola d'oro: ogni cosa che appartiene all'esperimento resta confinata qui.
`main` deve continuare a essere il repository "lite" pulito (un solo motore:
PyMuPDF/PyMuPDF4LLM + `layout_engine.py`).

## Stato — Fase 1 (Xberg) implementata

Il backend Xberg è **opt-in** e spento di default: il tier lite continua a usare
PyMuPDF4LLM senza modifiche. Per attivarlo:

```bash
pip install -r requirements-xberg.txt          # Xberg (Rust + ONNX, no torch)
NOESIS_EXTRACT_BACKEND=xberg ./run.sh          # oppure "extract_backend": "xberg" in config.json
```

Cosa fa la Fase 1:

- `xberg_engine.py` — adapter isolato (import lazy, cache documento, fallback a
  PyMuPDF4LLM se Xberg manca o fallisce);
- `requirements-xberg.txt` — dipendenza opzionale, fuori dal tier lite;
- `layout_engine.py` — il fix `reorder_columns` viene saltato per il backend
  `Xberg` (forma B: niente doppio riordino);
- `main.py` / `i18n.py` — selezione del backend via env/config e header che
  mostra il backend attivo;
- `tests/test_xberg_engine.py` — test dell'adapter con un modulo `xberg` finto;
- `experimental/benchmark_xberg.py` — confronto Xberg vs PyMuPDF4LLM su PDF
  reali (validazione del piano).

Il rollback qui sotto annulla **anche** tutte queste modifiche, perché
`main.py`, `i18n.py` e `layout_engine.py` sono tracciati e tornano allo stato di
`main` con il checkout.

## Requisito: poter annullare tutto con un comando

L'annullamento completo è implementato da
[`abort_experiment.py`](abort_experiment.py) (+ wrapper [`abort.sh`](abort.sh)).

```bash
# dalla radice del repository, sul branch experimental
python experimental/abort_experiment.py --yes
```

Cosa fa, in ordine:

1. `git checkout -f main` — torna al branch base scartando le modifiche
   dell'esperimento;
2. `git branch -D experimental` — elimina il branch sperimentale;
3. `git clean -fd` — rimuove i file non tracciati introdotti dal piano
   (requirements dei motori, moduli nuovi, ecc.). **Non** usa `-x`, quindi
   `.venv/` e le altre esclusioni di `.gitignore` restano intatte, e **esclude
   i `*.pdf`** (sono dati dell'utente, non artefatti dell'esperimento);
4. (opzionale) elimina le cache dei modelli scaricati;
5. (opzionale) disinstalla i motori dal venv.

Alla fine verifica `git status`: esce con **0** solo se il repository è pulito.

### Opzioni

| Opzione | Effetto |
|---|---|
| `--yes` / `-y` | non chiedere conferma |
| `--dry-run` | stampa i comandi senza eseguirli |
| `--purge-models` | rimuove `~/.cache/xberg`, `~/.mineru` e le cache HuggingFace di `xberg-io`/`opendatalab`/`datalab-to` |
| `--purge-venv` | disinstalla `xberg`, `mineru`, `marker-pdf`, `surya-ocr`, ecc. da `.venv/` |
| `--delete-remote` | elimina anche `origin/experimental` |
| `--keep-plan` | conserva `PIANO-motori-estrazione-vlm.md` (lo ripristina come file non tracciato) |
| `--keep-branch` | non eliminare il branch `experimental` |
| `--keep-untracked` | salta `git clean -fd` |
| `--branch` / `--base` | nomi dei branch (default `experimental` / `main`) |
| `--repo-root` | radice del repository (default: autodetect) |

Esempio "pulizia totale" (branch + modelli + pacchetti):

```bash
python experimental/abort_experiment.py --yes --purge-models --purge-venv
```

## Alternativa manuale (senza lo script)

```bash
git checkout main
git branch -D experimental
git clean -fd -e '*.pdf'            # -e: NON cancellare i PDF; mai -x (.venv/)
```

Le cache dei modelli vanno rimosse a mano:

```bash
rm -rf ~/.cache/xberg ~/.mineru
rm -rf ~/.cache/huggingface/hub/models--xberg-io--*
rm -rf ~/.cache/huggingface/hub/models--opendatalab--*
rm -rf ~/.cache/huggingface/hub/models--datalab-to--*
```

## Note

- Lo script è **effimero di proposito**: vive sul branch sperimentale. Se lo si
  esegue, il file sparisce insieme al branch. La procedura manuale sopra è il
  fallback sempre disponibile.
- `--purge-venv` non tocca `torch` condiviso con eventuali altri backend: per
  quello conviene ricreare il venv (`python3 -m venv .venv` +
  `pip install -r requirements.txt`).
- La stessa logica è richiamabile a mano; lo script serve solo a renderla
  ripetibile e verificabile.
