#!/usr/bin/env python3
"""Adapter isolato per il motore di estrazione alternativo **Xberg**.

Questo modulo è **sperimentale** e vive sul branch ``experimental``: tutto
l'esperimento "motori alternativi" è confinato qui e in pochi ganci in
``main.py``, così che possa essere annullato in blocco
(vedi ``experimental/README.md``).

Caratteristiche:

- nessuna dipendenza da PyQt e nessun import pesante a livello di modulo:
  ``xberg`` (Rust + ONNX, ~155 MB) viene importato *lazy* alla prima chiamata,
  quindi il tier "lite" resta leggero e un motore assente non rompe l'app;
- estrazione **a livello documento** (l'API Xberg è documentale) con cache in
  memoria per ``(path, size, mtime)`` e selezione della pagina;
- il layout/reading order di Xberg è opzionale: il chiamante decide se
  applicare sopra il ``layout_engine`` esistente (vedi ``layout_engine.py``).

API pubblica::

    import xberg_engine
    if xberg_engine.is_available():
        md = xberg_engine.extract_page("doc.pdf", 3)   # str | None
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

#: Etichetta mostrata nell'header di estrazione.
BACKEND_LABEL = "Xberg"

#: Numero di documenti (liste di pagine) tenuti in memoria (LRU).
_CACHE_MAX = 4

_xberg: Any = None
_import_done = False
_import_lock = threading.Lock()

_cache: "OrderedDict[tuple, list[str | None]]" = OrderedDict()
_cache_lock = threading.Lock()

_last_error: str | None = None


class XbergNotAvailable(RuntimeError):
    """Sollevata quando il pacchetto ``xberg`` non è importabile."""


class XbergExtractionError(RuntimeError):
    """Sollevata quando Xberg non riesce a estrarre il documento."""


# ─────────────────────────────────────────────────────────────────────────────
#  caricamento lazy del modulo
# ─────────────────────────────────────────────────────────────────────────────


def _module() -> Any:
    """Importa ``xberg`` una sola volta (thread-safe); ``None`` se assente."""
    global _xberg, _import_done
    if not _import_done:
        with _import_lock:
            if not _import_done:
                try:
                    import xberg as _m  # type: ignore
                except Exception:
                    _m = None
                _xberg = _m
                _import_done = True
    return _xberg


def is_available() -> bool:
    """True se il pacchetto ``xberg`` è importabile (senza importarlo a fondo)."""
    return _module() is not None


def version() -> str | None:
    """Versione dichiarata da Xberg, se disponibile."""
    m = _module()
    if m is None:
        return None
    value = getattr(m, "__version__", None)
    return str(value) if value else None


def last_error() -> str | None:
    """Ultimo errore d'estrazione registrato (diagnostica)."""
    return _last_error


# ─────────────────────────────────────────────────────────────────────────────
#  costruzione configurazione e chiamata nativa
# ─────────────────────────────────────────────────────────────────────────────


def _build_config() -> Any:
    """Costruisce ``ExtractionConfig`` in modo difensivo (API in evoluzione).

    Prova prima la configurazione con estrazione per pagina; in caso di
    ``TypeError``/attributi mancanti degrada a Markdown di default.  Non solleva
    mai: se ``ExtractionConfig`` non è disponibile torna ``None``.
    """
    m = _module()
    if m is None:
        return None
    cfg_cls = getattr(m, "ExtractionConfig", None)
    if cfg_cls is None:
        return None

    page_cfg = None
    page_cls = getattr(m, "PageConfig", None)
    if page_cls is not None:
        try:
            page_cfg = page_cls(extract_pages=True)
        except Exception:
            page_cfg = None

    attempts: list[dict] = []
    if page_cfg is not None:
        attempts.append({"output_format": "markdown", "pages": page_cfg})
    attempts.append({"output_format": "markdown"})
    attempts.append({})
    for kwargs in attempts:
        try:
            return cfg_cls(**kwargs)
        except Exception:
            continue
    return None


def _run_async(coro: Any) -> Any:
    """Esegue una coroutine anche se un event loop è già attivo nel thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _page_text(page: Any) -> str:
    """Estrae il Markdown da una pagina del risultato Xberg (formati vari)."""
    if page is None:
        return ""
    if isinstance(page, str):
        return page
    if isinstance(page, dict):
        for key in ("content", "text", "markdown", "md"):
            value = page.get(key)
            if isinstance(value, str):
                return value
        return ""
    for attr in ("content", "text", "markdown", "md"):
        value = getattr(page, attr, None)
        if isinstance(value, str):
            return value
    return ""


def _result_pages(result: Any) -> list[str | None]:
    """Converte un ``ExtractionResult`` in una lista di pagine Markdown."""
    pages = getattr(result, "pages", None)
    if pages is None and isinstance(result, dict):
        pages = result.get("pages")
    if pages:
        return [_page_text(p) for p in pages]

    content = getattr(result, "content", None)
    if content is None and isinstance(result, dict):
        content = result.get("content")
    if not isinstance(content, str):
        raise XbergExtractionError("risultato Xberg senza 'content' testuale")
    # Fallback: separatore di pagina classico; se assente, un'unica pagina.
    return content.split("\f")


def _call_extract(path: str, config: Any) -> Any:
    """Invoca l'API sincrona o asincrona di Xberg, con o senza config."""
    m = _module()
    if m is None:
        raise XbergNotAvailable("pacchetto 'xberg' non installato")

    def _invoke(fn):
        return fn(path, config=config) if config is not None else fn(path)

    sync_fn = getattr(m, "extract_file_sync", None)
    if callable(sync_fn):
        return _invoke(sync_fn)

    async_fn = getattr(m, "extract_file", None)
    if callable(async_fn):
        return _run_async(_invoke(async_fn))

    raise XbergNotAvailable("Xberg senza extract_file/extract_file_sync")


def _extract_document(path: str) -> list[str | None]:
    """Estrae l'intero documento in pagine Markdown (una sola volta)."""
    return _result_pages(_call_extract(path, _build_config()))


# ─────────────────────────────────────────────────────────────────────────────
#  cache + API pagina
# ─────────────────────────────────────────────────────────────────────────────


def _cache_key(path: str) -> tuple:
    p = Path(path)
    try:
        st = p.stat()
        return (str(p.resolve()), st.st_size, st.st_mtime_ns)
    except OSError:
        return (str(p), 0, 0)


def clear_cache() -> None:
    """Svuota la cache in memoria (utile per test e per il rollback)."""
    with _cache_lock:
        _cache.clear()


def extract_page(path: str, page_num: int) -> str | None:
    """Markdown della pagina ``page_num`` (0-based) via Xberg.

    Ritorna ``None`` se Xberg non è disponibile o se la pagina richiesta non è
    recuperabile: il chiamante deve quindi degradare al backend di default.
    L'estrazione dell'intero documento è cachata per ``(path, size, mtime)``,
    così le pagine successive dello stesso PDF sono immediate.
    """
    global _last_error
    if not is_available():
        _last_error = "xberg non installato"
        return None

    key = _cache_key(path)
    with _cache_lock:
        pages = _cache.get(key)
        if pages is not None:
            _cache.move_to_end(key)

    if pages is None:
        try:
            pages = _extract_document(path)
        except Exception as exc:  # noqa: BLE001 — degrada, non crasha
            _last_error = f"{type(exc).__name__}: {exc}"
            pages = []
        with _cache_lock:
            _cache[key] = pages
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)

    if 0 <= page_num < len(pages):
        _last_error = None
        return pages[page_num]
    return None


# ─────────────────────────────────────────────────────────────────────────────
#  supporto ai test (reset dello stato globale)
# ─────────────────────────────────────────────────────────────────────────────


def _reset_for_tests() -> None:
    """Riporta il modulo allo stato iniziale (solo per i test)."""
    global _xberg, _import_done, _last_error
    with _import_lock:
        _xberg = None
        _import_done = False
    with _cache_lock:
        _cache.clear()
    _last_error = None
