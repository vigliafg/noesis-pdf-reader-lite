"""Temi dell'interfaccia (chiaro / scuro / sistema).

Modulo **puro Python** (nessuna dipendenza da PyQt): definisce due tavolozze di
token semantici e costruisce i fogli di stile QSS usati dalla GUI. Il modulo
``main`` sceglie il tema attivo (``set_mode``), lo persiste in ``config.json``
(chiave ``theme``) e applica i QSS restituiti da ``main_qss()``,
``settings_qss()`` e ``wizard_qss()``.

Regola: nessun colore deve restare cablato nel resto del codice. Ogni widget
usa i token tramite ``color("nome")`` oppure un QSS costruito qui. In questo
modo "chiaro" e "scuro" restano coerenti e si aggiunge un tema nuovo toccando
un solo file.

La modalità ``system`` segue il tema del sistema operativo: la risoluzione
effettiva avviene in ``main`` (``QStyleHints.colorScheme``), che chiama
``set_mode("dark"|"light")``. Qui ``system`` è trattato come ``dark``
(fallback) se non risolto.
"""

from __future__ import annotations

from string import Template

# ── tavolozze ────────────────────────────────────────────────────────────────
#
# I nomi dei token sono semantici (non il colore): ``bg`` è lo sfondo della
# finestra, ``text4`` un testo attenuato, ``accent`` il blu dei comandi, ecc.

DARK: dict[str, str] = {
    # superfici
    "bg": "#2b2b2b",
    "bg_alt": "#333333",
    "bg_bar": "#3a3a3a",
    "bg_input": "#444444",
    "bg_hover": "#555555",
    "bg_pressed": "#666666",
    "bg_menu": "#2b2b2b",
    "bg_scroll": "#2f2f2f",
    # bordi
    "border": "#444444",
    "border2": "#555555",
    # testi
    "text": "#eeeeee",
    "text2": "#dddddd",
    "text3": "#cccccc",
    "text4": "#aaaaaa",
    "text5": "#999999",
    "text_inv": "#ffffff",
    # accent / stati
    "accent": "#3a6bc5",
    "accent_hover": "#4a7bd5",
    "accent_text": "#ffffff",
    "sel_bg": "#3a6bc5",
    "sel_text": "#ffffff",
    "warn": "#ffcc66",
    "err": "#ff6b6b",
    "disabled_text": "#999999",
    # radio del motore di traduzione + FAB azioni
    "radio_border": "#888888",
    "radio_bg": "#444444",
    "fab_bg": "#1e3452",
    "fab_text": "#ffffff",
    "fab_border": "#5b9bff",
    "fab_hover_bg": "#27436b",
    "fab_hover_border": "#7cb2ff",
    "fab_glow": "#4800f0",
    # riflesso "scintilla" sul FAB: bianco traslucido sul tema scuro
    "fab_spark": "#d9ffffff",
    "badge": "#4a90d9",
    # menu a comparsa
    "menu_bg": "#2b2b2b",
    "menu_text": "#e8e8e8",
    "menu_border": "#555555",
    # toc / alberi
    "tree_bg": "#2b2b2b",
    "tree_text": "#dddddd",
    "tree_sel_bg": "#3a6bc5",
    "tree_sel_text": "#ffffff",
    # wizard (palette bluastra)
    "wiz_bg": "#171b23",
    "wiz_header": "#171b23",
    "wiz_line": "#2a3242",
    "wiz_panel": "#1e2430",
    "wiz_title": "#e7ecf3",
    "wiz_muted": "#93a0b4",
    "wiz_accent": "#4f8cff",
    "wiz_accent_hover": "#6ba0ff",
    "wiz_ok": "#35d0a5",
    "wiz_ok_text": "#06231b",
    "wiz_sel_bg": "#1d3350",
    "wiz_ind_border": "#5a6b86",
    "wiz_primary_text": "#08152e",
    "wiz_err": "#ff6b6b",
    # anteprima "liquida"
    "liquid_bg": "#ee0b0e13",
    "liquid_caption_bg": "#e6141414",
    "liquid_text": "#ffffff",
    "liquid_bar": "#e64f8cff",
    # documento (area testo estratta/renderizzata): resta chiara come un
    # foglio in entrambi i temi.
    "doc_bg": "#ffffff",
    "doc_text": "#1a1a1a",
    "doc_border": "#dddddd",
    "doc_code_bg": "#f0f0f0",
    "doc_code_text": "#333333",
    "doc_pre_bg": "#f5f5f5",
    "doc_th_bg": "#4a90d9",
    "doc_th_text": "#ffffff",
    "doc_row_alt": "#f8f9fa",
    "doc_h2": "#2c5f8a",
    "doc_h3": "#3a7ab5",
    "doc_strong": "#1a3a5c",
    "doc_em": "#555555",
    "doc_quote_bg": "#f0f4f8",
    "doc_quote_text": "#444444",
    "doc_link": "#4a90d9",
    # pulsanti di azione (galleria figure)
    "err_hover": "#ff5252",
    # zone manuali sul PDF (overlay di esclusione/inclusione)
    "zone_band": "#4a90d9",
    "zone_exclude": "#d9534f",
    "zone_include": "#5cb85c",
    "zone_badge": "#2e7d32",
}

LIGHT: dict[str, str] = {
    # superfici
    "bg": "#f2f3f5",
    "bg_alt": "#e9ebef",
    "bg_bar": "#e2e5ea",
    "bg_input": "#ffffff",
    "bg_hover": "#d7dbe2",
    "bg_pressed": "#c8cdd6",
    "bg_menu": "#ffffff",
    "bg_scroll": "#e6e8ec",
    # bordi
    "border": "#c9ced6",
    "border2": "#b6bcc6",
    # testi
    "text": "#1c2430",
    "text2": "#2a3442",
    "text3": "#39434f",
    "text4": "#4d5866",
    "text5": "#6a7480",
    "text_inv": "#ffffff",
    # accent / stati
    "accent": "#2f6fe0",
    "accent_hover": "#4a86f0",
    "accent_text": "#ffffff",
    "sel_bg": "#2f6fe0",
    "sel_text": "#ffffff",
    "warn": "#b06a00",
    "err": "#c0392b",
    "disabled_text": "#9aa1ac",
    # radio del motore di traduzione + FAB azioni
    "radio_border": "#8a919c",
    "radio_bg": "#ffffff",
    "fab_bg": "#ffffff",
    "fab_text": "#12305f",
    "fab_border": "#2f6fe0",
    "fab_hover_bg": "#eaf1ff",
    "fab_hover_border": "#1e6bff",
    "fab_glow": "#1e6bff",
    # riflesso "scintilla" sul FAB: blu traslucido (il bianco sarebbe
    # invisibile sul pulsante chiaro appoggiato a una pagina bianca)
    "fab_spark": "#8c2f6fe0",
    "badge": "#2f6fe0",
    # menu a comparsa
    "menu_bg": "#ffffff",
    "menu_text": "#1c2430",
    "menu_border": "#9aa3b0",
    # toc / alberi
    "tree_bg": "#ffffff",
    "tree_text": "#2a3442",
    "tree_sel_bg": "#2f6fe0",
    "tree_sel_text": "#ffffff",
    # wizard
    "wiz_bg": "#f4f6fa",
    "wiz_header": "#ffffff",
    "wiz_line": "#d7deea",
    "wiz_panel": "#ffffff",
    "wiz_title": "#1a2230",
    "wiz_muted": "#5a6a86",
    "wiz_accent": "#2f6fe0",
    "wiz_accent_hover": "#4a86f0",
    "wiz_ok": "#1f9d6b",
    "wiz_ok_text": "#ffffff",
    "wiz_sel_bg": "#dbe8ff",
    "wiz_ind_border": "#8a919c",
    "wiz_primary_text": "#ffffff",
    "wiz_err": "#c0392b",
    # anteprima "liquida"
    "liquid_bg": "#e6f2f3f5",
    "liquid_caption_bg": "#e6ffffff",
    "liquid_text": "#1c2430",
    "liquid_bar": "#e62f6fe0",
    # documento (area testo estratta/renderizzata): resta chiara come un
    # foglio in entrambi i temi.
    "doc_bg": "#ffffff",
    "doc_text": "#1a1a1a",
    "doc_border": "#dddddd",
    "doc_code_bg": "#f0f0f0",
    "doc_code_text": "#333333",
    "doc_pre_bg": "#f5f5f5",
    "doc_th_bg": "#4a90d9",
    "doc_th_text": "#ffffff",
    "doc_row_alt": "#f8f9fa",
    "doc_h2": "#2c5f8a",
    "doc_h3": "#3a7ab5",
    "doc_strong": "#1a3a5c",
    "doc_em": "#555555",
    "doc_quote_bg": "#f0f4f8",
    "doc_quote_text": "#444444",
    "doc_link": "#4a90d9",
    # pulsanti di azione (galleria figure)
    "err_hover": "#a93226",
    # zone manuali sul PDF (overlay di esclusione/inclusione)
    "zone_band": "#4a90d9",
    "zone_exclude": "#d9534f",
    "zone_include": "#5cb85c",
    "zone_badge": "#2e7d32",
}

PALETTES: dict[str, dict[str, str]] = {"dark": DARK, "light": LIGHT}

_VALID_MODES = ("dark", "light", "system")
_mode: str = "dark"
_resolved: str = "dark"


def normalize_mode(mode: str) -> str:
    """Normalizza una modalità di tema (default ``dark``)."""
    code = (mode or "").strip().lower()
    return code if code in _VALID_MODES else "dark"


def set_mode(mode: str) -> None:
    """Imposta la modalità richiesta (``dark``/``light``/``system``).

    ``system`` viene conservato come richiesta; la risoluzione effettiva va fatta
    dal chiamante con ``set_resolved`` (PyQt non è disponibile qui).
    """
    global _mode, _resolved
    _mode = normalize_mode(mode)
    if _mode != "system":
        _resolved = _mode


def set_resolved(mode: str) -> None:
    """Imposta la palette effettiva quando la modalità richiesta è ``system``."""
    global _resolved
    _resolved = normalize_mode(mode)


def mode() -> str:
    """Modalità richiesta: ``dark``, ``light`` o ``system``."""
    return _mode


def resolved() -> str:
    """Modalità effettiva usata per i colori (``dark`` o ``light``)."""
    return _resolved if _resolved in ("dark", "light") else "dark"


def is_dark() -> bool:
    return resolved() == "dark"


def palette() -> dict[str, str]:
    """Tavolozza effettiva (dict token → colore)."""
    return dict(PALETTES.get(resolved(), DARK))


def color(token: str, fallback: str = "#888888") -> str:
    """Colore del token nella palette attiva (fallback se il token manca)."""
    return PALETTES.get(resolved(), DARK).get(token, fallback)


# ── QSS: main window ─────────────────────────────────────────────────────────

_MAIN_QSS = Template(
    """
    QMainWindow { background: $bg; }
    QWidget { selection-background-color: $sel_bg; selection-color: $sel_text; }
    QToolBar {
        background: $bg_alt; padding: 4px; spacing: 6px;
        border-bottom: 1px solid $border;
    }
    QToolBar QPushButton {
        background: $bg_input; color: $text; border: 1px solid $border2;
        border-radius: 4px; padding: 6px 14px; font-size: 13px;
    }
    QToolBar QPushButton:hover { background: $bg_hover; }
    QToolBar QPushButton:pressed { background: $bg_pressed; }
    QToolBar QPushButton:checked { background: $accent; color: $accent_text; }
    QToolBar QToolButton {
        background: $bg_input; color: $text; border: 1px solid $border2;
        border-radius: 4px; padding: 6px 10px; font-size: 13px;
    }
    QToolBar QToolButton:hover { background: $bg_hover; }
    QToolBar QToolButton:pressed { background: $bg_pressed; }
    QToolBar QToolButton::menu-indicator { image: none; }
    QToolBar QSpinBox {
        background: $bg_input; color: $text; border: 1px solid $border2;
        border-radius: 4px; padding: 4px 8px; font-size: 13px;
        min-width: 60px;
    }
    /* Page-number box: no up/down buttons (they made the widget look
       cluttered); navigation is via ◀ ▶ or by typing a page number. */
    QToolBar QSpinBox::up-button, QToolBar QSpinBox::down-button {
        width: 0px; border: none; background: transparent;
    }
    QToolBar QLabel { color: $text3; font-size: 13px; }
    QStatusBar { background: $bg_alt; color: $text4; }

    /* Punti che altrimenti ereditano il tema di sistema: colori espliciti
       così l'app resta coerente su qualunque impostazione del sistema. */
    QScrollArea { background: $bg; border: none; }
    QSplitter::handle { background: $bg_alt; }
    QDockWidget { color: $text2; }
    QDockWidget::title {
        background: $bg_alt; color: $text2; padding: 5px 8px;
        text-align: left;
    }
    QDockWidget::close-button, QDockWidget::float-button {
        background: $bg_input; border: none; border-radius: 2px;
    }
    QDockWidget::close-button:hover,
    QDockWidget::float-button:hover { background: $bg_hover; }
    QScrollBar:vertical {
        background: $bg_scroll; width: 12px; margin: 0;
    }
    QScrollBar::handle:vertical {
        background: $bg_hover; min-height: 24px;
        border-radius: 6px; margin: 2px;
    }
    QScrollBar::handle:vertical:hover { background: $bg_pressed; }
    QScrollBar:horizontal {
        background: $bg_scroll; height: 12px; margin: 0;
    }
    QScrollBar::handle:horizontal {
        background: $bg_hover; min-width: 24px;
        border-radius: 6px; margin: 2px;
    }
    QScrollBar::handle:horizontal:hover { background: $bg_pressed; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        height: 0; width: 0;
    }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical,
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
        background: none;
    }
    QMenu {
        background: $bg_menu; color: $text; border: 1px solid $border2;
        padding: 4px;
    }
    QMenu::item { padding: 6px 18px; border-radius: 4px; }
    QMenu::item:selected { background: $sel_bg; color: $sel_text; }
    QMenu::separator { height: 1px; background: $border2; margin: 4px 8px; }
    QToolTip {
        background: $bg_menu; color: $text; border: 1px solid $border2;
        padding: 4px 6px;
    }
    """
)


def main_qss() -> str:
    """Foglio di stile della finestra principale."""
    return _MAIN_QSS.substitute(palette())


# ── QSS: dialoghi (Impostazioni, info cache, progresso) ──────────────────────

_SETTINGS_QSS = Template(
    """
    QDialog { background: $bg; }
    QGroupBox { color: $text; border: 1px solid $border2; border-radius: 6px;
                margin-top: 10px; padding-top: 8px; font-size: 13px; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
    QLabel { color: $text3; font-size: 13px; }
    QComboBox, QSpinBox, QDoubleSpinBox {
        background: $bg_input; color: $text; border: 1px solid $border2;
        border-radius: 4px; padding: 4px 8px; font-size: 13px;
        min-width: 220px;
    }
    QComboBox QAbstractItemView { background: $bg_input; color: $text;
        selection-background-color: $sel_bg; selection-color: $sel_text; }
    QLineEdit { background: $bg_input; color: $text; border: 1px solid $border2;
        border-radius: 4px; padding: 4px 8px; font-size: 13px; }
    QPlainTextEdit, QTextEdit { background: $bg_input; color: $text;
        border: 1px solid $border2; }
    QCheckBox { color: $text2; font-size: 13px; spacing: 8px; }
    QRadioButton { color: $text2; font-size: 13px; spacing: 8px; }
    QRadioButton::indicator {
        width: 13px; height: 13px; border: 1px solid $radio_border;
        border-radius: 7px; background: $radio_bg;
    }
    QRadioButton::indicator:checked {
        background: $accent; border-color: $accent;
    }
    QPushButton {
        background: $bg_input; color: $text; border: 1px solid $border2;
        border-radius: 4px; padding: 6px 18px; font-size: 13px;
    }
    QPushButton:hover { background: $bg_hover; }
    QPushButton:pressed { background: $bg_pressed; }
    QProgressBar {
        background: $bg_input; color: $text; border: 1px solid $border2;
        border-radius: 4px; text-align: center;
    }
    QProgressBar::chunk { background: $accent; border-radius: 3px; }
    QScrollBar:vertical { background: $bg_scroll; width: 12px; margin: 0; }
    QScrollBar::handle:vertical { background: $bg_hover; min-height: 24px;
        border-radius: 6px; margin: 2px; }
    QScrollBar::handle:vertical:hover { background: $bg_pressed; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
        background: none; }
    QScrollArea#settingsScroll { background: transparent; border: none; }
    QScrollArea#settingsScroll > QWidget > QWidget { background: transparent; }
    QWidget#settingsContent { background: transparent; }
    """
)


def settings_qss() -> str:
    """Foglio di stile dei dialoghi (Impostazioni, info cache, progresso export)."""
    return _SETTINGS_QSS.substitute(palette())


# ── QSS: wizard di esportazione ──────────────────────────────────────────────

_WIZARD_QSS = Template(
    """
    QDialog { background: $wiz_bg; }
    QScrollArea#wizScroll, QWidget#wizViewport, QWidget#wizPane {
        background: transparent; border: none;
    }
    QWidget#wizHeader { background: $wiz_header; border-bottom: 1px solid $wiz_line; }
    QLabel#wizTitle { color: $wiz_title; font-size: 14px; font-weight: 600; }
    QPushButton#wizX { background: transparent; border: 0; color: $wiz_muted;
                       font-size: 17px; padding: 0 6px; }
    QPushButton#wizX:hover { color: $wiz_title; }
    QWidget#wizSteps { background: $wiz_panel; border-bottom: 1px solid $wiz_line; }
    QLabel#wizStepNum {
        color: $wiz_muted; border: 1px solid $wiz_line; border-radius: 10px;
        min-width: 20px; max-width: 20px; min-height: 20px; max-height: 20px;
        font-size: 11px;
    }
    QLabel#wizStepName { color: $wiz_muted; font-size: 12px; }
    QWidget#wizStep[state="on"] QLabel#wizStepNum {
        border-color: $wiz_accent; color: $wiz_accent;
    }
    QWidget#wizStep[state="on"] QLabel#wizStepName { color: $wiz_title; }
    QWidget#wizStep[state="done"] QLabel#wizStepNum {
        background: $wiz_ok; border-color: $wiz_ok; color: $wiz_ok_text;
    }
    QWidget#wizStep[state="done"] QLabel#wizStepName { color: $wiz_title; }
    QFrame#wizSep { background: $wiz_line; max-height: 1px; }
    QLabel#wizH3 { color: $wiz_title; font-size: 14px; font-weight: 600; }
    QLabel#wizHint { color: $wiz_muted; font-size: 12.5px; }
    QLabel#wizEst { background: $wiz_panel; border: 1px solid $wiz_line; border-radius: 11px;
                    padding: 12px 14px; color: $wiz_muted; font-size: 12.5px; }
    QLabel#wizSum { background: $wiz_panel; border: 1px solid $wiz_line; border-radius: 11px;
                    padding: 14px; color: $wiz_title; font-size: 13px; }
    QLineEdit, QComboBox, QSpinBox {
        background: $wiz_panel; border: 1px solid $wiz_line; color: $wiz_title;
        border-radius: 9px; padding: 8px 10px; font-size: 13px;
    }
    QSpinBox#wizSpin { padding: 6px 4px; padding-right: 24px; font-size: 13px; }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: $wiz_accent; }
    QComboBox QAbstractItemView { background: $wiz_panel; color: $wiz_title;
        selection-background-color: $wiz_accent; selection-color: $wiz_primary_text; }
    QCheckBox, QRadioButton { color: $wiz_title; font-size: 13px; spacing: 8px; }
    QCheckBox::indicator {
        width: 14px; height: 14px; border: 1px solid $wiz_ind_border;
        border-radius: 4px; background: $wiz_panel;
    }
    QCheckBox::indicator:checked { background: $wiz_accent; border-color: $wiz_accent; }
    QRadioButton::indicator {
        width: 13px; height: 13px; border: 1px solid $wiz_ind_border;
        border-radius: 7px; background: $wiz_panel;
    }
    QRadioButton::indicator:checked { background: $wiz_accent; border-color: $wiz_accent; }
    QWidget#wizFooter { background: $wiz_panel; border-top: 1px solid $wiz_line; }
    QLabel#wizError { color: $wiz_err; font-size: 12px; }
    QPushButton#wizGhost { background: transparent; border: 0; color: $wiz_muted;
        padding: 9px 12px; font-size: 13px; }
    QPushButton#wizGhost:hover { color: $wiz_title; }
    QPushButton#wizBtn { background: $wiz_bg; border: 1px solid $wiz_line; color: $wiz_title;
        border-radius: 9px; padding: 9px 16px; font-size: 13px; }
    QPushButton#wizBtn:hover { border-color: $wiz_accent; }
    QPushButton#wizPrimary { background: $wiz_accent; border: 1px solid $wiz_accent;
        color: $wiz_primary_text; border-radius: 9px; padding: 9px 16px; font-size: 13px;
        font-weight: 700; }
    QPushButton#wizPrimary:hover { background: $wiz_accent_hover; }
    """
)


def wizard_qss() -> str:
    """Foglio di stile del wizard di esportazione."""
    return _WIZARD_QSS.substitute(palette())
