#!/usr/bin/env bash
# Script para monitorear actividad y llamadas a Google Sheets en tiempo real

set -e

# Nos aseguramos de estar en la raíz del proyecto
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "📊 Monitoreando actividad de Google Sheets en tiempo real (Ctrl+C para salir)..."
echo "-------------------------------------------------------------------------------"

docker compose logs -f -t api 2>&1 \
    | grep --line-buffered -E "(\[READ:|\[WRITE:|\[SYNC:|CACHED|REMOTE|/i/|/invitados|sheets|gspread)" \
    | ccze -A

