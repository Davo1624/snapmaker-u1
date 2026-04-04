#!/bin/sh

LOG="/home/lava/printer_data/logs/nfc_spool_reader.log"
SCRIPT="/home/lava/printer_data/config/extended/nfc_spool_reader.py"
PYTHON="/usr/bin/python3"

log_msg() {
  echo "$(date) $1" >> "$LOG"
}

# --- NEW: trap termination signals ---
trap 'log_msg "wrapper received SIGTERM, exiting"; exit 0' TERM
trap 'log_msg "wrapper received SIGINT (Ctrl+C), exiting"; exit 0' INT
trap 'log_msg "wrapper exiting"; exit 0' EXIT
# ------------------------------------

log_msg "wrapper started"

while true; do
  log_msg "starting watcher"

  "$PYTHON" "$SCRIPT" >> "$LOG" 2>&1

  log_msg "watcher exited, restarting in 2s"

  sleep 2
done
