#!/bin/sh

# Explicit Paths for Paxx12 Stability
LOG="/home/lava/printer_data/logs/nfc_spool_reader.log"
MAX_SIZE=5242880  # 5 MB in bytes
KEEP=5
PYTHON="/usr/bin/python3"
SCRIPT="/home/lava/printer_data/config/extended/nfc_spool_reader.py"
DATE_BIN="/bin/date"

log_msg() {
    echo "$($DATE_BIN '+%F %T') - $1" >> "$LOG"
}

# --- Improved: log termination ---
trap 'log_msg "Wrapper received SIGINT, exiting"; exit 0' INT
trap 'log_msg "Wrapper received SIGTERM, exiting"; exit 0' TERM
trap 'log_msg "Wrapper exiting"' EXIT
# --------------------------------

rotate_log() {
    [ -f "$LOG" ] || return 0

    size=$(stat -c%s "$LOG" 2>/dev/null || echo 0)
    [ "$size" -lt "$MAX_SIZE" ] && return 0

    i=$KEEP
    while [ "$i" -ge 1 ]; do
        prev=$((i - 1))

        [ "$i" -eq "$KEEP" ] && rm -f "$LOG.$i"

        target="$LOG.$i"
        source="$LOG"
        [ "$prev" -ne 0 ] && source="$LOG.$prev"

        [ -f "$source" ] && mv "$source" "$target"

        i=$((i - 1))
    done

    touch "$LOG"
}

log_msg "Wrapper started"

# Main loop
while true; do
    rotate_log

    log_msg "Starting NFC Watcher"

    # Run Python unbuffered
    "$PYTHON" -u "$SCRIPT" >> "$LOG" 2>&1

    EXIT_CODE=$?
    log_msg "Watcher exited ($EXIT_CODE), restarting in 5s"

    sleep 5
done
