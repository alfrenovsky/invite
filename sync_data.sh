#!/usr/bin/env bash
set -e

# Configuration (defaults can be overridden via environment variables)
REMOTE_HOST="${REMOTE_HOST:-root@nos.vamos.acas.ar}"
REMOTE_DIR="${REMOTE_DIR:-/home/alfredo/invite}"
LOCAL_DATA="./data/"
REMOTE_DATA="${REMOTE_HOST}:${REMOTE_DIR}/data/"

ACTION="push"
DRY_RUN=""

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        pull)
            ACTION="pull"
            ;;
        push)
            ACTION="push"
            ;;
        --dry-run|-n)
            DRY_RUN="--dry-run"
            ;;
        -h|--help)
            echo "Uso: $0 [push|pull] [--dry-run]"
            echo ""
            echo "Comandos:"
            echo "  push        Sube el contenido local (data/) a producción (por defecto)"
            echo "  pull        Descarga el contenido de producción a local"
            echo "  --dry-run   Muestra los cambios que se transferirían sin modificar archivos"
            exit 0
            ;;
    esac
done

echo "========================================="
if [ -n "$DRY_RUN" ]; then
    echo "🔍 SIMULACIÓN (DRY-RUN): Ningún archivo será modificado"
fi

if [ "$ACTION" = "push" ]; then
    echo "📤 Sincronizando contenido: LOCAL ($LOCAL_DATA) ➔ PRODUCCIÓN ($REMOTE_DATA)"
    SRC="$LOCAL_DATA"
    DST="$REMOTE_DATA"
else
    echo "📥 Sincronizando contenido: PRODUCCIÓN ($REMOTE_DATA) ➔ LOCAL ($LOCAL_DATA)"
    SRC="$REMOTE_DATA"
    DST="$LOCAL_DATA"
fi
echo "========================================="

# Ensure local data directory exists
mkdir -p "$LOCAL_DATA"

# Run rsync excluding runtime logs
rsync -avz --progress --exclude="*.log" $DRY_RUN "$SRC" "$DST"

echo "========================================="
echo "✅ Sincronización completada con éxito."
echo "========================================="
