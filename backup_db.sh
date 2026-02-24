#!/bin/bash
# Backup bot database to backups directory

BACKUP_DIR="./backups"
DB_FILE="bot.db"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/bot_${DATE}.db"

# Create backups directory if not exists
mkdir -p $BACKUP_DIR

# Check if database exists
if [ ! -f "$DB_FILE" ]; then
    echo "Error: $DB_FILE not found!"
    exit 1
fi

# Create backup
cp $DB_FILE $BACKUP_FILE

# Compress backup
gzip $BACKUP_FILE

echo "Backup created: ${BACKUP_FILE}.gz"

# Keep only last 30 backups
ls -t ${BACKUP_DIR}/bot_*.db.gz | tail -n +31 | xargs -r rm

echo "Old backups cleaned. Current backups:"
ls -lh ${BACKUP_DIR}/bot_*.db.gz | head -10
