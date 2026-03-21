import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

KLIPPY_LOG = "/home/lava/printer_data/logs/klippy.log"
MOONRAKER_GCODE_URL = "http://127.0.0.1:7125/printer/gcode/script"
SPOOLMAN_API_BASE = "http://poolman-ip:port/api/v1"

POLL_INTERVAL = 0.25
REQUEST_TIMEOUT = 30
DEDUP_SECONDS = 10.0
START_AT_END = True

VALID_CHANNELS = {0, 1, 2, 3}
EVENT_MAX_LINES = 120
EVENT_MAX_AGE_SECONDS = 10.0
ASSIGNMENT_RETRY_INTERVAL = 5.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("nfc_spool_reader")

NTAG_READ_RE = re.compile(r"NTAG read successful", re.IGNORECASE)
CHANNEL_RE = re.compile(r"channel\[(\d+)\]", re.IGNORECASE)
OPEN_SPOOL_JSON_RE = re.compile(r"OpenSpool JSON payload:\s*(\{.*\})", re.IGNORECASE)


@dataclass
class OpenSpoolRecord:
    channel: int
    payload: Dict[str, Any]

    @property
    def spool_id(self) -> Optional[int]:
        value = self.payload.get("spool_id")
        try:
            return None if value in (None, "") else int(value)
        except (TypeError, ValueError):
            return None

    def fingerprint(self) -> str:
        return json.dumps(
            {"channel": self.channel, "spool_id": self.spool_id},
            sort_keys=True,
        )


@dataclass
class PendingScanEvent:
    started_line_no: int
    started_monotonic: float
    channel: Optional[int] = None

    def is_expired(self, current_line_no: int, now_monotonic: float) -> bool:
        return (
            current_line_no - self.started_line_no > EVENT_MAX_LINES
            or now_monotonic - self.started_monotonic > EVENT_MAX_AGE_SECONDS
        )


class Deduper:
    def __init__(self, ttl_seconds: float):
        self.ttl_seconds = ttl_seconds
        self._seen: Dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        now = time.time()
        self._seen = {k: ts for k, ts in self._seen.items() if now - ts <= self.ttl_seconds}
        if key in self._seen:
            return True
        self._seen[key] = now
        return False


class MoonrakerClient:
    def __init__(self, gcode_url: str, timeout: int = REQUEST_TIMEOUT):
        self.gcode_url = gcode_url
        self.timeout = timeout
        self.session = requests.Session()

    def set_channel_spool(self, channel: int, spool_id: int) -> bool:
        script = f"SET_CHANNEL_SPOOL CHANNEL={channel} ID={spool_id}"
        logger.info("Sending gcode: %s", script)
        try:
            resp = self.session.post(
                self.gcode_url,
                json={"script": script},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            return True
        except requests.exceptions.Timeout:
            logger.warning("Timed out sending gcode: %s", script)
            return False
        except requests.exceptions.RequestException:
            logger.exception("HTTP error sending gcode: %s", script)
            return False


class SpoolmanClient:
    def __init__(self, base_url: str, timeout: int = REQUEST_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def spool_exists(self, spool_id: int) -> bool:
        url = f"{self.base_url}/spool/{spool_id}"
        try:
            resp = self.session.get(url, timeout=self.timeout)
            if resp.status_code == 200:
                return True
            logger.warning(
                "Spool lookup failed: spool_id=%s url=%s status=%s body=%r",
                spool_id, url, resp.status_code, resp.text[:300],
            )
            return False
        except requests.exceptions.RequestException:
            logger.exception("Error checking spool_id=%s at %s", spool_id, url)
            return False


class KlippyLogWatcher:
    def __init__(self, path: str, start_at_end: bool = True):
        self.path = path
        self.start_at_end = start_at_end
        self.fp = None
        self.line_no = 0
        self.current_event: Optional[PendingScanEvent] = None

    def open(self) -> None:
        self.fp = open(self.path, "r", encoding="utf-8", errors="replace")
        self.line_no = 0
        self.current_event = None
        if self.start_at_end:
            self.fp.seek(0, os.SEEK_END)
        logger.info("Watching log: %s", self.path)

    def reopen_if_rotated(self) -> None:
        if self.fp is None:
            self.open()
            return
        try:
            if os.stat(self.path).st_ino != os.fstat(self.fp.fileno()).st_ino:
                logger.info("Log rotated, reopening %s", self.path)
                self.fp.close()
                self.fp = None
                self.open()
        except FileNotFoundError:
            logger.warning("Log file not found: %s", self.path)
            time.sleep(1)

    def _expire_event_if_needed(self) -> None:
        if self.current_event and self.current_event.is_expired(self.line_no, time.monotonic()):
            self.current_event = None

    def _ensure_event(self) -> None:
        if self.current_event is None:
            self.current_event = PendingScanEvent(self.line_no, time.monotonic())

    @staticmethod
    def _extract_channel(line: str) -> Optional[int]:
        match = CHANNEL_RE.search(line)
        if not match:
            return None
        try:
            channel = int(match.group(1))
        except ValueError:
            return None
        return channel if channel in VALID_CHANNELS else None

    def poll(self) -> List[OpenSpoolRecord]:
        self.reopen_if_rotated()
        if self.fp is None:
            return []

        results: List[OpenSpoolRecord] = []

        while True:
            line = self.fp.readline()
            if not line:
                break

            self.line_no += 1
            line = line.rstrip("\n")
            self._expire_event_if_needed()

            if NTAG_READ_RE.search(line):
                self.current_event = PendingScanEvent(self.line_no, time.monotonic())
                continue

            channel = self._extract_channel(line)
            if channel is not None:
                self._ensure_event()
                self.current_event.channel = channel
                continue

            json_match = OPEN_SPOOL_JSON_RE.search(line)
            if not json_match:
                continue

            try:
                payload = json.loads(json_match.group(1))
            except Exception:
                logger.exception("Failed to parse OpenSpool payload: %r", json_match.group(1))
                continue

            if self.current_event is None or self.current_event.channel is None:
                logger.warning("Parsed payload but no channel was captured yet")
                continue

            record = OpenSpoolRecord(channel=self.current_event.channel, payload=payload)
            logger.info(
                "Parsed OpenSpool payload: channel=%s spool_id=%s",
                record.channel,
                record.spool_id,
            )
            results.append(record)
            self.current_event = None

        return results


class PendingAssignments:
    def __init__(self):
        self._pending: Dict[int, Dict[str, Any]] = {}

    def update(self, channel: int, spool_id: int) -> None:
        now = time.monotonic()
        existing = self._pending.get(channel)
        if existing and existing["spool_id"] == spool_id:
            existing["updated_at"] = now
            logger.info(
                "Channel %s already pending spool %s, keeping latest desired state",
                channel,
                spool_id,
            )
            return

        self._pending[channel] = {
            "spool_id": spool_id,
            "updated_at": now,
            "last_attempt_at": 0.0,
        }
        logger.info("Queued assignment: channel %s -> spool %s", channel, spool_id)

    def ready(self, retry_interval: float) -> List[tuple[int, int]]:
        now = time.monotonic()
        return [
            (channel, int(item["spool_id"]))
            for channel, item in self._pending.items()
            if now - item["last_attempt_at"] >= retry_interval
        ]

    def mark_attempt(self, channel: int) -> None:
        if channel in self._pending:
            self._pending[channel]["last_attempt_at"] = time.monotonic()

    def mark_success(self, channel: int, spool_id: int) -> None:
        item = self._pending.get(channel)
        if item and int(item["spool_id"]) == spool_id:
            self._pending.pop(channel, None)
            logger.info("Assignment applied successfully: channel %s -> spool %s", channel, spool_id)

    def has_pending(self) -> bool:
        return bool(self._pending)


class NFCSpoolReaderApp:
    def __init__(self):
        self.spoolman = SpoolmanClient(SPOOLMAN_API_BASE)
        self.moonraker = MoonrakerClient(MOONRAKER_GCODE_URL)
        self.watcher = KlippyLogWatcher(KLIPPY_LOG, start_at_end=START_AT_END)
        self.deduper = Deduper(DEDUP_SECONDS)
        self.pending_assignments = PendingAssignments()

    def handle_record(self, record: OpenSpoolRecord) -> None:
        spool_id = record.spool_id
        if spool_id is None:
            logger.warning("Missing spool_id in payload, skipping: %s", record.payload)
            return

        if self.deduper.is_duplicate(record.fingerprint()):
            logger.info("Duplicate payload ignored: %s", record.fingerprint())
            return

        if not self.spoolman.spool_exists(spool_id):
            logger.warning("spool_id=%s not found in Spoolman, skipping", spool_id)
            return

        logger.info("Discovered assignment channel %d -> spool %d", record.channel, spool_id)
        self.pending_assignments.update(record.channel, spool_id)

    def flush_pending_assignments(self) -> None:
        if not self.pending_assignments.has_pending():
            return

        for channel, spool_id in self.pending_assignments.ready(ASSIGNMENT_RETRY_INTERVAL):
            self.pending_assignments.mark_attempt(channel)
            if self.moonraker.set_channel_spool(channel, spool_id):
                self.pending_assignments.mark_success(channel, spool_id)
            else:
                logger.warning(
                    "Moonraker busy/unavailable, keeping pending assignment channel=%s spool_id=%s for retry",
                    channel,
                    spool_id,
                )

    def run(self) -> None:
        while not os.path.exists(KLIPPY_LOG):
            logger.warning("Waiting for log file: %s", KLIPPY_LOG)
            time.sleep(1)

        self.watcher.open()

        while True:
            try:
                for record in self.watcher.poll():
                    self.handle_record(record)
                self.flush_pending_assignments()
            except Exception:
                logger.exception("Watcher loop error")

            time.sleep(POLL_INTERVAL)


def main() -> None:
    NFCSpoolReaderApp().run()


if __name__ == "__main__":
    main()