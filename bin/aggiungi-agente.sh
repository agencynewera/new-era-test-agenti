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

TMP="$(mktemp -d)"; trap 'rm -rf -- "$TMP"' EXIT
rsync -a --no-links \
  --exclude '.git' --include '.env.example' --exclude '.env' --exclude '.env.*' --exclude '.envrc' \
  --exclude '*.pem' --exclude '*.key' --exclude '*.p12' --exclude '*.pfx' --exclude 'id_rsa*' \
  --exclude '.ssh/' --exclude '.aws/' --exclude '.netrc' --exclude 'credentials*' --exclude 'credenziali*' \
  --exclude 'service-account*' --exclude '*token*.json' --exclude 'token/' --exclude '.secrets/' \
  --exclude '*.db' --exclude '*.sqlite*' --exclude '*.log' --exclude 'logs/' \
  --exclude '*.csv' --exclude '*.xlsx' --exclude '*.xls' \
  --exclude '.venv/' --exclude 'venv/' --exclude '__pycache__/' --exclude 'node_modules/' --exclude '.DS_Store' \
  --exclude 'scratchpad/' --exclude 'output/' --exclude 'dati/' \
  "$ORIG/" "$TMP/"

# Controllo segreti PRIMA di mettere la copia nel repo: se trova qualcosa, non copia nulla.
if ! gitleaks detect --no-git --source "$TMP" --no-banner --redact; then
  echo "ATTENZIONE: gitleaks ha trovato possibili segreti in $ORIG. Nessun file copiato nel repo."; exit 2
fi
echo "gitleaks: nessun segreto trovato."

sed -e "s|{{AGENTE}}|$NOME|" -e "s|{{DATA}}|$(date +%d/%m/%Y)|" -e "s|{{ORIGINE}}|$ORIG|" \
  "$REPO/modelli/SCHEDA-TEST.md" > "$TMP/SCHEDA-TEST.md"
mkdir -p "$REPO/agenti" && mv "$TMP" "$DEST" && trap - EXIT
echo "Copiato in $DEST"
echo "Ora: aggiorna REGISTRO.md, poi commit e push."
