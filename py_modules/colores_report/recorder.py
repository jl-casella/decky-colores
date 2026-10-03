from __future__ import annotations

import asyncio
import glob
import json
import os
import time
from datetime import datetime, timezone

from .collector import ensure_diagnostics_directory, redact_text

POLL_INTERVAL_SECONDS = 15
RETENTION_SECONDS = 30 * 60
MAX_CAPTURE_BYTES = 10 * 1024 * 1024
MAX_BATCH_BYTES = 512 * 1024
MAX_STEAM_FILE_BYTES = 64 * 1024

_JOURNAL_QUERIES = {
    "plugin_loader": ["-u", "plugin_loader"],
    "kernel": ["-k"],
    "hhd": ["-u", "hhd.service"],
    "steam_gamescope": [
        "_COMM=steam",
        "_COMM=steamwebhelper",
        "_COMM=gamescope",
        "_COMM=gamescope-session",
    ],
}


class LocalDiagnosticsRecorder:
    """Append-only, bounded local diagnostics stream with resumable cursors."""

    def __init__(
        self, directory: str, home: str, started_at: float, runner,
        plugin_log_dir: str = "",
    ):
        self.directory = directory
        self.home = home
        self.started_at = float(started_at)
        self.runner = runner
        self.plugin_log_dir = plugin_log_dir
        self.state_path = os.path.join(directory, ".colores-capture-state.json")
        self._state = self._load_state()

    def prepare(self) -> str:
        ensure_diagnostics_directory(self.directory)
        self._append_records([{
            "timestamp": time.time(),
            "source": "colores",
            "message": "diagnostics capture started",
        }])
        self._save_state()
        return self.current_file()

    def current_file(self) -> str:
        minute = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
        return os.path.join(self.directory, f"colores-session-{minute}.jsonl")

    async def run(self) -> None:
        while True:
            worker = asyncio.create_task(asyncio.to_thread(self.poll_once))
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(worker)
                finally:
                    raise
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

    def poll_once(self) -> None:
        ensure_diagnostics_directory(self.directory)
        now = time.time()
        records = []
        cursors = self._state.setdefault("journal_cursors", {})
        for source, filters in _JOURNAL_QUERIES.items():
            args = ["/usr/bin/journalctl", "--no-pager", "--show-cursor", "-o", "json"]
            cursor = cursors.get(source)
            # Bound recovery after a long shutdown even when a saved cursor exists.
            args.extend(["--since", f"@{max(self.started_at, now - RETENTION_SECONDS):.3f}"])
            if cursor:
                args.append(f"--after-cursor={cursor}")
            args.extend(filters)
            try:
                output = self.runner(args)
            except Exception:  # noqa: BLE001
                output = None
            if not output:
                continue
            next_cursor, journal_records = self._parse_journal_output(source, output)
            records.extend(journal_records)
            if next_cursor:
                cursors[source] = next_cursor

        records.extend(self._read_colores_deltas(now))
        records.extend(self._read_steam_deltas(now))
        records = self._bounded_batch(records)
        self._append_records(records)
        self._prune(now)
        self._save_state()

    def read_records(self, since: float | None = None) -> list[dict]:
        cutoff = max(time.time() - RETENTION_SECONDS, since or 0)
        records = []
        for path in self._capture_files():
            try:
                with open(path, encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            record = json.loads(line)
                        except (ValueError, TypeError):
                            continue
                        if float(record.get("timestamp", 0)) >= cutoff:
                            records.append(record)
            except OSError:
                continue
        return records

    def _load_state(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                state = json.load(handle)
            if isinstance(state, dict):
                return state
        except (OSError, ValueError):
            pass
        return {"journal_cursors": {}, "steam_offsets": {}}

    def _save_state(self) -> None:
        temporary = f"{self.state_path}.{os.getpid()}.tmp"
        try:
            ensure_diagnostics_directory(self.directory)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._state, handle, ensure_ascii=False)
                handle.write("\n")
            os.replace(temporary, self.state_path)
            os.chmod(self.state_path, 0o600)
        except OSError:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    def _parse_journal_output(self, source: str, output: str) -> tuple[str | None, list[dict]]:
        cursor = None
        records = []
        for line in output.splitlines():
            if line.startswith("-- cursor: "):
                cursor = line.partition(": ")[2].strip()
                continue
            try:
                entry = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(entry, dict):
                continue
            try:
                timestamp = int(entry.get("__REALTIME_TIMESTAMP", "0")) / 1_000_000
            except (TypeError, ValueError):
                timestamp = time.time()
            message = entry.get("MESSAGE", "")
            if isinstance(message, list):
                message = "".join(chr(value) for value in message if isinstance(value, int))
            if isinstance(message, dict):
                message = str(message)
            record = {
                "timestamp": timestamp,
                "source": source,
                "message": redact_text(str(message), home=self.home),
            }
            unit = entry.get("_SYSTEMD_UNIT")
            comm = entry.get("_COMM")
            priority = entry.get("PRIORITY")
            if unit:
                record["unit"] = redact_text(str(unit), home=self.home)
            if comm:
                record["process"] = redact_text(str(comm), home=self.home)
            if priority is not None:
                record["priority"] = str(priority)
            records.append(record)
        return cursor, records

    def _read_steam_deltas(self, observed_at: float) -> list[dict]:
        roots = (
            os.path.join(self.home, ".local/share/Steam/logs"),
            os.path.join(self.home, ".steam/steam/logs"),
            os.path.join(self.home, ".steam/root/logs"),
        )
        return self._read_file_deltas(
            roots, observed_at, source="steam_file", state_key="steam_offsets",
            patterns=("*.txt", "*.log"),
        )

    def _read_colores_deltas(self, observed_at: float) -> list[dict]:
        if not self.plugin_log_dir:
            return []
        return self._read_file_deltas(
            (self.plugin_log_dir,), observed_at, source="colores_file",
            state_key="colores_offsets", patterns=("*.log",),
        )

    def _read_file_deltas(
        self, roots, observed_at: float, *, source: str, state_key: str, patterns,
    ) -> list[dict]:
        offsets = self._state.setdefault(state_key, {})
        records = []
        paths = set()
        for root in roots:
            for pattern in patterns:
                paths.update(glob.glob(os.path.join(root, pattern)))
        for path in sorted(paths):
            try:
                stat = os.stat(path)
                identity = f"{stat.st_dev}:{stat.st_ino}"
                saved = offsets.get(path, {})
                offset = int(saved.get("offset", 0)) if saved.get("identity") == identity else 0
                if stat.st_size < offset:
                    offset = 0
                if not saved:
                    offset = max(0, stat.st_size - MAX_STEAM_FILE_BYTES)
                with open(path, "rb") as handle:
                    handle.seek(offset)
                    raw = handle.read(MAX_STEAM_FILE_BYTES)
                    read_start = offset
                    if handle.tell() < stat.st_size:
                        handle.seek(0, os.SEEK_END)
                        end = handle.tell()
                        read_start = max(offset, end - MAX_STEAM_FILE_BYTES)
                        handle.seek(read_start)
                        raw = handle.read(MAX_STEAM_FILE_BYTES)
                        offset = end
                    else:
                        offset = handle.tell()
                offsets[path] = {"identity": identity, "offset": offset}
            except OSError:
                continue
            text = raw.decode("utf-8", "replace")
            if read_start > 0 and "\n" in text:
                text = text[text.find("\n") + 1:]
            for line in text.splitlines():
                if line.strip():
                    records.append({
                        "timestamp": observed_at,
                        "source": source,
                        "file": os.path.basename(path),
                        "message": redact_text(line, home=self.home),
                    })
        active = set(paths)
        self._state[state_key] = {
            path: value for path, value in offsets.items() if path in active
        }
        return records

    @staticmethod
    def _bounded_batch(records: list[dict]) -> list[dict]:
        selected = []
        budget = MAX_BATCH_BYTES
        for record in reversed(records):
            encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            if len(encoded) > budget:
                continue
            selected.append(record)
            budget -= len(encoded)
        selected.reverse()
        return selected

    def _append_records(self, records: list[dict]) -> None:
        if not records:
            return
        path = self.current_file()
        ensure_diagnostics_directory(self.directory)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o644)

    def _capture_files(self) -> list[str]:
        return sorted(glob.glob(os.path.join(self.directory, "colores-session-*.jsonl")))

    def _prune(self, now: float) -> None:
        paths = self._capture_files()
        cutoff = now - RETENTION_SECONDS
        for path in paths:
            try:
                if os.path.getmtime(path) < cutoff:
                    os.unlink(path)
            except OSError:
                continue
        paths = self._capture_files()
        sizes = {}
        for path in paths:
            try:
                sizes[path] = os.path.getsize(path)
            except OSError:
                continue
        total = sum(sizes.values())
        current = self.current_file()
        for path in paths:
            if total <= MAX_CAPTURE_BYTES:
                break
            if path == current:
                continue
            try:
                os.unlink(path)
                total -= sizes.get(path, 0)
            except OSError:
                pass
        if total > MAX_CAPTURE_BYTES and os.path.exists(current):
            try:
                with open(current, "rb") as handle:
                    lines = handle.readlines()
                kept = []
                used = 0
                for line in reversed(lines):
                    if used + len(line) > MAX_CAPTURE_BYTES:
                        break
                    kept.append(line)
                    used += len(line)
                temporary = f"{current}.{os.getpid()}.tmp"
                with open(temporary, "wb") as handle:
                    handle.writelines(reversed(kept))
                os.replace(temporary, current)
                os.chmod(current, 0o644)
            except OSError:
                pass
