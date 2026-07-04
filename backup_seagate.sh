#!/bin/bash
# Sequential backup of selected Seagate folders to S3 Glacier IR.
# Run with: bash backup_seagate.sh 2>&1 | tee backup.log

set -euo pipefail
CD="$(cd "$(dirname "$0")" && pwd)"
PYTHON=python3
MAIN="$CD/main.py"
CONFIG="$CD/config.yaml"
LOG="$CD/backup.log"

run_sync() {
    local folder="$1"
    echo ""
    echo "============================================================"
    echo "SYNCING: $folder"
    echo "Started: $(date)"
    echo "============================================================"
    $PYTHON "$MAIN" --config "$CONFIG" sync "$folder" || {
        echo "[ERROR] Sync failed for $folder — continuing with next folder."
    }
    echo "Finished: $(date)"
}

echo "Cloud-Drive backup started: $(date)" | tee -a "$LOG"

run_sync "/media/patito/seagate/Personal/Datos familia"
run_sync "/media/patito/seagate/Personal/Documentos"
run_sync "/media/patito/seagate/Personal/Musica"
run_sync "/media/patito/seagate/Personal/Programar"
run_sync "/media/patito/seagate/Personal/Videos/Baile/Salsemba"

echo ""
echo "============================================================"
echo "ALL FOLDERS DONE: $(date)"
echo "============================================================"

$PYTHON "$MAIN" --config "$CONFIG" status
