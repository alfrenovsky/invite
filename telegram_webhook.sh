#!/usr/bin/env bash
set -e

# Usage:
#   ./telegram_webhook.sh           # Registra el webhook usando variables de project.env
#   ./telegram_webhook.sh --info    # Muestra el estado del webhook actual
#   ./telegram_webhook.sh --delete  # Elimina el webhook (para usar modo polling)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/project.env"

if [[ -f "$ENV_FILE" ]]; then
  # Extract variables from project.env without exporting comments
  TELEGRAM_BOT_TOKEN="$(grep -E '^TELEGRAM_BOT_TOKEN=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '\r\n"' | tr -d "'")"
  TELEGRAM_BOT_NAME="$(grep -E '^TELEGRAM_BOT_NAME=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '\r\n"' | tr -d "'")"
  TELEGRAM_AUTH_SECRET="$(grep -E '^TELEGRAM_AUTH_SECRET=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '\r\n"' | tr -d "'")"
  BASE_URL="$(grep -E '^BASE_URL=' "$ENV_FILE" | cut -d '=' -f2- | tr -d '\r\n"' | tr -d "'")"
fi

# Fallbacks from environment if not in file
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-$TELEGRAM_BOT_TOKEN}"
TELEGRAM_BOT_NAME="${TELEGRAM_BOT_NAME:-celiayalfredo_bot}"
TELEGRAM_AUTH_SECRET="${TELEGRAM_AUTH_SECRET:-boda_cya_admin_telegram_2027}"
BASE_URL="${BASE_URL:-https://nos.vamos.acas.ar}"

if [[ -z "$TELEGRAM_BOT_TOKEN" ]]; then
  echo "❌ Error: TELEGRAM_BOT_TOKEN no está definido en project.env ni en el entorno."
  exit 1
fi

ACTION="${1:-set}"
AUTH_LINK="https://t.me/${TELEGRAM_BOT_NAME}?start=${TELEGRAM_AUTH_SECRET}"

case "$ACTION" in
  --info|--status|info|status)
    echo "🔍 Consultando información del webhook en Telegram..."
    curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo" | python3 -m json.tool || curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
    echo ""
    echo "🔗 Enlace de autenticación de inicio (/start):"
    echo "   ${AUTH_LINK}"
    echo ""
    ;;

  --delete|--remove|delete|remove)
    echo "🗑️ Eliminando webhook de Telegram..."
    curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=true" | python3 -m json.tool || curl -s "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook"
    echo ""
    echo "ℹ️ El webhook ha sido eliminado. El bot puede operar ahora en modo polling."
    echo ""
    ;;

  --set|set|*)
    # Clean trailing slash from base url
    BASE_URL="${BASE_URL%/}"
    WEBHOOK_URL="${BASE_URL}/telegram/webhook"

    echo "🚀 Registrando webhook en Telegram..."
    echo "   URL: ${WEBHOOK_URL}"
    echo ""

    if [[ -n "$TELEGRAM_AUTH_SECRET" ]]; then
      curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
        -d "url=${WEBHOOK_URL}" \
        -d "secret_token=${TELEGRAM_AUTH_SECRET}" \
        -d "drop_pending_updates=true" | python3 -m json.tool || \
      curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
        -d "url=${WEBHOOK_URL}" \
        -d "secret_token=${TELEGRAM_AUTH_SECRET}" \
        -d "drop_pending_updates=true"
    else
      curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
        -d "url=${WEBHOOK_URL}" \
        -d "drop_pending_updates=true" | python3 -m json.tool || \
      curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
        -d "url=${WEBHOOK_URL}" \
        -d "drop_pending_updates=true"
    fi

    echo ""
    echo "=========================================================="
    echo "✅ Webhook configurado con éxito!"
    echo "🔗 Enlace de autenticación de inicio (/start):"
    echo "   ${AUTH_LINK}"
    echo "=========================================================="
    echo ""
    ;;
esac
