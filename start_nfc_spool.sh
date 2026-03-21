#!/bin/sh
LOG="/home/lava/printer_data/logs/nfc_spool_reader.log"
MAX_SIZE=$((5 * 1024 * 1024))   # 5 MB
KEEP=5
PYTHON="/usr/bin/python3"
SCRIPT="/home/lava/printer_data/config/extended/nfc_spool_reader.py"

rotate_log() {
    [ -f "$LOG" ] || return 0

    size=$(wc -c < "$LOG" 2>/dev/null || echo 0)
    [ "$size" -lt "$MAX_SIZE" ] && return 0

    i=$KEEP
    while [ "$i" -ge 1 ]; do
        prev=$((i - 1))
        if [ "$i" -eq "$KEEP" ]; then
            [ -f "$LOG.$i" ] && rm -f "$LOG.$i"
        fi
        if [ "$prev" -eq 0 ]; then
            [ -f "$LOG" ] && mv "$LOG" "$LOG.1"
        else
            [ -f "$LOG.$prev" ] && mv "$LOG.$prev" "$LOG.$i"
        fi
        i=$((i - 1))
    done

    : > "$LOG"
}

while true; do
    rotate_log
    echo "$(date '+%F %T') starting watcher" >> "$LOG"
    "$PYTHON" "$SCRIPT" >> "$LOG" 2>&1
    echo "$(date '+%F %T') watcher exited, restarting in 2s" >> "$LOG"
    sleep 2
done
