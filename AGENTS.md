# Istruzioni per l'agente — noesis-pdf-reader-lite (app desktop)

## Relazione con il repository dell'engine CLI

Questo repository è la **fonte di verità** del motore. Il repository gemello
`../noesis-pdf-reader-lite-engine` (edizione CLI headless) **non ha un motore
proprio**: usa lo **stesso engine** copiato **verbatim** da qui.

Moduli del motore condivisi (copiati senza modifiche nel repo CLI):

- `ir_layout.py`
- `layout_engine.py`
- `page_model.py`
- `layout_proxies.py`
- `pages.py`
- `i18n.py`
- `theme.py`

In più, il repo CLI:

- rigenera un `main.py` **Qt-free** (il "core" headless) dal `main.py` di
  questo repo, con `tools/sync_from_desktop.py`;
- registra la provenienza (commit desktop) in `ENGINE_SOURCE.json`;
- usa `tools/measure_page_model.py` (copiato) per i test del page model.

## Accoppiamento da ricordare

`ir_layout.py` fa `import main` **lazy** per alcuni helper puri
(`_detect_column_splits`, `_figure_jpeg`, `_word_recall`,
`_rotated_table_rects`, `_norm_text`). Per questo il core headless del repo CLI
**deve restare importabile come modulo `main`**: non rinominarlo.

## Regola operativa (sincronizzazione)

Ogni volta che modifichi un modulo del motore qui — o il blocco Qt-free di
`main.py` (tra i marker `#  helpers` e
`#  app data / i18n bootstrap (Qt helpers)`) — i due repo **divergono** finché
non sincronizzi:

```bash
cd ../noesis-pdf-reader-lite-engine
python tools/sync_from_desktop.py
python -m unittest discover -s tests
```

`tests/test_sync_parity.py` nel repo CLI **fallisce** se le copie non sono
byte-identiche o se `main.py` è stale. Se sposti i due marker in `main.py`,
aggiorna `CORE_START` / `CORE_END` in `tools/sync_from_desktop.py` del repo CLI.

## File condivisi oltre al motore

- `tools/measure_page_model.py` — usato dai test di entrambi i repo (copiato nel
  repo CLI): se lo modifichi qui, ricopialo.
- Le opzioni di esportazione batch (`_write_batch_output` / `_batch_page_content`
  / `_batch_strip_images`) sono riprodotte in `export_core.py` del repo CLI:
  se ne cambi la semantica qui, allinea anche `export_core.py`.
