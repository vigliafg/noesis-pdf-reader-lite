"""UI internationalization — lightweight dict-based i18n (no Qt dependency).

The UI chrome (buttons, tooltips, status bar, dialogs) is looked up through
``T(key, **fmt)`` against ``_STRINGS``. The chosen language is a module-level
global switched at runtime (``set_language``); the choice is persisted to a
small JSON file by the application (this module only receives paths, so it
stays pure and testable without a QApplication).

The language is loaded before the UI is built, and each widget exposes a
``retranslate()`` method that re-applies the strings, enabling on-the-fly
switching without restarting.

PyInstaller note: translations live inside this module (no external resource
files), so ``--onefile`` bundles them automatically via the import.
"""

import json
import os
from pathlib import Path

__all__ = [
    "LANGUAGES",
    "TRANSLATION_LANGUAGES",
    "TRANSLATION_ENGINES",
    "DEFAULTS",
    "T",
    "get_language",
    "set_language",
    "get_source_lang",
    "set_source_lang",
    "get_target_lang",
    "set_target_lang",
    "get_translation_engine",
    "set_translation_engine",
    "flag_endonym",
    "get_config",
    "get_setting",
    "set_setting",
    "init_config",
    "load_config",
    "save_config",
    "load_language",
    "save_language",
    "ensure_config",
]

# Language codes → display names (also used for the toolbar selector).
LANGUAGES: dict[str, str] = {
    "it": "🇮🇹 Italiano",
    "en": "🇬🇧 English",
    "fr": "🇫🇷 Français",
    "de": "🇩🇪 Deutsch",
    "es": "🇪🇸 Español",
}

# Translation languages (source/target). ``auto`` is allowed for the source
# only. Values are (flag, endonym): the endonym is the language's own name,
# invariant with respect to the UI language, e.g. "🇫🇷 Français".
TRANSLATION_LANGUAGES: dict[str, tuple[str, str]] = {
    "auto": ("🌐", "Auto"),  # rilevamento automatico (solo sorgente)
    "en": ("🇬🇧", "English"),
    "it": ("🇮🇹", "Italiano"),
    "fr": ("🇫🇷", "Français"),
    "de": ("🇩🇪", "Deutsch"),
    "es": ("🇪🇸", "Español"),
    "pt": ("🇵🇹", "Português"),
    "nl": ("🇳🇱", "Nederlands"),
    "pl": ("🇵🇱", "Polski"),
    "ru": ("🇷🇺", "Русский"),
    "zh": ("🇨🇳", "中文"),
    "ja": ("🇯🇵", "日本語"),
    "ko": ("🇰🇷", "한국어"),
    "ar": ("🇸🇦", "العربية"),
    "tr": ("🇹🇷", "Türkçe"),
}

# Translation engines selectable in the settings dialog (label via T()).
TRANSLATION_ENGINES: tuple[str, ...] = ("google", "microsoft")

# Default configuration (config.json schema v2). ``last_tab``/``last_pages``
# are runtime state persisted alongside the user settings.
DEFAULTS: dict = {
    "lang": "it",           # lingua UI (LANGUAGES)
    "src_lang": "auto",     # origine traduzione (TRANSLATION_LANGUAGES)
    "dst_lang": "it",       # destinazione traduzione (TRANSLATION_LANGUAGES, no auto)
    "engine": "google",     # motore di traduzione (TRANSLATION_ENGINES)
    "zoom": 3.0,             # risoluzione base del render (0.5–4.0);
                           # lo zoom visibile è runtime (1.0 = adatta)
    "render_md": True,       # rendering Markdown on/off
    "show_header": True,     # riga "── Backend … Fix: …" on/off
    "remember_tab": True,    # riapri il pannello sull'ultima tab usata
    "resume_last_page": True,  # riprendi dall'ultima pagina del documento
    "save_edits": True,      # salva le modifiche ai testi (per documento)
    "font_size": 12,         # dimensione font testo estratto (10–16 pt)
    "theme": "dark",         # tema UI (dark|light|system) — vedi theme.py
    "notify_batch": True,    # avviso a fine esportazione batch
    "last_tab": "original",  # ultima tab attiva (original|translated|images)
    "last_pages": {},        # nome.pdf → ultima pagina (max 20, LRU)
}

_current: str = "it"
_CONFIG: dict = dict(DEFAULTS)
_CONFIG_PATH: str | None = None


def get_language() -> str:
    """Return the currently active language code."""
    return _current


def set_language(code: str) -> None:
    """Switch the active language at runtime (no-op for unknown codes)."""
    global _current
    if code in LANGUAGES:
        _current = code
        _CONFIG["lang"] = code


def get_source_lang() -> str:
    """Return the document (source) language for translation."""
    return _CONFIG.get("src_lang", "auto")


def set_source_lang(code: str) -> None:
    """Set the document (source) language (validated against the list)."""
    if code in TRANSLATION_LANGUAGES:
        _CONFIG["src_lang"] = code


def get_target_lang() -> str:
    """Return the translation (target) language."""
    return _CONFIG.get("dst_lang", "it")


def set_target_lang(code: str) -> None:
    """Set the translation (target) language (auto not allowed)."""
    if code in TRANSLATION_LANGUAGES and code != "auto":
        _CONFIG["dst_lang"] = code


def get_translation_engine() -> str:
    """Return the active translation engine id (google|microsoft)."""
    return _CONFIG.get("engine", "google")


def set_translation_engine(code: str) -> None:
    """Set the translation engine (validated against the known list)."""
    if code in TRANSLATION_ENGINES:
        _CONFIG["engine"] = code


def flag_endonym(code: str) -> str:
    """Flag + endonym for a translation language, e.g. "🇫🇷 Français"."""
    flag, name = TRANSLATION_LANGUAGES.get(code, ("", code))
    return f"{flag} {name}".strip()


# ═══════════════════════════════════════════════════════════════════════════════
#  strings — every key must exist in every language
# ═══════════════════════════════════════════════════════════════════════════════

_STRINGS: dict[str, dict[str, str]] = {
    # ── main toolbar ────────────────────────────────────────────────────────
    "toolbar.nav": {
        "it": "Navigazione", "en": "Navigation", "fr": "Navigation",
        "de": "Navigation", "es": "Navegación",
    },
    "toolbar.open": {
        "it": "📂 Apri PDF", "en": "📂 Open PDF", "fr": "📂 Ouvrir un PDF",
        "de": "📂 PDF öffnen", "es": "📂 Abrir PDF",
    },
    "toolbar.toc": {
        "it": "📑 Indice", "en": "📑 Index", "fr": "📑 Sommaire",
        "de": "📑 Inhaltsverzeichnis", "es": "📑 Índice",
    },
    "toolbar.toc.tip": {
        "it": "Mostra/nascondi l'indice (TOC) del PDF",
        "en": "Show/hide the PDF table of contents (TOC)",
        "fr": "Afficher/masquer la table des matières (TOC) du PDF",
        "de": "Inhaltsverzeichnis (TOC) des PDF ein-/ausblenden",
        "es": "Mostrar/ocultar el índice (TOC) del PDF",
    },
    "toolbar.prev": {
        "it": "◀ Prec.", "en": "◀ Prev.", "fr": "◀ Préc.",
        "de": "◀ Zurück", "es": "◀ Ant.",
    },
    "toolbar.next": {
        "it": "Succ. ▶", "en": "Next ▶", "fr": "Suiv. ▶",
        "de": "Weiter ▶", "es": "Sig. ▶",
    },
    "toolbar.of": {
        "it": "di", "en": "of", "fr": "de", "de": "von", "es": "de",
    },
    "toolbar.printed": {
        "it": "· st. {n}", "en": "· p. {n}", "fr": "· p. {n}",
        "de": "· S. {n}", "es": "· p. {n}",
    },
    "toolbar.reextract": {
        "it": "↻ Riestrai pagina", "en": "↻ Re-extract page",
        "fr": "↻ Réextraire la page", "de": "↻ Seite neu extrahieren",
        "es": "↻ Reextraer página",
    },
    "toolbar.clear_cache": {
        "it": "🧹 Svuota cache", "en": "🧹 Clear cache",
        "fr": "🧹 Vider le cache", "de": "🧹 Cache leeren",
        "es": "🧹 Vaciar caché",
    },
    "cache.regenerating": {
        "it": "Riestrazione pagina {page}…",
        "en": "Re-extracting page {page}…",
        "fr": "Réextraction de la page {page}…",
        "de": "Seite {page} wird neu extrahiert…",
        "es": "Reextrayendo página {page}…",
    },
    "cache.clearing": {
        "it": "Svuotamento cache in corso…",
        "en": "Clearing cache…",
        "fr": "Vidage du cache…",
        "de": "Cache wird geleert…",
        "es": "Vaciando caché…",
    },
    "cache.cleared": {
        "it": "Cache svuotata.", "en": "Cache cleared.",
        "fr": "Cache vidé.", "de": "Cache geleert.", "es": "Caché vaciada.",
    },
    "cache.cleared_hint": {
        "it": "Cache svuotata.\n\nNessun testo in memoria per questa pagina: "
              "premi «↻ Riestrai pagina» per rieseguire l'estrazione.",
        "en": "Cache cleared.\n\nNo text in memory for this page: press "
              "«↻ Re-extract page» to run the extraction again.",
        "fr": "Cache vidé.\n\nAucun texte en mémoire pour cette page : appuyez "
              "sur «↻ Réextraire la page» pour relancer l'extraction.",
        "de": "Cache geleert.\n\nKein Text im Speicher für diese Seite: "
              "„↻ Seite neu extrahieren“ drücken, um die Extraktion zu starten.",
        "es": "Caché vaciada.\n\nNo hay texto en memoria para esta página: "
              "pulsa «↻ Reextraer página» para repetir la extracción.",
    },
    "toolbar.reextract.tip": {
        "it": "Rigenera la pagina: svuota la cache della pagina e riesegue "
              "estrazione e traduzione",
        "en": "Re-extract the page: clear the page cache and rerun extraction "
              "and translation",
        "fr": "Régénérer la page : vide le cache de la page et relance "
              "extraction et traduction",
        "de": "Seite neu erzeugen: Seiten-Cache leeren und Extraktion/"
              "Übersetzung erneut ausführen",
        "es": "Regenerar la página: vacía la caché de la página y relanza "
              "extracción y traducción",
    },
    "toolbar.clear_cache.tip": {
        "it": "Svuota la cache dell'intero documento (tutte le pagine) e rigenera",
        "en": "Clear the whole document's cache (all pages) and regenerate",
        "fr": "Vider le cache de tout le document (toutes les pages) et régénérer",
        "de": "Den Cache des gesamten Dokuments leeren (alle Seiten) und neu "
              "erzeugen",
        "es": "Vaciar la caché de todo el documento (todas las páginas) y "
              "regenerar",
    },
    "toolbar.cache_info.tip": {
        "it": "Info cache: quale file/cartella l'app usa per la pagina corrente",
        "en": "Cache info: which file/folder the app uses for the current page",
        "fr": "Infos cache : quel fichier/dossier l'app utilise pour la page",
        "de": "Cache-Info: welche Datei/Ordner die App für die Seite nutzt",
        "es": "Info de caché: qué archivo/carpeta usa la app para la página",
    },
    "cache.info.title": {
        "it": "Info cache", "en": "Cache info", "fr": "Infos cache",
        "de": "Cache-Info", "es": "Info de caché",
    },
    "cache.info.copy": {
        "it": "Copia", "en": "Copy", "fr": "Copier", "de": "Kopieren",
        "es": "Copiar",
    },
    "cache.info.close": {
        "it": "Chiudi", "en": "Close", "fr": "Fermer", "de": "Schließen",
        "es": "Cerrar",
    },
    "cache.info.copied": {
        "it": "Info cache copiate negli appunti", "en": "Cache info copied",
        "fr": "Infos cache copiées", "de": "Cache-Info kopiert",
        "es": "Info de caché copiada",
    },
    "cache.clear.title": {
        "it": "Svuota cache", "en": "Clear cache", "fr": "Vider le cache",
        "de": "Cache leeren", "es": "Vaciar caché",
    },
    "cache.clear.confirm": {
        "it": "Svuotare la cache dell'intero documento? Le pagine verranno "
              "riestratte al prossimo accesso.",
        "en": "Clear the whole document's cache? Pages will be re-extracted on "
              "next view.",
        "fr": "Vider le cache de tout le document ? Les pages seront "
              "ré-extraites au prochain affichage.",
        "de": "Den Cache des gesamten Dokuments leeren? Die Seiten werden beim "
              "nächsten Aufruf neu extrahiert.",
        "es": "¿Vaciar la caché de todo el documento? Las páginas se "
              "reextraerán al volver a verlas.",
    },
    "toolbar.zoom_out.tip": {
        "it": "Riduci zoom (Ctrl+-)", "en": "Zoom out (Ctrl+-)",
        "fr": "Zoom arrière (Ctrl+-)", "de": "Verkleinern (Strg+-)",
        "es": "Alejar (Ctrl+-)",
    },
    "toolbar.zoom_in.tip": {
        "it": "Aumenta zoom (Ctrl++)", "en": "Zoom in (Ctrl++)",
        "fr": "Zoom avant (Ctrl++)", "de": "Vergrößern (Strg++)",
        "es": "Acercar (Ctrl++)",
    },
    "toolbar.zoom.scale": {
        "it": "Scala: {x}x", "en": "Scale: {x}x", "fr": "Échelle : {x}x",
        "de": "Skalierung: {x}x", "es": "Escala: {x}x",
    },
    "toolbar.md.on": {
        "it": "📝 MD ✓", "en": "📝 MD ✓", "fr": "📝 MD ✓",
        "de": "📝 MD ✓", "es": "📝 MD ✓",
    },
    "toolbar.md.plain": {
        "it": "📝 Plain", "en": "📝 Plain", "fr": "📝 Texte brut",
        "de": "📝 Klartext", "es": "📝 Texto plano",
    },
    "toolbar.md.tip": {
        "it": "Attiva/disattiva rendering Markdown → HTML\n(Ctrl+M per toggle)",
        "en": "Toggle Markdown rendering → HTML\n(Ctrl+M to toggle)",
        "fr": "Activer/désactiver le rendu Markdown → HTML\n(Ctrl+M pour basculer)",
        "de": "Markdown-Rendering → HTML ein-/ausschalten\n(Strg+M zum Umschalten)",
        "es": "Activar/desactivar el renderizado Markdown → HTML\n(Ctrl+M para alternar)",
    },
    "toolbar.help": {
        "it": "❓ Guida", "en": "❓ Help", "fr": "❓ Aide",
        "de": "❓ Hilfe", "es": "❓ Ayuda",
    },
    "toolbar.help.tip": {
        "it": "Apre la guida online nel browser",
        "en": "Opens the online help in the browser",
        "fr": "Ouvre l'aide en ligne dans le navigateur",
        "de": "Öffnet die Online-Hilfe im Browser",
        "es": "Abre la ayuda en línea en el navegador",
    },
    # ── page toolbar ────────────────────────────────────────────────────────
    "page_toolbar.title": {
        "it": "Pagina", "en": "Page", "fr": "Page", "de": "Seite", "es": "Página",
    },
    "page_toolbar.capture": {
        "it": "📸 Cattura", "en": "📸 Capture",
        "fr": "📸 Capturer", "de": "📸 Erfassen",
        "es": "📸 Capturar",
    },
    "page_toolbar.capture.tip": {
        "it": "Trascina col mouse su un'immagine, una tabella o un altro\noggetto della pagina per catturarlo nella tab 🖼️ Immagini",
        "en": "Drag the mouse over an image, a table or any other\nobject on the page to capture it into the 🖼️ Images tab",
        "fr": "Glissez la souris sur une image, un tableau ou tout autre\nobjet de la page pour le capturer dans l'onglet 🖼️ Images",
        "de": "Ziehen Sie mit der Maus über ein Bild, eine Tabelle oder ein\nanderes Objekt der Seite, um es in den Tab 🖼️ Bilder aufzunehmen",
        "es": "Arrastra el ratón sobre una imagen, una tabla o cualquier otro\nobjeto de la página para capturarlo en la pestaña 🖼️ Imágenes",
    },
    "page_toolbar.capture.blocked": {
        "it": "Fai il reset delle zone prima di catturare",
        "en": "Reset the zones before capturing",
        "fr": "Réinitialisez les zones avant de capturer",
        "de": "Setzen Sie die Zonen zurück, bevor Sie erfassen",
        "es": "Restablece las zonas antes de capturar",
    },
    "page_toolbar.capture.image": {
        "it": "📸 Cattura immagine", "en": "📸 Capture image",
        "fr": "📸 Capturer l'image", "de": "📸 Bild erfassen",
        "es": "📸 Capturar imagen",
    },
    "page_toolbar.capture.interpret": {
        "it": "🔤 Cattura e interpreta", "en": "🔤 Capture and interpret",
        "fr": "🔤 Capturer et interpréter", "de": "🔤 Erfassen und interpretieren",
        "es": "🔤 Capturar e interpretar",
    },
    "page_toolbar.capture.interpret.tip": {
        "it": "Cattura la regione e ne estrae il testo (nativo o OCR)",
        "en": "Capture the region and extract its text (native or OCR)",
        "fr": "Capture la zone et en extrait le texte (natif ou OCR)",
        "de": "Erfasst den Bereich und extrahiert den Text (nativ oder OCR)",
        "es": "Captura la zona y extrae su texto (nativo u OCR)",
    },
    "page_toolbar.capture.active": {
        "it": "📸 Disegna la zona…", "en": "📸 Draw the region…",
        "fr": "📸 Dessinez la zone…", "de": "📸 Bereich zeichnen…",
        "es": "📸 Dibuja la zona…",
    },
    "capture.text_ready": {
        "it": "Testo interpretato ({chars} caratteri)",
        "en": "Text interpreted ({chars} characters)",
        "fr": "Texte interprété ({chars} caractères)",
        "de": "Text interpretiert ({chars} Zeichen)",
        "es": "Texto interpretado ({chars} caracteres)",
    },
    "capture.empty": {
        "it": "Nessun testo riconosciuto nella zona",
        "en": "No text recognised in the region",
        "fr": "Aucun texte reconnu dans la zone",
        "de": "Kein Text im Bereich erkannt",
        "es": "No se reconoció texto en la zona",
    },
    "capture.copy_text": {
        "it": "📋 Copia testo", "en": "📋 Copy text",
        "fr": "📋 Copier le texte", "de": "📋 Text kopieren",
        "es": "📋 Copiar texto",
    },
    "capture.save_dialog": {
        "it": "Salva testo interpretato", "en": "Save interpreted text",
        "fr": "Enregistrer le texte interprété",
        "de": "Interpretierten Text speichern",
        "es": "Guardar texto interpretado",
    },
    "page_toolbar.extract": {
        "it": "▶ Estrai", "en": "▶ Extract",
        "fr": "▶ Extraire", "de": "▶ Extrahieren",
        "es": "▶ Extraer",
    },
    "page_toolbar.extract.tip": {
        "it": "Conclude l'editing delle zone e riesegue l'estrazione della pagina",
        "en": "Ends zone editing and re-runs the page extraction",
        "fr": "Termine l'édition des zones et relance l'extraction de la page",
        "de": "Beendet die Zonenbearbeitung und startet die Seitenextraktion neu",
        "es": "Termina la edición de zonas y vuelve a extraer la página",
    },
    "page_toolbar.exclude": {
        "it": "🚫 Escludi zona", "en": "🚫 Exclude region",
        "fr": "🚫 Exclure une zone", "de": "🚫 Bereich ausschließen",
        "es": "🚫 Excluir zona",
    },
    "page_toolbar.exclude.tip": {
        "it": "Trascina col mouse una zona (header, footer, immagine,\ndidascalia…) per escluderla: il motore adattativo riordina\nil testo rimanente. È aggiuntivo al sistema automatico.\n\nSe la zona contiene un'immagine, viene estratta anche nella\ntab 🖼️ Immagini (escludi + estrai in un solo gesto).",
        "en": "Drag a region (header, footer, image, caption…) with the\nmouse to exclude it: the adaptive engine reorders the\nremaining text. It adds to the automatic system.\n\nIf the region contains an image, it is also extracted into\nthe 🖼️ Images tab (exclude + extract in one gesture).",
        "fr": "Glissez une zone (en-tête, pied de page, image,\nlégende…) pour l'exclure : le moteur adaptatif réordonne\nle texte restant. C'est un ajout au système automatique.\n\nSi la zone contient une image, elle est aussi extraite dans\nl'onglet 🖼️ Images (exclure + extraire en un seul geste).",
        "de": "Ziehen Sie einen Bereich (Kopfzeile, Fußzeile, Bild,\nBildunterschrift…) zum Ausschließen: Die adaptive Engine\nordnet den verbleibenden Text neu. Ergänzend zum\nautomatischen System.\n\nEnthält der Bereich ein Bild, wird es auch in den Tab\n🖼️ Bilder extrahiert (Ausschließen + Extrahieren in einem\nSchritt).",
        "es": "Arrastra una zona (encabezado, pie de página, imagen,\nleyenda…) para excluirla: el motor adaptativo reordena\nel texto restante. Es adicional al sistema automático.\n\nSi la zona contiene una imagen, también se extrae en la\npestaña 🖼️ Imágenes (excluir + extraer en un solo gesto).",
    },
    "page_toolbar.include": {
        "it": "🟩 Includi zona", "en": "🟩 Include region",
        "fr": "🟩 Inclure une zone", "de": "🟩 Bereich einschließen",
        "es": "🟩 Incluir zona",
    },
    "page_toolbar.include.tip": {
        "it": "Trascina col mouse i box verdi nell'ordine di lettura che vuoi:\nil testo verrà ricostruito seguendo la numerazione (1, 2, 3…).\nUn box verde = una colonna/regione di lettura.",
        "en": "Drag the green boxes with the mouse in the reading order\nyou want: the text is rebuilt following the numbering (1, 2, 3…).\nOne green box = one reading column/region.",
        "fr": "Glissez les boîtes vertes avec la souris dans l'ordre de\nlecture souhaité : le texte est reconstruit selon la\nnumérotation (1, 2, 3…). Une boîte verte = une\ncolonne/région de lecture.",
        "de": "Ziehen Sie die grünen Boxen mit der Maus in der\ngewünschten Lesereihenfolge: Der Text wird gemäß der\nNummerierung (1, 2, 3…) neu aufgebaut. Eine grüne Box =\neine Lesespalte/-region.",
        "es": "Arrastra los recuadros verdes con el ratón en el orden de\nlectura que quieras: el texto se reconstruye siguiendo la\nnumeración (1, 2, 3…). Un recuadro verde = una\ncolumna/región de lectura.",
    },
    "page_toolbar.reset": {
        "it": "🧹 Reset zone", "en": "🧹 Reset zones",
        "fr": "🧹 Réinitialiser les zones", "de": "🧹 Zonen zurücksetzen",
        "es": "🧹 Restablecer zonas",
    },
    "page_toolbar.reset.tip": {
        "it": "Rimuove tutte le zone (rosse e verdi) dalla pagina corrente",
        "en": "Removes all zones (red and green) from the current page",
        "fr": "Supprime toutes les zones (rouges et vertes) de la page courante",
        "de": "Entfernt alle Zonen (rot und grün) von der aktuellen Seite",
        "es": "Elimina todas las zonas (rojas y verdes) de la página actual",
    },
    # ── right panel tabs ────────────────────────────────────────────────────
    "tab.original": {
        "it": "📄 Originale", "en": "📄 Original", "fr": "📄 Original",
        "de": "📄 Original", "es": "📄 Original",
    },
    "tab.objects": {
        "it": "🗂️ Oggetti", "en": "🗂️ Objects", "fr": "🗂️ Objets",
        "de": "🗂️ Objekte", "es": "🗂️ Objetos",
    },
    # ── text editor mini-toolbar ────────────────────────────────────────────
    "editor.decrease": {
        "it": "Riduci testo", "en": "Decrease text size",
        "fr": "Réduire le texte", "de": "Text verkleinern", "es": "Reducir texto",
    },
    "editor.increase": {
        "it": "Aumenta testo", "en": "Increase text size",
        "fr": "Agrandir le texte", "de": "Text vergrößern", "es": "Aumentar texto",
    },
    "editor.reset": {
        "it": "Ripristina dimensione", "en": "Reset size",
        "fr": "Réinitialiser la taille", "de": "Größe zurücksetzen",
        "es": "Restablecer tamaño",
    },
    "editor.export": {
        "it": "Esporta testo", "en": "Export text",
        "fr": "Exporter le texte", "de": "Text exportieren",
        "es": "Exportar texto",
    },
    "editor.export_dialog": {
        "it": "Salva testo come...", "en": "Save text as...",
        "fr": "Enregistrer le texte sous...", "de": "Text speichern unter...",
        "es": "Guardar texto como...",
    },
    "editor.export_filter": {
        "it": "Markdown (*.md);;Testo (*.txt)",
        "en": "Markdown (*.md);;Text (*.txt)",
        "fr": "Markdown (*.md);;Texte (*.txt)",
        "de": "Markdown (*.md);;Text (*.txt)",
        "es": "Markdown (*.md);;Texto (*.txt)",
    },
    "editor.export_error": {
        "it": "❌ Errore di esportazione", "en": "❌ Export error",
        "fr": "❌ Erreur d'exportation", "de": "❌ Exportfehler",
        "es": "❌ Error de exportación",
    },
    "editor.unsaved": {
        "it": "Modifiche non salvate", "en": "Unsaved edits",
        "fr": "Modifications non enregistrées",
        "de": "Nicht gespeicherte Änderungen", "es": "Cambios sin guardar",
    },
    # Azioni locali della finestra (mini toolbar): copia.
    "toolbar.copy.tip": {
        "it": "Copia il testo", "en": "Copy the text",
        "fr": "Copier le texte", "de": "Text kopieren", "es": "Copiar el texto",
    },
    # ── spinner / gallery status ────────────────────────────────────────────
    "status.translating": {
        "it": "⏳ Traducendo...", "en": "⏳ Translating...",
        "fr": "⏳ Traduction...", "de": "⏳ Übersetzen...",
        "es": "⏳ Traduciendo...",
    },
    "status.translating_engine": {
        "it": "⏳ Traducendo con {engine}…", "en": "⏳ Translating with {engine}…",
        "fr": "⏳ Traduction avec {engine}…", "de": "⏳ Übersetzen mit {engine}…",
        "es": "⏳ Traduciendo con {engine}…",
    },
    "status.translation_error": {
        "it": "⚠️ Traduzione fallita ({engine}): {reason} — riprova o scegli l'altro motore nelle Impostazioni",
        "en": "⚠️ Translation failed ({engine}): {reason} — retry or choose the other engine in Settings",
        "fr": "⚠️ Échec de la traduction ({engine}) : {reason} — réessayez ou choisissez l'autre moteur dans Paramètres",
        "de": "⚠️ Übersetzung fehlgeschlagen ({engine}): {reason} — erneut versuchen oder die andere Engine in den Einstellungen wählen",
        "es": "⚠️ Error de traducción ({engine}): {reason} — reinténtalo o elige el otro motor en Configuración",
    },
    "toast.engine_changed": {
        "it": "⚙️ Engine cambiato: {engine}",
        "en": "⚙️ Engine changed: {engine}",
        "fr": "⚙️ Moteur changé : {engine}",
        "de": "⚙️ Engine geändert: {engine}",
        "es": "⚙️ Motor cambiado: {engine}",
    },
    "status.extracting": {
        "it": "⏳ Estrazione in corso...", "en": "⏳ Extracting...",
        "fr": "⏳ Extraction en cours...", "de": "⏳ Extraktion läuft...",
        "es": "⏳ Extrayendo...",
    },
    "status.copied": {
        "it": "✅ Copiata", "en": "✅ Copied", "fr": "✅ Copiée",
        "de": "✅ Kopiert", "es": "✅ Copiada",
    },
    "status.saved": {
        "it": "✅ Salvata", "en": "✅ Saved", "fr": "✅ Enregistrée",
        "de": "✅ Gespeichert", "es": "✅ Guardada",
    },
    "status.exported": {
        "it": "✅ Esportato", "en": "✅ Exported", "fr": "✅ Exporté",
        "de": "✅ Exportiert", "es": "✅ Exportado",
    },
    # ── objects tab (raccolta di oggetti: immagini / tabelle / testi) ───────
    "objects.empty": {
        "it": "Nessun oggetto.\n\nUsa 📸 Cattura per ritagliare un'immagine, una tabella o del testo dalla pagina.",
        "en": "No objects.\n\nUse 📸 Capture to crop an image, a table or text from the page.",
        "fr": "Aucun objet.\n\nUtilisez 📸 Capturer pour découper une image, un tableau ou du texte de la page.",
        "de": "Keine Objekte.\n\nVerwenden Sie 📸 Erfassen, um ein Bild, eine Tabelle oder Text aus der Seite auszuschneiden.",
        "es": "No hay objetos.\n\nUsa 📸 Capturar para recortar una imagen, una tabla o texto de la página.",
    },
    "objects.filter.all": {
        "it": "Tutti", "en": "All", "fr": "Tous", "de": "Alle", "es": "Todos",
    },
    "objects.filter.image": {
        "it": "Immagini", "en": "Images", "fr": "Images", "de": "Bilder",
        "es": "Imágenes",
    },
    "objects.filter.table": {
        "it": "Tabelle", "en": "Tables", "fr": "Tableaux", "de": "Tabellen",
        "es": "Tablas",
    },
    "objects.filter.text": {
        "it": "Testi", "en": "Texts", "fr": "Textes", "de": "Texte",
        "es": "Textos",
    },
    "objects.count": {
        "it": "{n} oggetti", "en": "{n} objects", "fr": "{n} objets",
        "de": "{n} Objekte", "es": "{n} objetos",
    },
    "objects.export_all": {
        "it": "💾 Esporta tutti", "en": "💾 Export all", "fr": "💾 Exporter tout",
        "de": "💾 Alle exportieren", "es": "💾 Exportar todo",
    },
    "objects.remove_all": {
        "it": "🗑️ Rimuovi tutti", "en": "🗑️ Remove all",
        "fr": "🗑️ Tout supprimer", "de": "🗑️ Alle entfernen",
        "es": "🗑️ Eliminar todo",
    },
    "objects.export_dialog": {
        "it": "Esporta tutti gli oggetti", "en": "Export all objects",
        "fr": "Exporter tous les objets", "de": "Alle Objekte exportieren",
        "es": "Exportar todos los objetos",
    },
    "objects.exported": {
        "it": "✅ Oggetti esportati", "en": "✅ Objects exported",
        "fr": "✅ Objets exportés", "de": "✅ Objekte exportiert",
        "es": "✅ Objetos exportados",
    },
    "objects.kind.image": {
        "it": "Immagine", "en": "Image", "fr": "Image", "de": "Bild",
        "es": "Imagen",
    },
    "objects.kind.table": {
        "it": "Tabella", "en": "Table", "fr": "Tableau", "de": "Tabelle",
        "es": "Tabla",
    },
    "objects.kind.text": {
        "it": "Testo", "en": "Text", "fr": "Texte", "de": "Text",
        "es": "Texto",
    },
    "objects.copy_md": {
        "it": "📋 Copia markdown", "en": "📋 Copy markdown",
        "fr": "📋 Copier le markdown", "de": "📋 Markdown kopieren",
        "es": "📋 Copiar markdown",
    },
    "objects.save_md": {
        "it": "💾 Salva .md", "en": "💾 Save .md", "fr": "💾 Enregistrer .md",
        "de": "💾 Als .md speichern", "es": "💾 Guardar .md",
    },
    "gallery.zoom_tip": {
        "it": "Clicca per ingrandire", "en": "Click to enlarge",
        "fr": "Cliquez pour agrandir", "de": "Zum Vergrößern klicken",
        "es": "Haz clic para ampliar",
    },
    "gallery.save": {
        "it": "💾 Salva", "en": "💾 Save", "fr": "💾 Enregistrer",
        "de": "💾 Speichern", "es": "💾 Guardar",
    },
    "gallery.copy": {
        "it": "📋 Copia", "en": "📋 Copy", "fr": "📋 Copier",
        "de": "📋 Kopieren", "es": "📋 Copiar",
    },
    "gallery.remove": {
        "it": "🗑️ Rimuovi", "en": "🗑️ Remove", "fr": "🗑️ Supprimer",
        "de": "🗑️ Entfernen", "es": "🗑️ Eliminar",
    },
    "gallery.save_dialog": {
        "it": "Salva immagine", "en": "Save image",
        "fr": "Enregistrer l'image", "de": "Bild speichern",
        "es": "Guardar imagen",
    },
    "gallery.save_filter": {
        "it": "PNG (*.png);;JPEG (*.jpg);;Tutti i file (*)",
        "en": "PNG (*.png);;JPEG (*.jpg);;All files (*)",
        "fr": "PNG (*.png);;JPEG (*.jpg);;Tous les fichiers (*)",
        "de": "PNG (*.png);;JPEG (*.jpg);;Alle Dateien (*)",
        "es": "PNG (*.png);;JPEG (*.jpg);;Todos los archivos (*)",
    },
    # ── dock / status bar ───────────────────────────────────────────────────
    "dock.toc": {
        "it": "Indice", "en": "Table of contents", "fr": "Sommaire",
        "de": "Inhaltsverzeichnis", "es": "Índice",
    },
    "status.ready": {
        "it": "Pronto — apri un file PDF con 📂 Apri PDF  |  Backend testo: PyMuPDF4LLM ⚡",
        "en": "Ready — open a PDF with 📂 Open PDF  |  Text backend: PyMuPDF4LLM ⚡",
        "fr": "Prêt — ouvrez un PDF avec 📂 Ouvrir un PDF  |  Backend texte : PyMuPDF4LLM ⚡",
        "de": "Bereit — öffnen Sie ein PDF mit 📂 PDF öffnen  |  Text-Backend: PyMuPDF4LLM ⚡",
        "es": "Listo — abre un PDF con 📂 Abrir PDF  |  Backend de texto: PyMuPDF4LLM ⚡",
    },
    "status.no_image": {
        "it": "Niente da catturare nella zona selezionata",
        "en": "Nothing to capture in the selected region",
        "fr": "Rien à capturer dans la zone sélectionnée",
        "de": "Nichts im ausgewählten Bereich zu erfassen",
        "es": "Nada que capturar en la zona seleccionada",
    },
    "status.image_extracted": {
        "it": "Catturato dalla zona: {name}",
        "en": "Captured from region: {name}",
        "fr": "Capturé depuis la zone : {name}",
        "de": "Aus Bereich erfasst: {name}",
        "es": "Capturado de la zona: {name}",
    },
    "status.zone_excluded": {
        "it": "Zona esclusa ({count} sulla pagina) — trascina altre zone o premi ▶ Estrai per concludere",
        "en": "Region excluded ({count} on page) — drag more regions or press ▶ Extract to finish",
        "fr": "Zone exclue ({count} sur la page) — faites glisser d'autres zones ou appuyez sur ▶ Extraire pour terminer",
        "de": "Bereich ausgeschlossen ({count} auf der Seite) — ziehen Sie weitere Bereiche oder drücken Sie ▶ Extrahieren zum Beenden",
        "es": "Zona excluida ({count} en la página) — arrastra más zonas o pulsa ▶ Extraer para terminar",
    },
    "status.zone_excluded_image": {
        "it": "Zona esclusa e oggetto catturato ({name}) — trascina altre zone o premi ▶ Estrai per concludere",
        "en": "Region excluded and object captured ({name}) — drag more regions or press ▶ Extract to finish",
        "fr": "Zone exclue et objet capturé ({name}) — faites glisser d'autres zones ou appuyez sur ▶ Extraire pour terminer",
        "de": "Bereich ausgeschlossen und Objekt erfasst ({name}) — ziehen Sie weitere Bereiche oder drücken Sie ▶ Extrahieren zum Beenden",
        "es": "Zona excluida y objeto capturado ({name}) — arrastra más zonas o pulsa ▶ Extraer para terminar",
    },
    "status.zone_included": {
        "it": "Zona inclusa (n. {count}) — disegna il prossimo box nell'ordine di lettura o premi ▶ Estrai per concludere",
        "en": "Region included (no. {count}) — draw the next box in reading order or press ▶ Extract to finish",
        "fr": "Zone incluse (n° {count}) — dessinez la boîte suivante dans l'ordre de lecture ou appuyez sur ▶ Extraire pour terminer",
        "de": "Bereich eingeschlossen (Nr. {count}) — zeichnen Sie die nächste Box in Lesereihenfolge oder drücken Sie ▶ Extrahieren zum Beenden",
        "es": "Zona incluida (n.º {count}) — dibuja el siguiente recuadro en orden de lectura o pulsa ▶ Extraer para terminar",
    },
    "status.zones_reset": {
        "it": "Zone rimosse per questa pagina",
        "en": "Zones removed for this page",
        "fr": "Zones supprimées pour cette page",
        "de": "Zonen für diese Seite entfernt",
        "es": "Zonas eliminadas para esta página",
    },
    "status.page": {
        "it": "Pagina {page} di {total}  —  {name}  |  Testo: PyMuPDF4LLM ⚡ ({ms} ms)",
        "en": "Page {page} of {total}  —  {name}  |  Text: PyMuPDF4LLM ⚡ ({ms} ms)",
        "fr": "Page {page} sur {total}  —  {name}  |  Texte : PyMuPDF4LLM ⚡ ({ms} ms)",
        "de": "Seite {page} von {total}  —  {name}  |  Text: PyMuPDF4LLM ⚡ ({ms} ms)",
        "es": "Página {page} de {total}  —  {name}  |  Texto: PyMuPDF4LLM ⚡ ({ms} ms)",
    },
    "status.empty_pdf": {
        "it": "PDF senza pagine", "en": "PDF with no pages",
        "fr": "PDF sans pages", "de": "PDF ohne Seiten", "es": "PDF sin páginas",
    },
    # ── extraction header / engine labels ───────────────────────────────────
    "header.line": {
        "it": "── Backend: PyMuPDF4LLM ⚡  │  {ms} ms  │  {chars} caratteri  │  Fix: {label}  │  OCR: {ocr}  │  Trad: {engine} ──\n\n",
        "en": "── Backend: PyMuPDF4LLM ⚡  │  {ms} ms  │  {chars} characters  │  Fix: {label}  │  OCR: {ocr}  │  Transl: {engine} ──\n\n",
        "fr": "── Backend : PyMuPDF4LLM ⚡  │  {ms} ms  │  {chars} caractères  │  Correctifs : {label}  │  OCR : {ocr}  │  Trad. : {engine} ──\n\n",
        "de": "── Backend: PyMuPDF4LLM ⚡  │  {ms} ms  │  {chars} Zeichen  │  Fix: {label}  │  OCR: {ocr}  │  Übers.: {engine} ──\n\n",
        "es": "── Backend: PyMuPDF4LLM ⚡  │  {ms} ms  │  {chars} caracteres  │  Fix: {label}  │  OCR: {ocr}  │  Trad.: {engine} ──\n\n",
    },
    "engine.label.auto": {
        "it": "Engine adattativo", "en": "Adaptive engine",
        "fr": "Moteur adaptatif", "de": "Adaptive Engine", "es": "Motor adaptativo",
    },
    "engine.label.manual": {
        "it": "Zone manuali", "en": "Manual zones",
        "fr": "Zones manuelles", "de": "Manuelle Zonen", "es": "Zonas manuales",
    },
    "engine.option.google": {
        "it": "Google Translate", "en": "Google Translate",
        "fr": "Google Translate", "de": "Google Translate", "es": "Google Translate",
    },
    "engine.option.microsoft": {
        "it": "Microsoft Edge (gratuito)", "en": "Microsoft Edge (Free)",
        "fr": "Microsoft Edge (gratuit)", "de": "Microsoft Edge (kostenlos)",
        "es": "Microsoft Edge (gratis)",
    },
    "engine.short.google": {
        "it": "Google", "en": "Google",
        "fr": "Google", "de": "Google", "es": "Google",
    },
    "engine.short.microsoft": {
        "it": "Microsoft", "en": "Microsoft",
        "fr": "Microsoft", "de": "Microsoft", "es": "Microsoft",
    },
    # ── dialogs ─────────────────────────────────────────────────────────────
    "dlg.open": {
        "it": "Apri PDF", "en": "Open PDF", "fr": "Ouvrir un PDF",
        "de": "PDF öffnen", "es": "Abrir PDF",
    },
    "dlg.open_filter": {
        "it": "PDF Files (*.pdf);;All Files (*)",
        "en": "PDF Files (*.pdf);;All Files (*)",
        "fr": "Fichiers PDF (*.pdf);;Tous les fichiers (*)",
        "de": "PDF-Dateien (*.pdf);;Alle Dateien (*)",
        "es": "Archivos PDF (*.pdf);;Todos los archivos (*)",
    },
    "dlg.error": {
        "it": "Errore", "en": "Error", "fr": "Erreur", "de": "Fehler", "es": "Error",
    },
    "dlg.file_not_found": {
        "it": "File non trovato:\n{path}", "en": "File not found:\n{path}",
        "fr": "Fichier introuvable :\n{path}", "de": "Datei nicht gefunden:\n{path}",
        "es": "Archivo no encontrado:\n{path}",
    },
    "dlg.pdf_error": {
        "it": "Errore PDF", "en": "PDF Error", "fr": "Erreur PDF",
        "de": "PDF-Fehler", "es": "Error de PDF",
    },
    "dlg.cannot_open": {
        "it": "Impossibile aprire il PDF:\n{e}",
        "en": "Unable to open the PDF:\n{e}",
        "fr": "Impossible d'ouvrir le PDF :\n{e}",
        "de": "PDF kann nicht geöffnet werden:\n{e}",
        "es": "No se puede abrir el PDF:\n{e}",
    },
    # ── page view messages ──────────────────────────────────────────────────
    "view.start_hint": {
        "it": "Apri un PDF per iniziare", "en": "Open a PDF to start",
        "fr": "Ouvrez un PDF pour commencer", "de": "Öffnen Sie ein PDF, um zu beginnen",
        "es": "Abre un PDF para empezar",
    },
    "view.page_unavailable": {
        "it": "(pagina non disponibile)", "en": "(page unavailable)",
        "fr": "(page indisponible)", "de": "(Seite nicht verfügbar)",
        "es": "(página no disponible)",
    },
    "view.no_pymupdf": {
        "it": "(pymupdf non installato)", "en": "(pymupdf not installed)",
        "fr": "(pymupdf non installé)", "de": "(pymupdf nicht installiert)",
        "es": "(pymupdf no instalado)",
    },
    "view.empty_pdf": {
        "it": "(PDF vuoto)", "en": "(empty PDF)", "fr": "(PDF vide)",
        "de": "(leeres PDF)", "es": "(PDF vacío)",
    },
    # ── extraction fallbacks (shown in the text panel) ──────────────────────
    "extract.no_pymupdf4llm": {
        "it": "(pymupdf4llm non installato — esegui: pip install pymupdf4llm)",
        "en": "(pymupdf4llm not installed — run: pip install pymupdf4llm)",
        "fr": "(pymupdf4llm non installé — exécutez : pip install pymupdf4llm)",
        "de": "(pymupdf4llm nicht installiert — ausführen: pip install pymupdf4llm)",
        "es": "(pymupdf4llm no instalado — ejecuta: pip install pymupdf4llm)",
    },
    "extract.empty_page": {
        "it": "(nessun testo estraibile su questa pagina)",
        "en": "(no extractable text on this page)",
        "fr": "(aucun texte extractible sur cette page)",
        "de": "(kein extrahierbarer Text auf dieser Seite)",
        "es": "(no hay texto extraíble en esta página)",
    },
    "extract.error": {
        "it": "(errore pymupdf4llm: {e})", "en": "(pymupdf4llm error: {e})",
        "fr": "(erreur pymupdf4llm : {e})", "de": "(pymupdf4llm-Fehler: {e})",
        "es": "(error de pymupdf4llm: {e})",
    },
    # ── table of contents ───────────────────────────────────────────────────
    "toc.no_title": {
        "it": "(senza titolo)", "en": "(untitled)", "fr": "(sans titre)",
        "de": "(ohne Titel)", "es": "(sin título)",
    },
    "toc.page_fmt": {
        "it": "{title}  ·  p. {page}", "en": "{title}  ·  p. {page}",
        "fr": "{title}  ·  p. {page}", "de": "{title}  ·  S. {page}",
        "es": "{title}  ·  p. {page}",
    },
    # ── settings dialog ────────────────────────────────────────────────────
    "settings.button": {
        "it": "⚙️ Impostazioni", "en": "⚙️ Settings",
        "fr": "⚙️ Paramètres", "de": "⚙️ Einstellungen",
        "es": "⚙️ Configuración",
    },
    "settings.button.tip": {
        "it": "Lingua interfaccia, lingue di traduzione e preferenze",
        "en": "Interface language, translation languages and preferences",
        "fr": "Langue de l'interface, langues de traduction et préférences",
        "de": "Oberflächensprache, Übersetzungssprachen und Einstellungen",
        "es": "Idioma de la interfaz, idiomas de traducción y preferencias",
    },
    "settings.title": {
        "it": "Impostazioni", "en": "Settings", "fr": "Paramètres",
        "de": "Einstellungen", "es": "Configuración",
    },
    "settings.group.lang": {
        "it": "Lingua", "en": "Language", "fr": "Langue",
        "de": "Sprache", "es": "Idioma",
    },
    "settings.lang.ui": {
        "it": "Lingua interfaccia", "en": "Interface language",
        "fr": "Langue de l'interface", "de": "Oberflächensprache",
        "es": "Idioma de la interfaz",
    },
    "settings.lang.source": {
        "it": "Lingua del documento (origine)",
        "en": "Document language (source)",
        "fr": "Langue du document (source)",
        "de": "Dokumentensprache (Quelle)",
        "es": "Idioma del documento (origen)",
    },
    "settings.lang.target": {
        "it": "Lingua della traduzione (destinazione)",
        "en": "Translation language (target)",
        "fr": "Langue de traduction (cible)",
        "de": "Übersetzungssprache (Ziel)",
        "es": "Idioma de traducción (destino)",
    },
    "settings.group.translation": {
        "it": "Traduzione", "en": "Translation", "fr": "Traduction",
        "de": "Übersetzung", "es": "Traducción",
    },
    "settings.translation.engine": {
        "it": "Motore di traduzione", "en": "Translation engine",
        "fr": "Moteur de traduction", "de": "Übersetzungs-Engine",
        "es": "Motor de traducción",
    },
    "settings.group.text": {
        "it": "Testo", "en": "Text", "fr": "Texte",
        "de": "Text", "es": "Texto",
    },
    "settings.text.font": {
        "it": "Dimensione font", "en": "Font size",
        "fr": "Taille de police", "de": "Schriftgröße",
        "es": "Tamaño de fuente",
    },
    "settings.text.md": {
        "it": "Rendering Markdown", "en": "Markdown rendering",
        "fr": "Rendu Markdown", "de": "Markdown-Rendering",
        "es": "Renderizado Markdown",
    },
    "settings.text.header": {
        "it": "Mostra l'header di estrazione",
        "en": "Show extraction header",
        "fr": "Afficher l'en-tête d'extraction",
        "de": "Extraktions-Header anzeigen",
        "es": "Mostrar la cabecera de extracción",
    },
    "settings.edits.save": {
        "it": "Salva le modifiche ai testi",
        "en": "Save text edits",
        "fr": "Enregistrer les modifications de texte",
        "de": "Textänderungen speichern",
        "es": "Guardar cambios de texto",
    },
    "settings.edits.clear": {
        "it": "🗑️ Cancella modifiche salvate",
        "en": "🗑️ Clear saved edits",
        "fr": "🗑️ Effacer les modifications enregistrées",
        "de": "🗑️ Gespeicherte Änderungen löschen",
        "es": "🗑️ Borrar cambios guardados",
    },
    "settings.edits.clear_confirm": {
        "it": "Cancellare le modifiche salvate di questo documento?",
        "en": "Delete the saved edits of this document?",
        "fr": "Supprimer les modifications enregistrées de ce document ?",
        "de": "Gespeicherte Änderungen dieses Dokuments löschen?",
        "es": "¿Borrar los cambios guardados de este documento?",
    },
    "settings.edits.clear_done": {
        "it": "✅ Modifiche cancellate", "en": "✅ Edits cleared",
        "fr": "✅ Modifications effacées", "de": "✅ Änderungen gelöscht",
        "es": "✅ Cambios borrados",
    },
    "settings.group.view": {
        "it": "Visualizzazione", "en": "View", "fr": "Affichage",
        "de": "Anzeige", "es": "Vista",
    },
    "settings.view.zoom": {
        "it": "Zoom di avvio", "en": "Initial zoom", "fr": "Zoom initial",
        "de": "Start-Zoom", "es": "Zoom inicial",
    },
    "settings.group.behavior": {
        "it": "Comportamento", "en": "Behavior", "fr": "Comportement",
        "de": "Verhalten", "es": "Comportamiento",
    },
    "settings.behavior.resume": {
        "it": "Riprendi dall'ultima pagina del documento",
        "en": "Resume at the document's last page",
        "fr": "Reprendre à la dernière page du document",
        "de": "An der letzten Seite des Dokuments fortfahren",
        "es": "Reanudar en la última página del documento",
    },
    "settings.behavior.tab": {
        "it": "Ricorda l'ultima tab del pannello destro",
        "en": "Remember the last right-panel tab",
        "fr": "Mémoriser le dernier onglet du panneau droit",
        "de": "Letzten Tab des rechten Bereichs merken",
        "es": "Recordar la última pestaña del panel derecho",
    },
    "settings.ok": {
        "it": "OK", "en": "OK", "fr": "OK", "de": "OK", "es": "OK",
    },
    "settings.cancel": {
        "it": "Annulla", "en": "Cancel", "fr": "Annuler",
        "de": "Abbrechen", "es": "Cancelar",
    },
    # ── toolbar: esporta batch + reader ─────────────────────────────────
    "toolbar.export": {
        "it": "💾 Esporta", "en": "💾 Export", "fr": "💾 Exporter",
        "de": "💾 Exportieren", "es": "💾 Exportar",
    },
    "toolbar.export.tip": {
        "it": "Esporta un gruppo di pagine (originale/traduzione)…",
        "en": "Export a group of pages (original/translation)…",
        "fr": "Exporter un groupe de pages (original/traduction)…",
        "de": "Eine Gruppe von Seiten exportieren (Original/Übersetzung)…",
        "es": "Exportar un grupo de páginas (original/traducción)…",
    },
    "toolbar.reader": {
        "it": "👁 Reader ▾", "en": "👁 Reader ▾", "fr": "👁 Reader ▾",
        "de": "👁 Reader ▾", "es": "👁 Reader ▾",
    },
    "toolbar.reader.tip": {
        "it": "Apri il reader interno o il visualizzatore di sistema",
        "en": "Open the built-in reader or the system viewer",
        "fr": "Ouvrir le lecteur intégré ou la visionneuse système",
        "de": "Den integrierten Reader oder den Systembetrachter öffnen",
        "es": "Abrir el lector integrado o el visor del sistema",
    },
    "toolbar.reader.internal": {
        "it": "Reader interno", "en": "Built-in reader",
        "fr": "Lecteur intégré", "de": "Integrierter Reader",
        "es": "Lector integrado",
    },
    "toolbar.reader.external": {
        "it": "Apri con il visualizzatore di sistema",
        "en": "Open with the system viewer",
        "fr": "Ouvrir avec la visionneuse système",
        "de": "Mit dem Systembetrachter öffnen",
        "es": "Abrir con el visor del sistema",
    },
    # ── reader interno ──────────────────────────────────────────────────
    "reader.title": {
        "it": "Reader — {name}", "en": "Reader — {name}",
        "fr": "Lecteur — {name}", "de": "Reader — {name}",
        "es": "Lector — {name}",
    },
    "reader.prev": {"it": "◀", "en": "◀", "fr": "◀", "de": "◀", "es": "◀"},
    "reader.next": {"it": "▶", "en": "▶", "fr": "▶", "de": "▶", "es": "▶"},
    "reader.of": {
        "it": "di {total}", "en": "of {total}", "fr": "sur {total}",
        "de": "von {total}", "es": "de {total}",
    },
    "reader.zoom_in": {"it": "🔍+", "en": "🔍+", "fr": "🔍+", "de": "🔍+", "es": "🔍+"},
    "reader.zoom_out": {"it": "🔍−", "en": "🔍−", "fr": "🔍−", "de": "🔍−", "es": "🔍−"},
    "reader.fit_width": {
        "it": "⇔ larghezza", "en": "⇔ width", "fr": "⇔ largeur",
        "de": "⇔ Breite", "es": "⇔ ancho",
    },
    "reader.fit_page": {
        "it": "⇕ pagina", "en": "⇕ page", "fr": "⇕ page",
        "de": "⇕ Seite", "es": "⇕ página",
    },
    "reader.rotate": {
        "it": "⟳ ruota", "en": "⟳ rotate", "fr": "⟳ pivoter",
        "de": "⟳ drehen", "es": "⟳ girar",
    },
    "reader.external": {
        "it": "👁 visualizzatore di sistema", "en": "👁 system viewer",
        "fr": "👁 visionneuse système", "de": "👁 Systembetrachter",
        "es": "👁 visor del sistema",
    },
    "reader.goto": {
        "it": "Vai a pagina", "en": "Go to page", "fr": "Aller à la page",
        "de": "Gehe zu Seite", "es": "Ir a la página",
    },
    "reader.zoom_in.tip": {
        "it": "Ingrandisci", "en": "Zoom in", "fr": "Zoom avant",
        "de": "Vergrößern", "es": "Acercar",
    },
    "reader.zoom_out.tip": {
        "it": "Riduci", "en": "Zoom out", "fr": "Zoom arrière",
        "de": "Verkleinern", "es": "Alejar",
    },
    "reader.fit_width.tip": {
        "it": "Adatta alla larghezza", "en": "Fit to width",
        "fr": "Ajuster à la largeur", "de": "An Breite anpassen",
        "es": "Ajustar al ancho",
    },
    "reader.fit_page.tip": {
        "it": "Adatta alla pagina", "en": "Fit to page",
        "fr": "Ajuster à la page", "de": "An Seite anpassen",
        "es": "Ajustar a la página",
    },
    "reader.rotate.tip": {
        "it": "Ruota di 90°", "en": "Rotate 90°", "fr": "Pivoter de 90°",
        "de": "Um 90° drehen", "es": "Girar 90°",
    },
    "reader.external.tip": {
        "it": "Apri con il visualizzatore di sistema",
        "en": "Open with the system viewer",
        "fr": "Ouvrir avec la visionneuse système",
        "de": "Mit dem Systembetrachter öffnen",
        "es": "Abrir con el visor del sistema",
    },
    # ── FAB (CTA unica) + azioni locali (mini toolbar) ──────────────────
    "actions.origin.translate": {
        "it": "🌐 Traduci pagina", "en": "🌐 Translate page",
        "fr": "🌐 Traduire la page", "de": "🌐 Seite übersetzen",
        "es": "🌐 Traducir página",
    },
    "actions.origin.cta.tip": {
        "it": "Passa alla traduzione di questa pagina",
        "en": "Go to this page's translation",
        "fr": "Passer à la traduction de cette page",
        "de": "Zur Übersetzung dieser Seite",
        "es": "Ir a la traducción de esta página",
    },
    "actions.origin.reextract": {
        "it": "🔁 Ri-estrai pagina", "en": "🔁 Re-extract page",
        "fr": "🔁 Ré-extraire la page", "de": "🔁 Seite neu extrahieren",
        "es": "🔁 Re-extraer página",
    },
    "actions.translated.retranslate": {
        "it": "🔁 Ritraduci", "en": "🔁 Retranslate",
        "fr": "🔁 Retraduire", "de": "🔁 Neu übersetzen",
        "es": "🔁 Retraducir",
    },
    "actions.translated.next": {
        "it": "▶ Traduci la successiva", "en": "▶ Translate the next",
        "fr": "▶ Traduire la suivante", "de": "▶ Nächste übersetzen",
        "es": "▶ Traducir la siguiente",
    },
    "actions.translated.cta.tip": {
        "it": "Passa alla pagina successiva e traducila",
        "en": "Go to the next page and translate it",
        "fr": "Passer à la page suivante et la traduire",
        "de": "Zur nächsten Seite wechseln und übersetzen",
        "es": "Ir a la página siguiente y traducirla",
    },
    "actions.last_page": {
        "it": "Sei già all'ultima pagina.", "en": "You are already on the last page.",
        "fr": "Vous êtes déjà à la dernière page.",
        "de": "Sie sind bereits auf der letzten Seite.",
        "es": "Ya estás en la última página.",
    },
    "actions.retranslate_confirm": {
        "it": "Ritradurre la pagina {page}? La traduzione attuale verrà eliminata e rigenerata.",
        "en": "Retranslate page {page}? The current translation will be discarded and regenerated.",
        "fr": "Retraduire la page {page} ? La traduction actuelle sera supprimée et régénérée.",
        "de": "Seite {page} neu übersetzen? Die aktuelle Übersetzung wird verworfen und neu erzeugt.",
        "es": "¿Retraducir la página {page}? La traducción actual se eliminará y se regenerará.",
    },
    "actions.copied": {
        "it": "Copiato negli appunti.", "en": "Copied to the clipboard.",
        "fr": "Copié dans le presse-papiers.", "de": "In die Zwischenablage kopiert.",
        "es": "Copiado al portapapeles.",
    },
    "actions.copy_empty": {
        "it": "Niente da copiare.", "en": "Nothing to copy.",
        "fr": "Rien à copier.", "de": "Nichts zu kopieren.",
        "es": "Nada que copiar.",
    },
    # ── mini-toolbar: gruppo Zone ───────────────────────────────────────
    "page_toolbar.zone_group": {
        "it": "🎯 Zone ▾", "en": "🎯 Zones ▾", "fr": "🎯 Zones ▾",
        "de": "🎯 Zonen ▾", "es": "🎯 Zonas ▾",
    },
    "page_toolbar.zone.tip": {
        "it": "Escludi/includi zone o azzera",
        "en": "Exclude/include regions or reset",
        "fr": "Exclure/inclure des zones ou réinitialiser",
        "de": "Zonen ausschließen/einschließen oder zurücksetzen",
        "es": "Excluir/incluir zonas o restablecer",
    },
    # ── esportazione batch: wizard ──────────────────────────────────────
    "export.wizard.title": {
        "it": "Esporta pagine estratte e tradotte",
        "en": "Export extracted and translated pages",
        "fr": "Exporter les pages extraites et traduites",
        "de": "Extrahierte und übersetzte Seiten exportieren",
        "es": "Exportar páginas extraídas y traducidas",
    },
    "export.wizard.back": {
        "it": "← Indietro", "en": "← Back", "fr": "← Retour",
        "de": "← Zurück", "es": "← Atrás",
    },
    "export.wizard.next": {
        "it": "Avanti →", "en": "Next →", "fr": "Suivant →",
        "de": "Weiter →", "es": "Siguiente →",
    },
    "export.wizard.start": {
        "it": "Avvia l'esportazione", "en": "Start export",
        "fr": "Lancer l'export", "de": "Export starten",
        "es": "Iniciar la exportación",
    },
    "export.wizard.step.pages": {
        "it": "Pagine", "en": "Pages", "fr": "Pages",
        "de": "Seiten", "es": "Páginas",
    },
    "export.wizard.step.content": {
        "it": "Contenuto", "en": "Content", "fr": "Contenu",
        "de": "Inhalt", "es": "Contenido",
    },
    "export.wizard.step.langs": {
        "it": "Lingue e motore", "en": "Languages and engine",
        "fr": "Langues et moteur", "de": "Sprachen und Engine",
        "es": "Idiomas y motor",
    },
    "export.wizard.step.output": {
        "it": "Output", "en": "Output", "fr": "Sortie",
        "de": "Ausgabe", "es": "Salida",
    },
    "export.wizard.step.summary": {
        "it": "Riepilogo", "en": "Summary", "fr": "Récapitulatif",
        "de": "Zusammenfassung", "es": "Resumen",
    },
    "export.wizard.pages.title": {
        "it": "Quali pagine?", "en": "Which pages?", "fr": "Quelles pages ?",
        "de": "Welche Seiten?", "es": "¿Qué páginas?",
    },
    "export.wizard.pages.hint": {
        "it": "Esporta la pagina corrente, un intervallo o una lista.",
        "en": "Export the current page, a range or a list.",
        "fr": "Exporter la page courante, une plage ou une liste.",
        "de": "Aktuelle Seite, einen Bereich oder eine Liste exportieren.",
        "es": "Exportar la página actual, un rango o una lista.",
    },
    "export.wizard.content.title": {
        "it": "Cosa esportare?", "en": "What to export?",
        "fr": "Quoi exporter ?", "de": "Was exportieren?",
        "es": "¿Qué exportar?",
    },
    "export.wizard.content.hint": {
        "it": "Origine, traduzione o entrambe; figure incluse o no.",
        "en": "Original, translation or both; include figures or not.",
        "fr": "Original, traduction ou les deux ; figures incluses ou non.",
        "de": "Original, Übersetzung oder beides; Abbildungen inklusive oder nicht.",
        "es": "Original, traducción o ambos; incluir figuras o no.",
    },
    "export.wizard.langs.title": {
        "it": "Lingue e motore", "en": "Languages and engine",
        "fr": "Langues et moteur", "de": "Sprachen und Engine",
        "es": "Idiomas y motor",
    },
    "export.wizard.langs.hint": {
        "it": "Origine «Auto» riconosce la lingua da sola.",
        "en": "Source “Auto” detects the language by itself.",
        "fr": "Source « Auto » détecte la langue toute seule.",
        "de": "Quelle „Auto“ erkennt die Sprache selbst.",
        "es": "Origen «Auto» reconoce el idioma por sí solo.",
    },
    "export.wizard.output.title": {
        "it": "Output", "en": "Output", "fr": "Sortie",
        "de": "Ausgabe", "es": "Salida",
    },
    "export.wizard.output.hint": {
        "it": "Ultimo controllo: nome, formato e destinazione.",
        "en": "Final check: name, format and destination.",
        "fr": "Dernière vérification : nom, format et destination.",
        "de": "Letzte Kontrolle: Name, Format und Ziel.",
        "es": "Última comprobación: nombre, formato y destino.",
    },
    "export.wizard.summary.title": {
        "it": "Riepilogo", "en": "Summary", "fr": "Récapitulatif",
        "de": "Zusammenfassung", "es": "Resumen",
    },
    "export.wizard.output.browse": {
        "it": "Sfoglia…", "en": "Browse…", "fr": "Parcourir…",
        "de": "Durchsuchen…", "es": "Examinar…",
    },
    "export.wizard.output.folder": {
        "it": "Cartella destinazione", "en": "Destination folder",
        "fr": "Dossier de destination", "de": "Zielordner",
        "es": "Carpeta de destino",
    },
    "export.wizard.output.filename": {
        "it": "Nome file", "en": "File name", "fr": "Nom du fichier",
        "de": "Dateiname", "es": "Nombre de archivo",
    },
    "export.wizard.preview.count": {
        "it": "{n} pagine selezionate · {label}",
        "en": "{n} pages selected · {label}",
        "fr": "{n} pages sélectionnées · {label}",
        "de": "{n} Seiten ausgewählt · {label}",
        "es": "{n} páginas seleccionadas · {label}",
    },
    "export.wizard.preview.none": {
        "it": "Anteprima non disponibile.", "en": "Preview unavailable.",
        "fr": "Aperçu indisponible.", "de": "Vorschau nicht verfügbar.",
        "es": "Vista previa no disponible.",
    },
    "export.wizard.preview.caption": {
        "it": "pagina {n}", "en": "page {n}", "fr": "page {n}",
        "de": "Seite {n}", "es": "página {n}",
    },
    # ── esportazione batch: selezione pagine ────────────────────────────
    "export.mode.current": {
        "it": "Pagina corrente", "en": "Current page",
        "fr": "Page courante", "de": "Aktuelle Seite",
        "es": "Página actual",
    },
    "export.mode.range": {
        "it": "Intervallo di pagine", "en": "Page range",
        "fr": "Plage de pages", "de": "Seitenbereich",
        "es": "Rango de páginas",
    },
    "export.mode.free": {
        "it": "Pagine (1,3,7-9)", "en": "Pages (1,3,7-9)",
        "fr": "Pages (1,3,7-9)", "de": "Seiten (1,3,7-9)",
        "es": "Páginas (1,3,7-9)",
    },
    "export.range.from.short": {
        "it": "Da", "en": "From", "fr": "De", "de": "Von", "es": "Desde",
    },
    "export.range.to.short": {
        "it": "A", "en": "To", "fr": "À", "de": "Bis", "es": "Hasta",
    },
    "export.free.placeholder": {
        "it": "es. 1,3,7-9", "en": "e.g. 1,3,7-9", "fr": "ex. 1,3,7-9",
        "de": "z. B. 1,3,7-9", "es": "p. ej. 1,3,7-9",
    },
    "export.free.hint": {
        "it": "Pagine singole o intervalli separati da virgole: 1,3,7-9. Usa «all» per tutte le pagine.",
        "en": "Single pages or ranges separated by commas: 1,3,7-9. Use “all” for every page.",
        "fr": "Pages seules ou plages séparées par des virgules : 1,3,7-9. Utilisez « all » pour toutes.",
        "de": "Einzelne Seiten oder Bereiche mit Kommas: 1,3,7-9. „all“ für alle Seiten.",
        "es": "Páginas sueltas o rangos separados por comas: 1,3,7-9. Usa «all» para todas.",
    },
    "export.free.err.bounds": {
        "it": "Pagina fuori intervallo (1–{total}).",
        "en": "Page out of range (1–{total}).",
        "fr": "Page hors plage (1–{total}).",
        "de": "Seite außerhalb des Bereichs (1–{total}).",
        "es": "Página fuera de rango (1–{total}).",
    },
    "export.free.err.empty": {
        "it": "Indica almeno una pagina (es. 1,3,7-9).",
        "en": "Enter at least one page (e.g. 1,3,7-9).",
        "fr": "Indiquez au moins une page (ex. 1,3,7-9).",
        "de": "Mindestens eine Seite angeben (z. B. 1,3,7-9).",
        "es": "Indica al menos una página (p. ej. 1,3,7-9).",
    },
    "export.free.err.no_pages": {
        "it": "Il documento non contiene pagine.",
        "en": "The document has no pages.",
        "fr": "Le document ne contient aucune page.",
        "de": "Das Dokument enthält keine Seiten.",
        "es": "El documento no contiene páginas.",
    },
    "export.free.err.none": {
        "it": "Nessuna pagina selezionata.", "en": "No page selected.",
        "fr": "Aucune page sélectionnée.", "de": "Keine Seite ausgewählt.",
        "es": "Ninguna página seleccionada.",
    },
    "export.free.err.range": {
        "it": "Intervallo non valido: «{token}».",
        "en": "Invalid range: “{token}”.",
        "fr": "Plage non valide : « {token} ».",
        "de": "Ungültiger Bereich: „{token}“.",
        "es": "Rango no válido: «{token}».",
    },
    "export.free.err.token": {
        "it": "Voce non valida: «{token}».",
        "en": "Invalid entry: “{token}”.",
        "fr": "Entrée non valide : « {token} ».",
        "de": "Ungültiger Eintrag: „{token}“.",
        "es": "Entrada no válida: «{token}».",
    },
    "export.free.err.too_many": {
        "it": "Troppe pagine richieste.", "en": "Too many pages requested.",
        "fr": "Trop de pages demandées.", "de": "Zu viele Seiten angefordert.",
        "es": "Demasiadas páginas solicitadas.",
    },
    # ── esportazione batch: contenuto e formato ─────────────────────────
    "export.mode.original": {
        "it": "Originale (markdown estratto)",
        "en": "Original (extracted markdown)",
        "fr": "Original (markdown extrait)",
        "de": "Original (extrahiertes Markdown)",
        "es": "Original (markdown extraído)",
    },
    "export.mode.translated": {
        "it": "Traduzione", "en": "Translation", "fr": "Traduction",
        "de": "Übersetzung", "es": "Traducción",
    },
    "export.mode.both": {
        "it": "Entrambi (originale + traduzione)",
        "en": "Both (original + translation)",
        "fr": "Les deux (original + traduction)",
        "de": "Beides (Original + Übersetzung)",
        "es": "Ambos (original + traducción)",
    },
    "export.include_images": {
        "it": "Includi le figure (gallery)",
        "en": "Include figures (gallery)",
        "fr": "Inclure les figures (galerie)",
        "de": "Abbildungen einbeziehen (Galerie)",
        "es": "Incluir figuras (galería)",
    },
    "export.format.merged": {
        "it": "Un unico Markdown", "en": "A single Markdown",
        "fr": "Un seul Markdown", "de": "Ein einziges Markdown",
        "es": "Un único Markdown",
    },
    "export.format.zip": {
        "it": "Pagine singole (ZIP)", "en": "Single pages (ZIP)",
        "fr": "Pages séparées (ZIP)", "de": "Einzelne Seiten (ZIP)",
        "es": "Páginas sueltas (ZIP)",
    },
    "export.format.folder": {
        "it": "Pagine singole in una cartella",
        "en": "Single pages in a folder",
        "fr": "Pages séparées dans un dossier",
        "de": "Einzelne Seiten in einem Ordner",
        "es": "Páginas sueltas en una carpeta",
    },
    "export.translate_missing": {
        "it": "Traduci prima le pagine mancanti (attesa)",
        "en": "Translate missing pages first (wait)",
        "fr": "Traduire d'abord les pages manquantes (attente)",
        "de": "Fehlende Seiten zuerst übersetzen (Wartezeit)",
        "es": "Traducir primero las páginas que faltan (espera)",
    },
    # ── esportazione batch: riepilogo ───────────────────────────────────
    "export.sum.file": {
        "it": "File", "en": "File", "fr": "Fichier", "de": "Datei", "es": "Archivo",
    },
    "export.sum.pages": {
        "it": "Pagine", "en": "Pages", "fr": "Pages", "de": "Seiten", "es": "Páginas",
    },
    "export.sum.content": {
        "it": "Contenuto", "en": "Content", "fr": "Contenu",
        "de": "Inhalt", "es": "Contenido",
    },
    "export.sum.langs": {
        "it": "Lingue", "en": "Languages", "fr": "Langues",
        "de": "Sprachen", "es": "Idiomas",
    },
    "export.sum.missing": {
        "it": "Da elaborare", "en": "To process", "fr": "À traiter",
        "de": "Zu verarbeiten", "es": "Por procesar",
    },
    "export.sum.output": {
        "it": "Output", "en": "Output", "fr": "Sortie",
        "de": "Ausgabe", "es": "Salida",
    },
    # ── esportazione batch: progresso e esiti ───────────────────────────
    "export.progress.title": {
        "it": "Esportazione in corso", "en": "Export in progress",
        "fr": "Export en cours", "de": "Export läuft",
        "es": "Exportación en curso",
    },
    "export.progress.engine_lang": {
        "it": "Motore: {engine} · {src} → {dst}",
        "en": "Engine: {engine} · {src} → {dst}",
        "fr": "Moteur : {engine} · {src} → {dst}",
        "de": "Engine: {engine} · {src} → {dst}",
        "es": "Motor: {engine} · {src} → {dst}",
    },
    "export.progress.pages": {
        "it": "Pagine: {label}", "en": "Pages: {label}",
        "fr": "Pages : {label}", "de": "Seiten: {label}",
        "es": "Páginas: {label}",
    },
    "export.progress.activity_extracting": {
        "it": "📄 Estrazione pagina {page}…", "en": "📄 Extracting page {page}…",
        "fr": "📄 Extraction de la page {page}…",
        "de": "📄 Seite {page} wird extrahiert…",
        "es": "📄 Extrayendo la página {page}…",
    },
    "export.progress.activity_translating": {
        "it": "🌐 Traduzione pagina {page}…", "en": "🌐 Translating page {page}…",
        "fr": "🌐 Traduction de la page {page}…",
        "de": "🌐 Seite {page} wird übersetzt…",
        "es": "🌐 Traduciendo la página {page}…",
    },
    "export.progress.page_ok": {
        "it": "✓ pag {page}", "en": "✓ p. {page}", "fr": "✓ p. {page}",
        "de": "✓ S. {page}", "es": "✓ pág. {page}",
    },
    "export.progress.page_fail": {
        "it": "✗ pag {page} — {reason}", "en": "✗ p. {page} — {reason}",
        "fr": "✗ p. {page} — {reason}", "de": "✗ S. {page} — {reason}",
        "es": "✗ pág. {page} — {reason}",
    },
    "export.progress.stats": {
        "it": "{done} elaborate · {cached} in cache · {failed} errori · ETA ~{eta}",
        "en": "{done} done · {cached} cached · {failed} errors · ETA ~{eta}",
        "fr": "{done} traitées · {cached} en cache · {failed} erreurs · ETA ~{eta}",
        "de": "{done} erledigt · {cached} im Cache · {failed} Fehler · ETA ~{eta}",
        "es": "{done} hechas · {cached} en caché · {failed} errores · ETA ~{eta}",
    },
    "export.progress.completed_title": {
        "it": "✅ Esportazione completata", "en": "✅ Export completed",
        "fr": "✅ Export terminé", "de": "✅ Export abgeschlossen",
        "es": "✅ Exportación completada",
    },
    "export.progress.completed_summary": {
        "it": "{count} pagine · non riuscite: {failed} · tempo: {elapsed}",
        "en": "{count} pages · failed: {failed} · time: {elapsed}",
        "fr": "{count} pages · échecs : {failed} · temps : {elapsed}",
        "de": "{count} Seiten · fehlgeschlagen: {failed} · Zeit: {elapsed}",
        "es": "{count} páginas · fallidas: {failed} · tiempo: {elapsed}",
    },
    "export.progress.saved_path": {
        "it": "File: {path}", "en": "File: {path}", "fr": "Fichier : {path}",
        "de": "Datei: {path}", "es": "Archivo: {path}",
    },
    "export.progress.open_folder": {
        "it": "📂 Apri cartella", "en": "📂 Open folder",
        "fr": "📂 Ouvrir le dossier", "de": "📂 Ordner öffnen",
        "es": "📂 Abrir carpeta",
    },
    "export.progress.save_download": {
        "it": "⬇ Copia in Download", "en": "⬇ Copy to Downloads",
        "fr": "⬇ Copier dans Téléchargements",
        "de": "⬇ In Downloads kopieren",
        "es": "⬇ Copiar a Descargas",
    },
    "export.progress.cancelling": {
        "it": "Interruzione…", "en": "Stopping…", "fr": "Interruption…",
        "de": "Wird abgebrochen…", "es": "Interrumpiendo…",
    },
    "export.progress.close": {
        "it": "Chiudi", "en": "Close", "fr": "Fermer",
        "de": "Schließen", "es": "Cerrar",
    },
    "export.error": {
        "it": "❌ Errore di esportazione", "en": "❌ Export error",
        "fr": "❌ Erreur d'export", "de": "❌ Exportfehler",
        "es": "❌ Error de exportación",
    },
    "export.cancelled": {
        "it": "Esportazione annullata.", "en": "Export cancelled.",
        "fr": "Export annulé.", "de": "Export abgebrochen.",
        "es": "Exportación cancelada.",
    },
    "export.busy": {
        "it": "Un'esportazione è già in corso: attendi che finisca.",
        "en": "An export is already running: wait for it to finish.",
        "fr": "Un export est déjà en cours : attendez la fin.",
        "de": "Ein Export läuft bereits: warten Sie, bis er endet.",
        "es": "Ya hay una exportación en curso: espera a que termine.",
    },
    "export.need_doc": {
        "it": "Apri prima un PDF", "en": "Open a PDF first",
        "fr": "Ouvrez d'abord un PDF", "de": "Zuerst ein PDF öffnen",
        "es": "Abre primero un PDF",
    },
    "export.not_ready": {
        "it": "Pagina non ancora tradotta.", "en": "Page not translated yet.",
        "fr": "Page pas encore traduite.", "de": "Seite noch nicht übersetzt.",
        "es": "Página aún no traducida.",
    },
    "export.none_ready": {
        "it": "Nessuna pagina dell'intervallo è pronta.",
        "en": "No page in the range is ready.",
        "fr": "Aucune page de la plage n'est prête.",
        "de": "Keine Seite im Bereich ist bereit.",
        "es": "Ninguna página del rango está lista.",
    },
    # ── notifica a fine batch ───────────────────────────────────────────
    "notify.batch.done": {
        "it": "Esportazione terminata: {count} pagine.",
        "en": "Export finished: {count} pages.",
        "fr": "Export terminé : {count} pages.",
        "de": "Export beendet: {count} Seiten.",
        "es": "Exportación finalizada: {count} páginas.",
    },
    "notify.batch.partial": {
        "it": "Esportazione terminata: {count} OK, {failed} non riuscite.",
        "en": "Export finished: {count} OK, {failed} failed.",
        "fr": "Export terminé : {count} OK, {failed} échecs.",
        "de": "Export beendet: {count} OK, {failed} fehlgeschlagen.",
        "es": "Exportación finalizada: {count} OK, {failed} fallidas.",
    },
    "notify.batch.cancelled": {
        "it": "Esportazione annullata dall'utente.",
        "en": "Export cancelled by the user.",
        "fr": "Export annulé par l'utilisateur.",
        "de": "Export vom Benutzer abgebrochen.",
        "es": "Exportación cancelada por el usuario.",
    },
    # ── impostazioni: aspetto / avanzate / notifiche ────────────────────
    "settings.group.appearance": {
        "it": "Aspetto", "en": "Appearance", "fr": "Apparence",
        "de": "Erscheinungsbild", "es": "Apariencia",
    },
    "settings.appearance.theme": {
        "it": "Tema", "en": "Theme", "fr": "Thème",
        "de": "Thema", "es": "Tema",
    },
    "settings.theme.dark": {
        "it": "Scuro", "en": "Dark", "fr": "Sombre",
        "de": "Dunkel", "es": "Oscuro",
    },
    "settings.theme.light": {
        "it": "Chiaro", "en": "Light", "fr": "Clair",
        "de": "Hell", "es": "Claro",
    },
    "settings.theme.system": {
        "it": "Come il sistema", "en": "Follow the system",
        "fr": "Comme le système", "de": "Wie das System",
        "es": "Como el sistema",
    },
    "settings.group.advanced": {
        "it": "Avanzate", "en": "Advanced", "fr": "Avancé",
        "de": "Erweitert", "es": "Avanzado",
    },
    "settings.advanced.cache.clear": {
        "it": "Svuota cache documento", "en": "Clear document cache",
        "fr": "Vider le cache du document", "de": "Dokument-Cache leeren",
        "es": "Vaciar caché del documento",
    },
    "settings.advanced.cache.info": {
        "it": "Info cache", "en": "Cache info",
        "fr": "Infos du cache", "de": "Cache-Info",
        "es": "Información de caché",
    },
    "settings.group.notifications": {
        "it": "Notifiche", "en": "Notifications", "fr": "Notifications",
        "de": "Benachrichtigungen", "es": "Notificaciones",
    },
    "settings.notify.batch": {
        "it": "Avvisa a fine esportazione",
        "en": "Notify when an export finishes",
        "fr": "Avertir à la fin d'un export",
        "de": "Bei Exportende benachrichtigen",
        "es": "Avisar al terminar una exportación",
    },
    "settings.view.render_quality": {
        "it": "Qualità di rendering", "en": "Render quality",
        "fr": "Qualité de rendu", "de": "Render-Qualität",
        "es": "Calidad de renderizado",
    },
}


def T(key: str, **fmt) -> str:
    """Return the string for ``key`` in the active language.

    ``fmt`` is applied with ``str.format`` for parameterized messages (e.g.
    ``T("status.page", page=3, total=12, name="x.pdf", ms="210")``). Falls
    back to Italian, then to the key itself, on any miss; formatting errors
    degrade to the raw translated text instead of raising.
    """
    table = _STRINGS.get(key)
    if not table:
        return key
    text = table.get(_current) or table.get("it") or key
    if not fmt:
        return text
    try:
        return text.format(**fmt)
    except (KeyError, IndexError, ValueError):
        return text


# ═══════════════════════════════════════════════════════════════════════════════
#  persistence — atomic write, tolerant read, forward-compatible schema
# ═══════════════════════════════════════════════════════════════════════════════


def _read_config(path) -> dict | None:
    """Parse the config file; ``None`` when missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _to_bool(value, default: bool) -> bool:
    """Coerce a config value to bool (accepts bools, 0/1, 'true'/'false')."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("1", "true", "yes", "on"):
            return True
        if low in ("0", "false", "no", "off"):
            return False
    return default


def _validate_config(raw: dict, defaults: dict) -> dict:
    """Merge ``raw`` over ``defaults`` with per-field validation.

    Unknown fields are ignored (forward compatibility); wrong types and
    out-of-range values degrade to the defaults/clamps instead of crashing.
    """
    out = dict(defaults)
    if raw.get("lang") in LANGUAGES:
        out["lang"] = raw["lang"]
    if raw.get("src_lang") in TRANSLATION_LANGUAGES:
        out["src_lang"] = raw["src_lang"]
    dst = raw.get("dst_lang")
    if dst in TRANSLATION_LANGUAGES and dst != "auto":
        out["dst_lang"] = dst
    try:
        out["zoom"] = min(4.0, max(0.5, float(raw.get("zoom", out["zoom"]))))
    except (TypeError, ValueError):
        pass
    try:
        out["font_size"] = min(16, max(10, int(raw.get("font_size", out["font_size"]))))
    except (TypeError, ValueError):
        pass
    for key in ("render_md", "show_header", "remember_tab", "resume_last_page", "save_edits"):
        out[key] = _to_bool(raw.get(key, out[key]), out[key])
    out["notify_batch"] = _to_bool(raw.get("notify_batch", out["notify_batch"]), out["notify_batch"])
    if raw.get("theme") in ("dark", "light", "system"):
        out["theme"] = raw["theme"]
    if raw.get("last_tab") in ("original", "translated", "images"):
        out["last_tab"] = raw["last_tab"]
    pages = raw.get("last_pages")
    if isinstance(pages, dict):
        cleaned: dict[str, int] = {}
        for name, page in pages.items():
            try:
                cleaned[str(name)] = int(page)
            except (TypeError, ValueError):
                pass
        out["last_pages"] = dict(list(cleaned.items())[-20:])  # cap LRU 20
    return out


def load_config(path, defaults: dict | None = None) -> dict:
    """Load and validate the config file, merging missing fields with defaults."""
    defaults = defaults or DEFAULTS
    data = _read_config(path)
    if data is None:
        return dict(defaults)
    return _validate_config(data, defaults)


def save_config(path=None, config: dict | None = None) -> None:
    """Write the config atomically (temp file + os.replace).

    Uses the module-level path when ``path`` is None (set by init_config).
    A crash mid-write leaves the previous file intact.
    """
    if path is not None:
        target = Path(path)
    elif _CONFIG_PATH:
        target = Path(_CONFIG_PATH)
    else:
        return
    payload = config if config is not None else _CONFIG
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, target)


def init_config(path, defaults: dict | None = None) -> dict:
    """Load config into module state and set the global save path.

    On first run (missing/corrupted file) the defaults are written
    immediately, so after the first launch the file always exists.
    """
    global _CONFIG, _CONFIG_PATH, _current
    _CONFIG_PATH = str(Path(path))
    cfg = load_config(path, defaults)
    if _read_config(path) is None:
        save_config(path, cfg)
    _CONFIG = cfg
    _current = cfg.get("lang", "it")
    return dict(_CONFIG)


def get_config() -> dict:
    """Snapshot of the current in-memory config."""
    return dict(_CONFIG)


def get_setting(key: str, default=None):
    """Return a config value (falls back to ``default``)."""
    return _CONFIG.get(key, default)


def set_setting(key: str, value) -> None:
    """Update a config value in memory (validated for scalar ranges)."""
    if key == "lang":
        # La lingua è speciale: sincronizza anche la lingua attiva di T().
        set_language(value)
        return
    if key == "zoom":
        try:
            _CONFIG[key] = min(4.0, max(0.5, float(value)))
            return
        except (TypeError, ValueError):
            pass
    elif key == "font_size":
        try:
            _CONFIG[key] = min(16, max(10, int(value)))
            return
        except (TypeError, ValueError):
            pass
    _CONFIG[key] = value


# ── compat wrappers (pre-config-v2 API) ──────────────────────────────────────


def load_language(path, default: str = "it") -> str:
    """Read the stored UI language code; ``default`` on any problem."""
    cfg = load_config(path, {**DEFAULTS, "lang": default})
    return cfg.get("lang", default)


def save_language(path, code: str) -> None:
    """Write the UI language, merging with the existing config file.

    Merges (never clobbers) so other fields (src/dst/zoom…) survive.
    """
    existing = _read_config(path) or {}
    existing["lang"] = code
    save_config(path, existing)


def ensure_config(path, default: str = "it") -> str:
    """Return the stored UI language, writing the defaults on first run."""
    path = Path(path)
    cfg = load_config(path, {**DEFAULTS, "lang": default})
    if _read_config(path) is None:
        save_config(path, cfg)
    return cfg.get("lang", default)
