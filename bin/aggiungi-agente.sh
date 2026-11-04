#!/bin/bash
# Copia un agente nel repo di test, senza segreti né dati, e prepara la scheda.
# Uso: bin/aggiungi-agente.sh <percorso-originale> <nome>
set -euo pipefail
[ $# -eq 2 ] || { echo "Uso: $0 <percorso-originale> <nome>"; exit 1; }
ORIG="${1%/}"; NOME="$2"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$REPO/agenti/$NOME"
[ -d "$ORIG" ] || { echo "Cartella non trovata: $ORIG"; exit 1; }
[ -e "$DEST" ] && { echo "Esiste già: $DEST"; exit 1; }

rsync -a \
  --exclude '.git' --exclude '.env' --exclude '.env.*' --include '.env.example' \
  --exclude '*.db' --exclude '*.sqlite*' --exclude '*.log' --exclude 'logs/' \
  --exclude 'token/' --exclude '.secrets/' --exclude '.venv/' --exclude 'venv/' \
  --exclude '__pycache__/' --exclude 'node_modules/' --exclude '.DS_Store' \
  --exclude 'scratchpad/' --exclude 'output/' --exclude 'dati/' \
  "$ORIG/" "$DEST/"

sed -e "s|{{AGENTE}}|$NOME|" -e "s|{{DATA}}|$(date +%d/%m/%Y)|" -e "s|{{ORIGINE}}|$ORIG|" \
  "$REPO/modelli/SCHEDA-TEST.md" > "$DEST/SCHEDA-TEST.md"

echo "Copiato in $DEST"
if gitleaks detect --no-git --source "$DEST" --no-banner --redact; then
  echo "gitleaks: nessun segreto trovato."
else
  echo "ATTENZIONE: gitleaks ha trovato possibili segreti. Controlla prima del commit."; exit 2
fi
echo "Ora: aggiorna REGISTRO.md, poi commit e push."
