#!/usr/bin/env python3
"""Adapter isolato per il motore di estrazione alternativo **Xberg 1.2.9**.

Questo modulo è **sperimentale** e vive sul branch ``experimental``: tutto
l'esperimento "motori alternativi" è confinato qui e in pochi ganci in
``main.py``, così che possa essere annullato in blocco
(vedi ``experimental/README.md``).

Caratteristiche:

- nessuna dipendenza da PyQt e nessun import pesante a livello di modulo:
  ``xberg`` (Rust + ONNX, ~62 MB di wheel) viene importato *lazy* alla prima
  chiamata, quindi il tier "lite" resta leggero e un motore assente non rompe
  l'app;
- API reale: ``xberg.extract`` è **asincrona** e ritorna un ``ExtractionResult``;
  con ``PageConfig(extract_pages=True)`` espone ``results[0].pages`` (una lista
  di ``PageContent`` con ``page_number`` 1-based e ``content`` Markdown);
- estrazione **a livello documento** con cache in memoria per
  ``(path, size, mtime)`` e selezione della pagina;
- layout/tabelle opzionali (``LayoutDetectionConfig`` + ``use_layout_for_markdown``):
  è lì il valore rispetto a PyMuPDF4LLM. I modelli ONNX vengono scaricati al
  primo uso in ``~/.cache/xberg``.

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
    """True se il pacchetto ``xberg`` espone l'API attesa."""
    m = _module()
    return m is not None and callable(getattr(m, "extract", None))


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


def _build_config(*, use_layout: bool = True, use_cache: bool = True) -> Any:
    """Costruisce ``ExtractionConfig`` in modo difensivo.

    - ``output_format="markdown"`` e ``pages=PageConfig(extract_pages=True)``
      per avere il Markdown pagina per pagina;
    - con ``use_layout`` attiva la layout detection (reading order + tabelle)
      e ``use_layout_for_markdown``, più ``PdfConfig(reading_order=True)``.
    """
    m = _module()
    if m is None:
        return None
    cfg_cls = getattr(m, "ExtractionConfig", None)
    if cfg_cls is None:
        return None

    kwargs: dict[str, Any] = {"output_format": "markdown", "use_cache": use_cache}
    page_cls = getattr(m, "PageConfig", None)
    if page_cls is not None:
        try:
            kwargs["pages"] = page_cls(extract_pages=True)
        except Exception:
            pass
    if use_layout:
        layout_cls = getattr(m, "LayoutDetectionConfig", None)
        if layout_cls is not None:
            try:
                kwargs["layout"] = layout_cls(strategy="always")
                kwargs["use_layout_for_markdown"] = True
            except Exception:
                pass
        pdf_cls = getattr(m, "PdfConfig", None)
        if pdf_cls is not None:
            try:
                kwargs["pdf_options"] = pdf_cls(reading_order=True)
            except Exception:
                pass

    try:
        return cfg_cls(**kwargs)
    except Exception:
        # Fallback minimale: solo Markdown, niente layout/pagine.
        try:
            return cfg_cls(output_format="markdown", use_cache=use_cache)
        except Exception:
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
    results = getattr(result, "results", None)
    if result is None:
        raise XbergExtractionError("risultato Xberg vuoto")

    first = None
    if results:
        first = results[0]
    elif hasattr(result, "content"):
        first = result

    if first is not None:
        pages = getattr(first, "pages", None)
        if pages:
            by_number: dict[int, str] = {}
            for pc in pages:
                try:
                    number = int(getattr(pc, "page_number", 0) or 0)
                except (TypeError, ValueError):
                    number = 0
                by_number[number] = _page_text(pc)
            highest = max(by_number) if by_number else 0
            if highest:
                return [by_number.get(i + 1) for i in range(highest)]

        content = getattr(first, "content", None)
        if isinstance(content, str):
            return content.split("\f")

    errors = getattr(result, "errors", None)
    raise XbergExtractionError(f"risultato Xberg senza pagine/ contenuto (errors={errors!r})")


def _call_extract(path: str, config: Any) -> Any:
    """Invoca ``xberg.extract`` (async) sull'input URI del file."""
    m = _module()
    if m is None:
        raise XbergNotAvailable("pacchetto 'xberg' non installato")

    extract_fn = getattr(m, "extract", None)
    input_cls = getattr(m, "ExtractInput", None)
    if not callable(extract_fn):
        raise XbergNotAvailable("Xberg senza 'extract'")

    if input_cls is not None:
        inp = input_cls(kind="uri", uri=path, mime_type="application/pdf")
        coro = extract_fn(inp, config)
    else:  # firma alternativa: extract(uri, config)
        coro = extract_fn(path, config)

    if asyncio.iscoroutine(coro):
        return _run_async(coro)
    return coro


def _extract_document(path: str, *, use_layout: bool, use_cache: bool) -> list[str | None]:
    """Estrae l'intero documento in pagine Markdown (una sola volta)."""
    config = _build_config(use_layout=use_layout, use_cache=use_cache)
    return _result_pages(_call_extract(path, config))


# ─────────────────────────────────────────────────────────────────────────────
#  cache + API pagina
# ─────────────────────────────────────────────────────────────────────────────


def _cache_key(path: str, use_layout: bool) -> tuple:
    p = Path(path)
    try:
        st = p.stat()
        return (str(p.resolve()), st.st_size, st.st_mtime_ns, use_layout)
    except OSError:
        return (str(p), 0, 0, use_layout)


def clear_cache() -> None:
    """Svuota la cache in memoria (utile per test e per il rollback)."""
    with _cache_lock:
        _cache.clear()


def extract_page(
    path: str, page_num: int, *, use_layout: bool = True, use_cache: bool = True
) -> str | None:
    """Markdown della pagina ``page_num`` (0-based) via Xberg.

    Ritorna ``None`` se Xberg non è disponibile o se la pagina richiesta non è
    recuperabile: il chiamante deve quindi degradare al backend di default.
    L'estrazione dell'intero documento è cachata per ``(path, size, mtime,
    use_layout)``, così le pagine successive dello stesso PDF sono immediate.
    """
    global _last_error
    if not is_available():
        _last_error = "xberg non installato"
        return None

    key = _cache_key(path, use_layout)
    with _cache_lock:
        pages = _cache.get(key)
        if pages is not None:
            _cache.move_to_end(key)

    if pages is None:
        try:
            pages = _extract_document(path, use_layout=use_layout, use_cache=use_cache)
        except Exception as exc:  # noqa: BLE001 — degrada, non crasha
            _last_error = f"{type(exc).__name__}: {exc}"
            pages = []
        with _cache_lock:
            _cache[key] = pages
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_MAX:
                _cache.popitem(last=False)

    if 0 <= page_num < len(pages) and pages[page_num] is not None:
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
