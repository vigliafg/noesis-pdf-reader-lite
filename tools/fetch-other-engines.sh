#!/usr/bin/env bash
# Noesis PDF Reader Lite — KB locale dei motori di estrazione esterni.
#
# I cloni vivono in `other-engines/` (NON versionata, vedi .gitignore): li si ha
# "sempre sottomano" per leggere il codice, ma non gonfiano la history di git.
# Ogni repo è pinnato al commit analizzato nei nostri STUDIO-*:
#   - docs/STUDIO-PAPERO-PDF-EXTRACTOR-2026-10-10.md
#   - docs/STUDIO-MOTORI-GEOMETRICI-ESTERNI-2026-10-10.md
#
# PORTABILITÀ su più macchine: la KB non si "porta", si RIPRODUCE. Su una
# macchina nuova basta: `git clone <nostro-repo> && tools/fetch-other-engines.sh --lean`.
# Per l'uso OFFLINE (nessuna rete) si crea uno snapshot e lo si ripristina:
#   tools/fetch-other-engines.sh --archive [file]   # crea uno snapshot .tar.gz
#   tools/fetch-other-engines.sh --restore [file]   # lo ripristina
#
# Uso:
#   tools/fetch-other-engines.sh            # scarica/verifica i commit pinnati
#   tools/fetch-other-engines.sh --latest   # aggiorna ogni repo a origin di default
#   tools/fetch-other-engines.sh --refresh  # ri-scarica da zero (commit pinnati)
#   tools/fetch-other-engines.sh --lean     # KB leggera: PdfPig blobless+sparse
#                                           # (solo codice), fixture rimosse.
#   tools/fetch-other-engines.sh --archive [file]   # snapshot (default: other-engines-kb.tar.gz)
#   tools/fetch-other-engines.sh --restore [file]   # ripristina uno snapshot
#
# Self-locating: lanciabile da qualsiasi directory.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/other-engines"
ARCHIVE_DEFAULT="$ROOT/other-engines-kb.tar.gz"

MODE="pin"
SLIM=0
ARCHIVE=""
RESTORE=""
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  a="${args[$i]}"
  case "$a" in
    --latest)  MODE="latest" ;;
    --refresh) MODE="refresh" ;;
    --lean)    SLIM=1 ;;
    --archive)
      ARCHIVE="$ARCHIVE_DEFAULT"
      if [ $((i + 1)) -lt ${#args[@]} ] && [ "${args[$((i + 1))]:0:1}" != "-" ]; then
        i=$((i + 1)); ARCHIVE="${args[$i]}"
      fi ;;
    --restore)
      RESTORE="$ARCHIVE_DEFAULT"
      if [ $((i + 1)) -lt ${#args[@]} ] && [ "${args[$((i + 1))]:0:1}" != "-" ]; then
        i=$((i + 1)); RESTORE="${args[$i]}"
      fi ;;
    --help|-h) sed -n '2,25p' "$0"; exit 0 ;;
    *) echo "opzione sconosciuta: $a (usa --help)" >&2; exit 2 ;;
  esac
done

# nome|url|sha_pinnato  (commit analizzati — non cambiare senza aggiornare i STUDIO-*)
ENGINES=(
  "papero|https://github.com/beatrizalmeidaf/papero-pdf-text-extractor.git|1b076dad96be246f9bca58cf69c59c77651ea694"
  "opendataloader|https://github.com/opendataloader-project/opendataloader-pdf.git|3dfb3b7ca754cd03800470bf2f6ae96d4a5c1e15"
  "pdfpig|https://github.com/UglyToad/PdfPig.git|91ddd23f5070899dbbd5b5f34fd99b388c8da1b4"
  "pdfplumber|https://github.com/jsvine/pdfplumber.git|4c64b92d5caccd71c645e98e0fabb0c4dba7ff45"
  "pdfminer|https://github.com/pdfminer/pdfminer.six.git|a18de2a9c479b4c847538500017b449ddaec177e"
)

# Repo con fixture enormi (PDF/immagini di test): con --lean si materializzano
# SOLO questi percorsi (clone blobless + sparse). Tutti i progetti di codice di
# PdfPig tranne `src/UglyToad.PdfPig.Tests` (i 342 MB di PDF).
SPARSE=(
  "pdfpig|src/UglyToad.PdfPig src/UglyToad.PdfPig.Core src/UglyToad.PdfPig.Fonts src/UglyToad.PdfPig.Tokens src/UglyToad.PdfPig.Tokenization src/UglyToad.PdfPig.DocumentLayoutAnalysis"
)

sparse_paths() {
  local entry
  for entry in "${SPARSE[@]}"; do
    if [ "${entry%%|*}" = "$1" ]; then echo "${entry#*|}"; return; fi
  done
  echo ""
}

clone_full() { # name url sha dir
  local name="$1" url="$2" sha="$3" dir="$4" ref="$3"
  [ "$MODE" = "latest" ] && ref="HEAD"
  echo "→ $name: scarico (shallow) ${sha:0:10}"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$url" 2>/dev/null || git -C "$dir" remote set-url origin "$url"
  if git -C "$dir" fetch -q --depth 1 origin "$ref"; then
    git -C "$dir" checkout -q --detach FETCH_HEAD
  else
    rm -rf "$dir"
    git clone -q "$url" "$dir"
    [ "$MODE" = "pin" ] && git -C "$dir" checkout -q "$sha"
  fi
}

clone_sparse() { # name url sha dir paths
  local name="$1" url="$2" sha="$3" dir="$4" paths="$5"
  echo "→ $name: scarico (blobless+sparse) ${sha:0:10}  [solo: $paths]"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" remote add origin "$url" 2>/dev/null || git -C "$dir" remote set-url origin "$url"
  if git -C "$dir" fetch -q --depth 1 --filter=blob:none origin "$sha" \
     && git -C "$dir" sparse-checkout init --cone \
     && git -C "$dir" sparse-checkout set $paths \
     && git -C "$dir" checkout -q --detach FETCH_HEAD; then
    return
  fi
  echo "  (sparse/partial non disponibile: fallback a clone normale)"
  rm -rf "$dir"
  clone_full "$name" "$url" "$sha" "$dir"
}

# Rimuove le fixture binarie sotto cartelle di test/benchmark (servono alle suite
# dei progetti, non a leggere il codice). Reversibile con `--refresh`.
slim_fixtures() { # dir
  local dir="$1" before after
  before=$(du -sm "$dir" 2>/dev/null | cut -f1)
  find "$dir" -type f \
    \( -iname '*.pdf' -o -iname '*.jp2' -o -iname '*.jpg' -o -iname '*.jpeg' \
       -o -iname '*.png' -o -iname '*.gif' -o -iname '*.bmp' -o -iname '*.tif' \) \
    \( -path '*[Tt]est*' -o -path '*[Bb]enchmark*' -o -path '*[Ss]pecific*' -o -path '*/samples/*' \) \
    ! -path '*/.git/*' -delete 2>/dev/null || true
  after=$(du -sm "$dir" 2>/dev/null | cut -f1)
  echo "  lean: ${before} MB -> ${after} MB"
}

# ── snapshot offline ─────────────────────────────────────────────────────────
do_archive() { # file
  local file="$1" present=() e name dir
  for e in "${ENGINES[@]}"; do
    name="${e%%|*}"; dir="$DEST/$name"
    [ -n "$(ls -A "$dir" 2>/dev/null)" ] && present+=("$name")
  done
  if [ "${#present[@]}" = 0 ]; then
    echo "niente da archiviare: popola prima la KB (tools/fetch-other-engines.sh --lean)" >&2
    exit 1
  fi
  echo "→ archivio ${present[*]} in $file (senza .git)"
  tar -C "$DEST" -czf "$file" --exclude='.git' "${present[@]}"
  echo "  creato: $file ($(du -h "$file" | cut -f1))"
  echo "  su un'altra macchina: tools/fetch-other-engines.sh --restore $(basename "$file")"
}

do_restore() { # file
  local file="$1"
  if [ ! -f "$file" ]; then
    echo "file non trovato: $file" >&2; exit 1
  fi
  echo "→ ripristino $file in $DEST"
  mkdir -p "$DEST"
  tar -C "$DEST" -xzf "$file"
  echo "  fatto. Nota: i cloni ripristinati non hanno .git (solo per lettura)."
  echo "  Per aggiornarli: rm -rf other-engines/<nome> && tools/fetch-other-engines.sh --lean"
}

# ── modalità snapshot: esegui e termina ──────────────────────────────────────
if [ -n "$RESTORE" ]; then do_restore "$RESTORE"; exit 0; fi
if [ -n "$ARCHIVE" ]; then do_archive "$ARCHIVE"; exit 0; fi

# ── fetch dei motori ─────────────────────────────────────────────────────────
mkdir -p "$DEST"
for e in "${ENGINES[@]}"; do
  IFS='|' read -r name url sha <<< "$e"
  dir="$DEST/$name"
  paths="$(sparse_paths "$name")"

  if [ "$MODE" = "refresh" ] && [ -d "$dir" ]; then
    rm -rf "$dir"
  fi
  # snapshot ripristinato (dir presente senza .git): si lascia stare, è per lettura.
  if [ -d "$dir" ] && [ ! -d "$dir/.git" ] && [ -n "$(ls -A "$dir" 2>/dev/null)" ]; then
    echo "✓ $name presente (snapshot senza .git) — salto"
    continue
  fi
  # --lean su un repo con sparse richiesto: se esiste ma non è sparso, lo rifaccio.
  if [ "$SLIM" = 1 ] && [ -n "$paths" ] && [ -d "$dir/.git" ] && [ ! -f "$dir/.git/info/sparse-checkout" ]; then
    rm -rf "$dir"
  fi

  if [ -d "$dir/.git" ]; then
    if [ "$MODE" = "latest" ]; then
      echo "→ $name: aggiorno a origin/HEAD"
      git -C "$dir" fetch -q origin
      git -C "$dir" checkout -q --detach "$(git -C "$dir" rev-parse origin/HEAD 2>/dev/null || git -C "$dir" rev-parse FETCH_HEAD)"
    else
      echo "✓ $name già presente @ $(git -C "$dir" rev-parse --short HEAD)"
      continue
    fi
  elif [ "$SLIM" = 1 ] && [ -n "$paths" ]; then
    clone_sparse "$name" "$url" "$sha" "$dir" "$paths"
  else
    clone_full "$name" "$url" "$sha" "$dir"
  fi
  echo "  other-engines/$name @ $(git -C "$dir" rev-parse --short HEAD)"
done

if [ "$SLIM" = 1 ]; then
  for e in "${ENGINES[@]}"; do
    name="${e%%|*}"
    [ -d "$DEST/$name/.git" ] && [ -z "$(sparse_paths "$name")" ] && slim_fixtures "$DEST/$name"
  done
fi

echo
echo "KB esterna pronta in other-engines/  (indice: other-engines/README.md)"
