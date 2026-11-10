#!/usr/bin/env bash
set -eo pipefail

# Configuration (defaults can be overridden via environment variables)
REMOTE_HOST="${REMOTE_HOST:-root@nos.vamos.acas.ar}"
REMOTE_PARENT="${REMOTE_PARENT:-/home/alfredo}"
REMOTE_FOLDER="${REMOTE_FOLDER:-invite}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILENAME="boda_server_backup_${TIMESTAMP}.tar.gz"
BACKUP_FILE="${BACKUP_DIR}/${BACKUP_FILENAME}"

echo "========================================="
echo "📦 Starting Full Server Backup"
echo "========================================="
echo "🌐 Remote Host:   ${REMOTE_HOST}"
echo "📁 Remote Target: ${REMOTE_PARENT}/${REMOTE_FOLDER}"
echo "💾 Local Output:  ${BACKUP_FILE}"
echo "========================================="

# Ensure local backup directory exists
mkdir -p "$BACKUP_DIR"

echo "⏳ Compressing and transferring all code and data from server..."
# Stream tar archive directly through SSH stdout into local file
# --warning=no-file-changed avoids exit code 1 if active log files change during read
ssh "$REMOTE_HOST" "tar --warning=no-file-changed -cz -C '${REMOTE_PARENT}' '${REMOTE_FOLDER}'" > "$BACKUP_FILE"

# Verify archive integrity
echo "🔍 Verifying archive integrity..."
if tar -tzf "$BACKUP_FILE" > /dev/null 2>&1; then
    echo "✓ Archive integrity verified successfully."
else
    echo "❌ Error: Corrupted backup archive received."
    rm -f "$BACKUP_FILE"
    exit 1
fi

# Update symlink to latest backup
(cd "$BACKUP_DIR" && ln -sf "$BACKUP_FILENAME" "latest.tar.gz")

FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)

echo "========================================="
echo "✅ Backup completed successfully!"
echo "📦 File: ${BACKUP_FILE} (${FILE_SIZE})"
echo "🔗 Link: ${BACKUP_DIR}/latest.tar.gz"
echo "========================================="

