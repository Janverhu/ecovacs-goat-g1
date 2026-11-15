"""Tests for the opt-in debug capture store."""

import json
from pathlib import Path
import sys
import threading
import types

import pytest

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

ecovacs_goat_g1 = types.ModuleType("custom_components.ecovacs_goat_g1")
ecovacs_goat_g1.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.ecovacs_goat_g1", ecovacs_goat_g1)

from custom_components.ecovacs_goat_g1.debug_capture import DebugCaptureStore


def _store(tmp_path: Path) -> DebugCaptureStore:
    return DebugCaptureStore(tmp_path / "debug", tmp_path / "www")


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_capture_is_on_disk_once_it_stops(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.add_redaction_value("secret-did")
    session_id = store.start(reason="test")["active"]

    store.capture_event(
        "mqtt_message", {"topic": "iot/atr/onPos/secret-did/x/y/j", "did": "abc"}
    )
    summary = store.stop()

    session = tmp_path / "debug" / session_id
    events = _events(session / "events.jsonl")
    assert [event["type"] for event in events] == [
        "capture_started",
        "mqtt_message",
        "capture_stopped",
    ]
    assert events[1]["data"] == {
        "topic": "iot/atr/onPos/<redacted>/x/y/j",
        "did": "<redacted>",
    }
    manifest = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["reason"] == "test"
    assert manifest["stop_reason"] == "manual"
    assert summary["active"] is None
    assert summary["sessions"][0]["bytes"] == (session / "events.jsonl").stat().st_size
    store.close()


def test_event_loop_calls_do_not_touch_the_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """capture_event, mark and summary run on the event loop and the MQTT thread."""
    store = _store(tmp_path)
    store.start()
    caller = threading.current_thread()

    def only_off_the_caller(original):
        def guarded(*args, **kwargs):
            if threading.current_thread() is caller:
                raise AssertionError("disk access on the calling thread")
            return original(*args, **kwargs)

        return guarded

    for name in ("open", "iterdir", "read_text", "write_text", "stat", "exists"):
        monkeypatch.setattr(Path, name, only_off_the_caller(getattr(Path, name)))
    store.capture_event("mqtt_message", {"topic": "t"})
    store.mark("note")
    summary = store.summary()
    monkeypatch.undo()

    assert summary["active"] is not None
    store.stop()
    session = tmp_path / "debug" / summary["active"]
    assert [event["type"] for event in _events(session / "events.jsonl")] == [
        "capture_started",
        "mqtt_message",
        "marker",
        "capture_stopped",
    ]
    store.close()


def test_load_lists_sessions_kept_from_before_a_restart(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session_id = store.start(reason="before restart")["active"]
    store.stop()
    store.close()

    restarted = _store(tmp_path)
    assert restarted.summary()["sessions"] == []
    restarted.load()

    sessions = restarted.summary()["sessions"]
    assert [session["session_id"] for session in sessions] == [session_id]
    assert sessions[0]["stop_reason"] == "manual"


def test_only_the_newest_sessions_are_kept(tmp_path: Path) -> None:
    store = DebugCaptureStore(tmp_path / "debug", tmp_path / "www", retained_sessions=2)
    session_ids = [store.start()["active"] for _ in range(3)]
    store.stop()

    kept = sorted(path.name for path in (tmp_path / "debug").iterdir())
    assert kept == sorted(session_ids[1:])
    assert [session["session_id"] for session in store.summary()["sessions"]] == sorted(
        session_ids[1:], reverse=True
    )
    store.close()


def test_export_and_clear(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.start()
    store.capture_event("mqtt_message", {"topic": "t"})

    export = store.export_zip()

    assert Path(export["path"]).is_file()
    assert store.summary()["last_export"] == export

    summary = store.clear()

    assert summary == {"active": None, "sessions": [], "last_export": None}
    assert not (tmp_path / "debug").exists()
    assert not (tmp_path / "www").exists()
    store.close()
