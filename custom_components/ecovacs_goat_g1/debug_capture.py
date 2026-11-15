"""Opt-in debug capture support for ECOVACS GOAT troubleshooting.

Events arrive on the Home Assistant event loop and the MQTT thread, so
``capture_event`` and ``summary`` never touch the disk: a writer thread appends
the events, and the session list is kept in memory. ``load``, ``start``,
``stop``, ``clear``, ``export_zip``, ``recent_events`` and ``close`` do file
work and must run in an executor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
import json
import logging
from pathlib import Path
import queue
import shutil
from threading import Lock, Thread
import time
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

_LOGGER = logging.getLogger(__name__)

DEFAULT_CAPTURE_DURATION_SECONDS = 30 * 60
DEFAULT_CAPTURE_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_RETAINED_SESSIONS = 3
REDACTED = "<redacted>"
REDACT_KEYS = {
    "accessToken",
    "access_token",
    "account_uid",
    "auth",
    "authCode",
    "authorization",
    "did",
    "eid",
    "er",
    "homeId",
    "name",
    "nick",
    "password",
    "resource",
    "session_store_id",
    "token",
    "uid",
    "user_id",
    "userId",
    "userid",
}


@dataclass(frozen=True)
class CaptureSession:
    """Metadata for a capture session."""

    session_id: str
    path: Path
    started_at: str
    include_raw_payloads: bool
    max_duration_seconds: int
    max_bytes: int


class DebugCaptureStore:
    """Bounded JSONL capture store used only when explicitly enabled."""

    def __init__(
        self,
        base_path: Path,
        export_path: Path,
        *,
        retained_sessions: int = DEFAULT_RETAINED_SESSIONS,
    ) -> None:
        """Initialize the capture store."""
        self._base_path = base_path
        self._export_path = export_path
        self._retained_sessions = retained_sessions
        self._lock = Lock()
        self._session: CaptureSession | None = None
        self._event_path: Path | None = None
        self._manifest: dict[str, Any] = {}
        self._session_bytes = 0
        self._sessions: dict[str, dict[str, Any]] = {}
        self._redaction_values: set[str] = set()
        self._default_include_raw_payloads = True
        self._default_max_duration_seconds = DEFAULT_CAPTURE_DURATION_SECONDS
        self._default_max_bytes = DEFAULT_CAPTURE_MAX_BYTES
        self._last_export: dict[str, str] | None = None
        self._writes: queue.Queue[Callable[[], None] | None] = queue.Queue()
        self._writer: Thread | None = None

    def configure(
        self,
        *,
        include_raw_payloads: bool | None = None,
        max_duration_seconds: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        """Set defaults used by subsequent capture sessions."""
        if include_raw_payloads is not None:
            self._default_include_raw_payloads = include_raw_payloads
        if max_duration_seconds is not None:
            self._default_max_duration_seconds = max(60, max_duration_seconds)
        if max_bytes is not None:
            self._default_max_bytes = max(1024 * 1024, max_bytes)

    def add_redaction_value(self, value: Any) -> None:
        """Register an exact string value that must be redacted."""
        if value is None:
            return
        text = str(value)
        if text:
            self._redaction_values.add(text)

    @property
    def is_active(self) -> bool:
        """Return whether a capture session is active."""
        return self._session is not None

    def load(self) -> None:
        """Read the retained sessions from disk."""
        sessions: dict[str, dict[str, Any]] = {}
        if self._base_path.exists():
            for path in self._base_path.iterdir():
                if path.is_dir():
                    sessions[path.name] = _session_summary(path)
        with self._lock:
            if self._session is not None:
                sessions[self._session.session_id] = self._sessions[
                    self._session.session_id
                ]
            self._sessions = sessions

    def start(
        self,
        *,
        reason: str | None = None,
        include_raw_payloads: bool | None = None,
        max_duration_seconds: int | None = None,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Start a new capture session, replacing any active session."""
        with self._lock:
            self._stop_locked("replaced")
        self._writes.join()

        self._base_path.mkdir(parents=True, exist_ok=True)
        session_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        session_path = self._base_path / session_id
        suffix = 1
        while session_path.exists():
            suffix += 1
            session_path = self._base_path / f"{session_id}-{suffix}"
        session_path.mkdir(parents=True)

        session = CaptureSession(
            session_id=session_path.name,
            path=session_path,
            started_at=_utc_now(),
            include_raw_payloads=self._default_include_raw_payloads
            if include_raw_payloads is None
            else include_raw_payloads,
            max_duration_seconds=self._default_max_duration_seconds
            if max_duration_seconds is None
            else max(60, max_duration_seconds),
            max_bytes=self._default_max_bytes
            if max_bytes is None
            else max(1024 * 1024, max_bytes),
        )
        manifest = {
            "session_id": session.session_id,
            "started_at": session.started_at,
            "reason": reason,
            "include_raw_payloads": session.include_raw_payloads,
            "max_duration_seconds": session.max_duration_seconds,
            "max_bytes": session.max_bytes,
        }
        _write_json(session_path / "manifest.json", manifest)
        removed = self._remove_old_sessions()

        with self._lock:
            for name in removed:
                self._sessions.pop(name, None)
            self._session = session
            self._event_path = session_path / "events.jsonl"
            self._manifest = manifest
            self._session_bytes = 0
            self._sessions[session.session_id] = {
                "session_id": session.session_id,
                "started_at": session.started_at,
                "stopped_at": None,
                "stop_reason": None,
                "bytes": 0,
            }
            self._write_event_locked(
                "capture_started",
                {
                    "reason": reason,
                    "include_raw_payloads": session.include_raw_payloads,
                    "max_duration_seconds": session.max_duration_seconds,
                    "max_bytes": session.max_bytes,
                },
            )
        return self.summary()

    def stop(self, reason: str = "manual") -> dict[str, Any]:
        """Stop the current capture session and wait until it is on disk."""
        with self._lock:
            self._stop_locked(reason)
        self._writes.join()
        return self.summary()

    def clear(self) -> dict[str, Any]:
        """Remove all capture sessions and exported zips."""
        with self._lock:
            self._stop_locked("cleared")
        self._writes.join()
        if self._base_path.exists():
            shutil.rmtree(self._base_path)
        if self._export_path.exists():
            shutil.rmtree(self._export_path)
        with self._lock:
            self._sessions = {}
            self._last_export = None
        return self.summary()

    def close(self) -> None:
        """Write any queued events and end the writer thread."""
        writer = self._writer
        if writer is None or not writer.is_alive():
            return
        self._writes.put(None)
        writer.join()
        self._writer = None

    def mark(self, message: str) -> None:
        """Add a user marker to the active capture."""
        self.capture_event("marker", {"message": message})

    def capture_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Queue a redacted event for the active capture session."""
        with self._lock:
            if self._session is None:
                return
            if self._session_expired_locked():
                self._stop_locked("expired")
                return
            if self._session_bytes > self._session.max_bytes:
                self._stop_locked("max_size_exceeded")
                return
            self._write_event_locked(event_type, data or {})
            if self._session_bytes > self._session.max_bytes:
                self._stop_locked("max_size_exceeded")

    def summary(self) -> dict[str, Any]:
        """Return a small serialisable capture summary."""
        with self._lock:
            return {
                "active": self._session.session_id if self._session else None,
                "sessions": [
                    dict(self._sessions[name])
                    for name in sorted(self._sessions, reverse=True)
                ],
                "last_export": self._last_export,
            }

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent events from the latest capture session."""
        self._writes.join()
        session = self._latest_session_path()
        if session is None:
            return []
        event_path = session / "events.jsonl"
        if not event_path.exists():
            return []
        lines = event_path.read_text(encoding="utf-8").splitlines()[-limit:]
        events = []
        for line in lines:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def export_zip(self, session_id: str | None = None) -> dict[str, str]:
        """Create a zip for the requested or latest capture session."""
        self._writes.join()
        session = self._session_path(session_id) if session_id else self._latest_session_path()
        if session is None:
            raise FileNotFoundError("No ECOVACS debug capture sessions exist")

        self._export_path.mkdir(parents=True, exist_ok=True)
        zip_path = self._export_path / f"{session.name}.zip"
        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
            for path in sorted(session.iterdir()):
                if path.is_file():
                    archive.write(path, arcname=path.name)
        export = {
            "session_id": session.name,
            "path": str(zip_path),
            "url": f"/local/ecovacs_goat/debug/{zip_path.name}",
        }
        with self._lock:
            self._last_export = export
        return export

    def _write_event_locked(self, event_type: str, data: dict[str, Any]) -> None:
        assert self._session is not None
        assert self._event_path is not None
        event = {
            "ts": _utc_now(),
            "type": event_type,
            "data": self._redact(_json_safe(data)),
        }
        if not self._session.include_raw_payloads:
            event["data"] = _drop_raw_payloads(event["data"])
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        self._session_bytes += len(line.encode("utf-8"))
        self._sessions[self._session.session_id]["bytes"] = self._session_bytes
        self._enqueue(partial(_append_text, self._event_path, line))

    def _stop_locked(self, reason: str) -> None:
        if self._session is None:
            return
        self._write_event_locked("capture_stopped", {"reason": reason})
        stopped_at = _utc_now()
        manifest = {**self._manifest, "stopped_at": stopped_at, "stop_reason": reason}
        self._sessions[self._session.session_id].update(
            stopped_at=stopped_at, stop_reason=reason
        )
        self._enqueue(partial(_write_json, self._session.path / "manifest.json", manifest))
        self._session = None
        self._event_path = None

    def _enqueue(self, write: Callable[[], None]) -> None:
        if self._writer is None or not self._writer.is_alive():
            self._writer = Thread(
                target=self._write_queued,
                name="ecovacs_goat_debug_capture",
                daemon=True,
            )
            self._writer.start()
        self._writes.put(write)

    def _write_queued(self) -> None:
        while True:
            write = self._writes.get()
            try:
                if write is None:
                    return
                write()
            except OSError as err:
                _LOGGER.warning("ECOVACS debug capture write failed: %s", err)
            finally:
                self._writes.task_done()

    def _session_expired_locked(self) -> bool:
        assert self._session is not None
        started = datetime.fromisoformat(self._session.started_at).timestamp()
        return time.time() - started > self._session.max_duration_seconds

    def _remove_old_sessions(self) -> list[str]:
        if not self._base_path.exists():
            return []
        sessions = [path for path in sorted(self._base_path.iterdir()) if path.is_dir()]
        removed = sessions[: -self._retained_sessions]
        for path in removed:
            shutil.rmtree(path, ignore_errors=True)
        return [path.name for path in removed]

    def _latest_session_path(self) -> Path | None:
        if not self._base_path.exists():
            return None
        sessions = [path for path in sorted(self._base_path.iterdir()) if path.is_dir()]
        return sessions[-1] if sessions else None

    def _session_path(self, session_id: str | None) -> Path | None:
        if not session_id:
            return None
        path = self._base_path / session_id
        return path if path.is_dir() else None

    def _redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: REDACTED if key in REDACT_KEYS else self._redact(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        if isinstance(value, str):
            redacted = value
            for sensitive in sorted(self._redaction_values, key=len, reverse=True):
                redacted = redacted.replace(sensitive, REDACTED)
            return redacted
        return value


def _utc_now() -> str:
    """Return an ISO UTC timestamp."""
    return datetime.now(UTC).isoformat()


def _append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write(text)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _session_summary(path: Path) -> dict[str, Any]:
    manifest = _read_json(path / "manifest.json")
    event_path = path / "events.jsonl"
    return {
        "session_id": path.name,
        "started_at": manifest.get("started_at"),
        "stopped_at": manifest.get("stopped_at"),
        "stop_reason": manifest.get("stop_reason"),
        "bytes": event_path.stat().st_size if event_path.exists() else 0,
    }


def _json_safe(value: Any) -> Any:
    """Convert bytes and unknown objects into JSON-safe values."""
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _drop_raw_payloads(value: Any) -> Any:
    """Remove large raw payload bodies when raw capture is disabled."""
    if isinstance(value, dict):
        return {
            key: "<omitted>"
            if key in {"payload", "raw_payload", "request", "response"}
            else _drop_raw_payloads(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_drop_raw_payloads(item) for item in value]
    return value


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}
