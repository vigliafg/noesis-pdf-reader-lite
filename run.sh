#!/bin/bash
# Noesis PDF Reader Lite — script di lancio con percorsi assoluti
# Non richiede l'attivazione manuale dell'ambiente virtuale

cd /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite || exit 1
exec /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite/.venv/bin/python /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite/main.py "$@"
