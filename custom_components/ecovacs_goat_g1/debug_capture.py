"""Opt-in debug capture support for ECOVACS GOAT troubleshooting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from threading import Lock
import time
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

DEFAULT_CAPTURE_DURATION_SECONDS = 30 * 60
DEFAULT_CAPTURE_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_RETAINED_SESSIONS = 3
REDACTED = "<redacted>"
REDACT_KEYS = {
    "accessToken",
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
    "token",
    "uid",
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
        self._redaction_values: set[str] = set()
        self._default_include_raw_payloads = True
        self._default_max_duration_seconds = DEFAULT_CAPTURE_DURATION_SECONDS
        self._default_max_bytes = DEFAULT_CAPTURE_MAX_BYTES
        self._last_export: dict[str, str] | None = None

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
            if self._session is not None:
                self._stop_locked("replaced")

            self._base_path.mkdir(parents=True, exist_ok=True)
            session_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            session_path = self._base_path / session_id
            suffix = 1
            while session_path.exists():
                suffix += 1
                session_path = self._base_path / f"{session_id}-{suffix}"
            session_path.mkdir(parents=True)

            started_at = _utc_now()
            session = CaptureSession(
                session_id=session_path.name,
                path=session_path,
                started_at=started_at,
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
            self._session = session
            self._event_path = session_path / "events.jsonl"
            self._write_manifest_locked(reason=reason)
            self._write_event_locked(
                "capture_started",
                {
                    "reason": reason,
                    "include_raw_payloads": session.include_raw_payloads,
                    "max_duration_seconds": session.max_duration_seconds,
                    "max_bytes": session.max_bytes,
                },
            )
            self._cleanup_old_sessions_locked()
            return self.summary()

    def stop(self, reason: str = "manual") -> dict[str, Any]:
        """Stop the current capture session."""
        with self._lock:
            self._stop_locked(reason)
            return self.summary()

    def clear(self) -> dict[str, Any]:
        """Remove all capture sessions and exported zips."""
        with self._lock:
            self._stop_locked("cleared")
            if self._base_path.exists():
                shutil.rmtree(self._base_path)
            if self._export_path.exists():
                shutil.rmtree(self._export_path)
            self._last_export = None
            return self.summary()

    def mark(self, message: str) -> None:
        """Add a user marker to the active capture."""
        self.capture_event("marker", {"message": message})

    def capture_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Append a redacted event to the active capture session."""
        with self._lock:
            if self._session is None:
                return
            if self._session_expired_locked():
                self._stop_locked("expired")
                return
            if self._size_exceeded_locked():
                self._stop_locked("max_size_exceeded")
                return
            self._write_event_locked(event_type, data or {})
            if self._size_exceeded_locked():
                self._stop_locked("max_size_exceeded")

    def summary(self) -> dict[str, Any]:
        """Return a small serialisable capture summary."""
        sessions = []
        if self._base_path.exists():
            for path in sorted(self._base_path.iterdir(), reverse=True):
                if not path.is_dir():
                    continue
                manifest = _read_json(path / "manifest.json")
                event_path = path / "events.jsonl"
                sessions.append(
                    {
                        "session_id": path.name,
                        "started_at": manifest.get("started_at"),
                        "stopped_at": manifest.get("stopped_at"),
                        "stop_reason": manifest.get("stop_reason"),
                        "bytes": event_path.stat().st_size if event_path.exists() else 0,
                    }
                )
        return {
            "active": self._session.session_id if self._session else None,
            "sessions": sessions,
            "last_export": self._last_export,
        }

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent events from the latest capture session."""
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
        session = self._session_path(session_id) if session_id else self._latest_session_path()
        if session is None:
            raise FileNotFoundError("No ECOVACS debug capture sessions exist")

        self._export_path.mkdir(parents=True, exist_ok=True)
        zip_path = self._export_path / f"{session.name}.zip"
        with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
            for path in sorted(session.iterdir()):
                if path.is_file():
                    archive.write(path, arcname=path.name)
        self._last_export = {
            "session_id": session.name,
            "path": str(zip_path),
            "url": f"/local/ecovacs_goat/debug/{zip_path.name}",
        }
        return self._last_export

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
        with self._event_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
            file.write("\n")

    def _write_manifest_locked(self, *, reason: str | None) -> None:
        assert self._session is not None
        manifest = {
            "session_id": self._session.session_id,
            "started_at": self._session.started_at,
            "reason": reason,
            "include_raw_payloads": self._session.include_raw_payloads,
            "max_duration_seconds": self._session.max_duration_seconds,
            "max_bytes": self._session.max_bytes,
        }
        (self._session.path / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )

    def _stop_locked(self, reason: str) -> None:
        if self._session is None:
            return
        self._write_event_locked("capture_stopped", {"reason": reason})
        manifest_path = self._session.path / "manifest.json"
        manifest = _read_json(manifest_path)
        manifest["stopped_at"] = _utc_now()
        manifest["stop_reason"] = reason
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self._session = None
        self._event_path = None

    def _session_expired_locked(self) -> bool:
        assert self._session is not None
        started = datetime.fromisoformat(self._session.started_at).timestamp()
        return time.time() - started > self._session.max_duration_seconds

    def _size_exceeded_locked(self) -> bool:
        if self._event_path is None or not self._event_path.exists():
            return False
        assert self._session is not None
        return self._event_path.stat().st_size > self._session.max_bytes

    def _cleanup_old_sessions_locked(self) -> None:
        if not self._base_path.exists():
            return
        sessions = [path for path in sorted(self._base_path.iterdir()) if path.is_dir()]
        for path in sessions[: -self._retained_sessions]:
            shutil.rmtree(path, ignore_errors=True)

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
