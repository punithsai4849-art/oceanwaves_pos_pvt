#!/bin/bash

# ==============================================================================
# OCEANWAVES DATABASE BACKUP SCRIPT
# ==============================================================================
# This script dumps MySQL, compresses it, and syncs to Google Drive via rclone.
# ==============================================================================

# 1. Load Environment Variables from .env
# Adjust path to your .env file
ENV_FILE="/home/ubuntu/ocnwvs/.env"
if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
else
    echo ".env file not found at $ENV_FILE"
    exit 1
fi

# 2. Settings
BACKUP_DIR="/home/ubuntu/ocnwvs/backups"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
DB_BACKUP_NAME="oceanwaves_db_$TIMESTAMP.sql"
ZIP_NAME="oceanwaves_backup_$TIMESTAMP.tar.gz"
RCLONE_REMOTE="gdrive"  # This must match your rclone config name
RCLONE_DEST="Oceanwaves_Backups" # Folder on your Google Drive

# 3. Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

echo "--- Starting Backup at $(date) ---"

# 4. Perform MySQL Dump
echo "Dumping database: $DB_NAME..."
mysqldump -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" > "$BACKUP_DIR/$DB_BACKUP_NAME"

if [ $? -eq 0 ]; then
    echo "Dump successful."
else
    echo "Error: Database dump failed!"
    exit 1
fi

# 5. Compress the backup
echo "Compressing backup..."
cd "$BACKUP_DIR"
tar -czf "$ZIP_NAME" "$DB_BACKUP_NAME"
rm "$DB_BACKUP_NAME"

# 6. Upload to Google Drive via rclone
echo "Syncing to Google Drive..."
rclone copy "$BACKUP_DIR/$ZIP_NAME" "$RCLONE_REMOTE:$RCLONE_DEST"

if [ $? -eq 0 ]; then
    echo "Successfully uploaded to Google Drive."
else
    echo "Error: Upload to Google Drive failed! (Is rclone configured?)"
fi

# 7. Cleanup local backups older than 30 days
echo "Cleaning up local files older than 30 days..."
find "$BACKUP_DIR" -type f -name "*.tar.gz" -mtime +30 -delete

echo "--- Backup process completed at $(date) ---"
