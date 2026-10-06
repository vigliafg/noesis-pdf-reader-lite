#!/bin/bash
# Noesis PDF Reader Lite — launcher del WORKTREE IR (branch layout-order).
# Lancia il codice di QUESTO worktree (fix chooser, ir_layout aggiornato, GNN),
# NON il repo base /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite.
# Percorsi assoluti: lanciabile da qualsiasi directory, senza attivare il venv.

cd /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite-ir || exit 1
exec /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite-ir/.venv/bin/python \
     /home/vigliafg/Documenti/GitHub/noesis-pdf-reader-lite-ir/main.py "$@"
