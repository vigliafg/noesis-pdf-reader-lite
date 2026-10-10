#!/usr/bin/env python3
"""Inventario automatico della superficie d'azione della finestra destra.

**Scopo**: "predisporre per analizzare" i bottoni flottanti (FAB) e le mini
toolbar delle tab. Costruisce la finestra reale (headless) e **enumera** le
azioni disponibili per ogni area, producendo un inventario JSON + markdown:

- **tab bar**: tab (Originale / lingua / Immagini) + extra (radio motore, spinner);
- **mini toolbar** di ogni finestra di testo (A−/A+/↺/💾/punto modifiche);
- **FAB azioni** post-estrazione (tab Originale) e post-traduzione (tab lingua):
  titolo, tooltip e **voci di menu**;
- **tab Immagini**: azioni delle card (immagine e testo interpretato) e assenza
  di una mini toolbar di tab.

Rileva anche **sovrapposizioni** elementari (stessa etichetta/tooltip presente in
più aree) per guidare l'analisi. Non usa rete né corpus.

Uso::

    QT_QPA_PLATFORM=offscreen .venv/bin/python tools/ui_audit.py \\
        --json /tmp/opencode/ui_audit.json --md /tmp/opencode/ui_audit.md
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_TMP = tempfile.mkdtemp(prefix="noesis_uiaudit_")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# ``setdefault``: se il chiamante (es. un test) ha già isolato l'ambiente, non
# lo sovrascriviamo.
os.environ.setdefault("XDG_CONFIG_HOME", os.path.join(_TMP, "config"))
os.environ.setdefault("XDG_DATA_HOME", os.path.join(_TMP, "data"))
os.environ.setdefault("XDG_CACHE_HOME", os.path.join(_TMP, "cache"))


def _sample_pdf(path: Path) -> bool:
    try:
        import pymupdf
    except Exception:  # noqa: BLE001
        return False
    doc = pymupdf.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((72, 80), "Introduction", fontsize=20)
    p.insert_text((72, 120), "Body text for the audit page.", fontsize=11)
    doc.save(str(path))
    doc.close()
    return True


def _sample_png_data_uri() -> str:
    """A tiny PNG as a data URI (for the image-card inventory)."""
    try:
        import pymupdf
    except Exception:  # noqa: BLE001
        return ""
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 32, 24))
    pix.set_rect(pix.irect, (80, 140, 220))
    return "data:image/png;base64," + base64.b64encode(
        pix.tobytes("png")).decode("ascii")


def _btn_inventory(buttons) -> list[dict]:
    out = []
    for b in buttons:
        out.append({
            "text": b.text(),
            "tooltip": b.toolTip(),
            "enabled": b.isEnabled(),
        })
    return out


def inventory_from_window(w) -> dict:
    """Enumerate the action surface of an **already built** MainWindow.

    Reusable by tests (no config/env side effects).
    """
    from PyQt6.QtWidgets import QPushButton, QRadioButton

    import main

    tp = w.text_panel
    inv: dict = {}

    # ── tab bar ─────────────────────────────────────────────────────────
    inv["tabs"] = [
        {"key": "original", "label": tp._btn_original.text(),
         "checked": tp._btn_original.isChecked()},
        {"key": "translated", "label": tp._btn_translated.text(),
         "checked": tp._btn_translated.isChecked()},
        {"key": "images", "label": tp._btn_images.text(),
         "checked": tp._btn_images.isChecked()},
    ]
    inv["tab_bar_extras"] = {
        "engine_radios": [rb.text() for rb in tp._tab_bar.findChildren(
            QRadioButton)],
        "spinner_text": tp._lbl_spinner.text(),
    }

    # ── mini toolbar per finestra di testo ──────────────────────────────
    def _toolbar(tb) -> dict:
        return {
            "buttons": _btn_inventory(tb.findChildren(QPushButton)),
            "size_label": tb.lbl_size.text(),
            "dirty_label": tb.lbl_dirty.text(),
        }

    inv["text_toolbars"] = {
        "original": _toolbar(tp.origin_toolbar),
        "translated": _toolbar(tp.translated_toolbar),
    }

    # ── FAB (post-estrazione / post-traduzione): CTA unica ──────────────
    def _fab(fab) -> dict:
        return {
            "label": fab.text(),
            "tooltip": fab.toolTip(),
            "cta": True,
        }

    inv["fab"] = {
        "origin": _fab(tp._fab_origin),
        "translated": _fab(tp._fab_translated),
    }

    # ── toolbar di pagina + capsula flottante zone (Settore 4 · V08) ────
    inv["page_toolbar"] = {
        "capture": w.btn_capture.text(),
        "edit": w.btn_edit.text(),
        "edit_tooltip": w.btn_edit.toolTip(),
    }
    zb = w.zone_bar
    inv["zone_capsule"] = {
        "title": zb.lbl_title.text(),
        "count": zb.lbl_count.text(),
        "buttons": [
            {"icon": b.text(), "tooltip": b.toolTip()}
            for b in (zb.btn_exclude, zb.btn_include, zb.btn_reset,
                      zb.btn_extract, zb.btn_close)
        ],
    }

    # ── tab Oggetti: mini toolbar di tab + azioni card per tipo ─────────
    ot = tp.objects_toolbar
    uri = _sample_png_data_uri()

    def _card_actions_for(kind: str) -> list[str]:
        if not uri:
            return []
        if kind == "image":
            tp._captures.pop(uri, None)
        elif kind == "table":
            tp.set_capture(uri, "table", "| a | b |", "table", 0, rebuild=False)
        else:
            tp.set_capture(uri, "text", "hello", "native", 0, rebuild=False)
        card = tp._make_image_card(uri)
        return [b.text() for b in card.findChildren(QPushButton)]

    card_actions = {
        "image": _card_actions_for("image"),
        "table": _card_actions_for("table"),
        "text": _card_actions_for("text"),
    }
    tp._captures.pop(uri, None) if uri else None

    inv["images"] = {
        "tab_toolbar": {
            "filters": [b.text() for b in ot._buttons.values()],
            "export_all": ot.btn_export_all.text(),
            "remove_all": ot.btn_remove_all.text(),
            "count": ot.lbl_count.text(),
        },
        "card_actions": card_actions,
        "empty_hint": main.T("objects.empty"),
    }

    # ── sovrapposizioni elementari (stessa etichetta/tooltip) ───────────
    labels: dict[str, list[str]] = {}

    def _add(label: str, where: str) -> None:
        label = (label or "").strip()
        if label:
            labels.setdefault(label, []).append(where)

    for name, tb in inv["text_toolbars"].items():
        for b in tb["buttons"]:
            _add(b["tooltip"], f"toolbar.{name}")
    for name, fab in inv["fab"].items():
        _add(fab["label"], f"fab.{name}")
    for kind, actions in card_actions.items():
        for a in actions:
            _add(a, f"objects.card.{kind}")

    inv["label_overlaps"] = {
        label: where for label, where in labels.items() if len(where) > 1
    }
    return inv


def build_inventory() -> dict:
    """Build the real window headless and enumerate its action surface."""
    import time

    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    import main

    main.init_config(
        main._config_file_path(), defaults={**main.DEFAULTS, "lang": "it"})
    main.set_language("it")

    pdf = Path(_TMP) / "audit.pdf"
    if not _sample_pdf(pdf):
        raise RuntimeError("pymupdf non disponibile")

    w = main.MainWindow()
    w._resume_last_page = False
    w.show()
    w._open_pdf(pdf)
    end = time.time() + 60
    while time.time() < end and not w._final_text_cache:
        app.processEvents()
        time.sleep(0.02)
    try:
        return inventory_from_window(w)
    finally:
        w.close()


def _md(inv: dict) -> str:
    lines = ["# UI audit — superficie d'azione (finestra destra)", ""]
    lines.append("## Tab")
    for t in inv["tabs"]:
        lines.append(f"- **{t['key']}** → `{t['label']}`")
    lines.append("")
    lines.append("## Extra della tab bar")
    lines.append(f"- radio motore: {', '.join(inv['tab_bar_extras']['engine_radios'])}")
    lines.append("")
    lines.append("## Mini toolbar (finestre di testo)")
    for name, tb in inv["text_toolbars"].items():
        btns = ", ".join(f"`{b['text']}`" for b in tb["buttons"])
        lines.append(f"- **{name}**: {btns}  (size: `{tb['size_label']}`)")
    lines.append("")
    lines.append("## FAB (CTA unica)")
    for name, fab in inv["fab"].items():
        lines.append(f"- **{name}**: `{fab['label']}` — {fab['tooltip']}")
    lines.append("")
    lines.append("## Toolbar di pagina + capsula zone (✎ Edit · V08)")
    pt = inv["page_toolbar"]
    lines.append(f"- cattura: `{pt['capture']}` · edit: `{pt['edit']}` — {pt['edit_tooltip']}")
    zc = inv["zone_capsule"]
    lines.append(f"- capsula `{zc['title']}` (`{zc['count']}`):")
    for b in zc["buttons"]:
        lines.append(f"  - `{b['icon']}` — {b['tooltip'].splitlines()[0]}")
    lines.append("")
    lines.append("## Tab Oggetti")
    tt = inv["images"]["tab_toolbar"]
    lines.append(
        f"- mini toolbar di tab: filtri {' / '.join(tt['filters'])}"
        f" · {tt['export_all']} · {tt['remove_all']}")
    for kind, actions in inv["images"]["card_actions"].items():
        lines.append(f"- azioni card ({kind}): {', '.join(actions)}")
    lines.append("")
    lines.append("## Sovrapposizioni di etichetta")
    if not inv["label_overlaps"]:
        lines.append("- (nessuna etichetta condivisa)")
    for label, where in inv["label_overlaps"].items():
        lines.append(f"- `{label}` → {', '.join(where)}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Inventario UI della finestra destra.")
    ap.add_argument("--json", default=None, help="file JSON di output")
    ap.add_argument("--md", default=None, help="file markdown di output")
    args = ap.parse_args(argv)
    try:
        inv = build_inventory()
    except Exception as exc:  # noqa: BLE001
        print(f"ui_audit: impossibile costruire l'inventario: {exc!r}")
        return 2
    text = json.dumps(inv, indent=2, ensure_ascii=False)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(text, encoding="utf-8")
    if args.md:
        Path(args.md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.md).write_text(_md(inv), encoding="utf-8")
    if not args.json and not args.md:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
