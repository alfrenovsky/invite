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
SHEET_FILENAME="boda_sheet_backup_${TIMESTAMP}.xlsx"
SHEET_FILE="${BACKUP_DIR}/${SHEET_FILENAME}"
CSV_FILENAME="boda_confirmaciones_${TIMESTAMP}.csv"
CSV_FILE="${BACKUP_DIR}/${CSV_FILENAME}"

echo "========================================="
echo "📦 Starting Full Server & Sheet Backup"
echo "========================================="
echo "🌐 Remote Host:   ${REMOTE_HOST}"
echo "📁 Remote Target: ${REMOTE_PARENT}/${REMOTE_FOLDER}"
echo "💾 Local Output:  ${BACKUP_FILE}"
echo "========================================="

# Ensure local backup directory exists
mkdir -p "$BACKUP_DIR"

# 1. Export fresh copy of Google Sheets on the remote server
echo "📊 Exporting live Google Sheet (Workbook .xlsx & Confirmaciones .csv)..."
ssh "$REMOTE_HOST" "cd '${REMOTE_PARENT}/${REMOTE_FOLDER}' && docker compose exec -T api python3 -c '
from sheets import GoogleSheetsTable
import gspread, csv, os

try:
    t = GoogleSheetsTable()
    ws = t._get_worksheet()
    sh = ws.spreadsheet

    # 1. Export entire workbook with all worksheets to Excel .xlsx
    xlsx_content = sh.export(format=gspread.utils.ExportFormat.EXCEL)
    with open(\"/data/google_sheet_backup.xlsx\", \"wb\") as f:
        f.write(xlsx_content)

    # 2. Export main Confirmaciones worksheet to CSV
    all_values = ws.get_all_values()
    with open(\"/data/confirmaciones.csv\", \"w\", newline=\"\", encoding=\"utf-8\") as f:
        writer = csv.writer(f)
        writer.writerows(all_values)

    # 3. Refresh sheet_cache.json
    t.get_all(force_remote=True)
    print(f\"✓ Sheet exported successfully ({len(xlsx_content)} bytes xlsx, {len(all_values)} rows csv)\")
except Exception as e:
    print(f\"⚠️ Warning: Could not export live sheet: {e}\")
'"

# 2. Compress and transfer all code and data
echo "⏳ Compressing and transferring all code and data from server..."
# Stream tar archive directly through SSH stdout into local file
# --warning=no-file-changed avoids exit code 1 if active log files change during read
ssh "$REMOTE_HOST" "tar --warning=no-file-changed -cz -C '${REMOTE_PARENT}' '${REMOTE_FOLDER}'" > "$BACKUP_FILE"

# 3. Verify archive integrity
echo "🔍 Verifying archive integrity..."
if tar -tzf "$BACKUP_FILE" > /dev/null 2>&1; then
    echo "✓ Archive integrity verified successfully."
else
    echo "❌ Error: Corrupted backup archive received."
    rm -f "$BACKUP_FILE"
    exit 1
fi

# 4. Extract standalone copies of the sheet to local backups folder
tar -xzf "$BACKUP_FILE" -O "${REMOTE_FOLDER}/data/google_sheet_backup.xlsx" > "$SHEET_FILE" 2>/dev/null || true
if [ -s "$SHEET_FILE" ]; then
    (cd "$BACKUP_DIR" && ln -sf "$SHEET_FILENAME" "latest_sheet.xlsx")
    echo "✓ Standalone Excel copy extracted: ${SHEET_FILE}"
fi

tar -xzf "$BACKUP_FILE" -O "${REMOTE_FOLDER}/data/confirmaciones.csv" > "$CSV_FILE" 2>/dev/null || true
if [ -s "$CSV_FILE" ]; then
    (cd "$BACKUP_DIR" && ln -sf "$CSV_FILENAME" "latest_confirmaciones.csv")
    echo "✓ Standalone CSV copy extracted: ${CSV_FILE}"
fi

# 5. Update symlink to latest server backup
(cd "$BACKUP_DIR" && ln -sf "$BACKUP_FILENAME" "latest.tar.gz")

FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
SHEET_SIZE=$(du -h "$SHEET_FILE" 2>/dev/null | cut -f1 || echo "")

echo "========================================="
echo "✅ Backup completed successfully!"
echo "📦 Full Backup: ${BACKUP_FILE} (${FILE_SIZE})"
if [ -n "$SHEET_SIZE" ]; then
    echo "📊 Excel Sheet: ${SHEET_FILE} (${SHEET_SIZE})"
fi
echo "🔗 Links:       ${BACKUP_DIR}/latest.tar.gz"
echo "                ${BACKUP_DIR}/latest_sheet.xlsx"
echo "========================================="
