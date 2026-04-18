#!/bin/bash

# ==============================================================================
# OCEANWAVES ENCRYPTED BACKUP SCRIPT
# ==============================================================================
# 1. Dumps MySQL Database
# 2. Encrypts the backup with password (Oceanwaves@202619)
# 3. Sends the encrypted file to Gmail
# ==============================================================================

# Configuration
PROJECT_ROOT="/home/ubuntu/ocnwvs"
BACKUP_DIR="$PROJECT_ROOT/backups"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
DB_BACKUP_NAME="oceanwaves_db_$TIMESTAMP.sql"
ZIP_NAME="oceanwaves_backup_$TIMESTAMP.zip"
PASSWORD="Oceanwaves@202619"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# 1. Load Environment Variables
if [ -f "$PROJECT_ROOT/.env" ]; then
    export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs)
else
    echo ".env file not found!"
    exit 1
fi

echo "--- Backup Started: $(date) ---"

# 2. Dump Database
echo "Creating database dump..."
mysqldump -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" > "$BACKUP_DIR/$DB_BACKUP_NAME"

if [ $? -ne 0 ]; then
    echo "ERROR: mysqldump failed!"
    exit 1
fi

# 3. Encrypt and Zip
echo "Encrypting backup with password..."
cd "$BACKUP_DIR"
# Use zip with password (-P)
zip -P "$PASSWORD" "$ZIP_NAME" "$DB_BACKUP_NAME"
rm "$DB_BACKUP_NAME"

# 4. Use Python to send the email
echo "Sending email to $EMAIL_HOST_USER..."
python3 "$PROJECT_ROOT/scripts/send_backup.py" "$BACKUP_DIR/$ZIP_NAME"

if [ $? -eq 0 ]; then
    echo "SUCCESS: Backup sent to email."
else
    echo "ERROR: Failed to send email."
fi

# 5. Cleanup local backups older than 28 days (keep 4 weeks)
find "$BACKUP_DIR" -type f -name "*.zip" -mtime +28 -delete

echo "--- Backup Completed: $(date) ---"
