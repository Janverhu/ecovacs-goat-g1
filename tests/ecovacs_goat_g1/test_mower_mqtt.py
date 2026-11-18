"""Tests for signing the MQTT push client in again after the broker refuses it."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types
from typing import Any

from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
import pytest

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

ecovacs_goat_g1 = types.ModuleType("custom_components.ecovacs_goat_g1")
ecovacs_goat_g1.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.ecovacs_goat_g1", ecovacs_goat_g1)

from custom_components.ecovacs_goat_g1 import mower_mqtt  # noqa: E402
from custom_components.ecovacs_goat_g1.mower_api import (  # noqa: E402
    Credentials,
    EcovacsApiError,
    EcovacsAuthError,
)
from custom_components.ecovacs_goat_g1.mower_models import MowerDevice  # noqa: E402
from custom_components.ecovacs_goat_g1.mower_mqtt import MowerMqttClient  # noqa: E402

DEVICE = MowerDevice(
    did="did", device_class="class", resource="resource", name="GOAT", model="GOAT G1-800"
)
NOT_AUTHORIZED = ReasonCode(PacketTypes.CONNACK, "Not authorized")
BAD_PASSWORD = ReasonCode(PacketTypes.CONNACK, "Bad user name or password")
SERVER_UNAVAILABLE = ReasonCode(PacketTypes.CONNACK, "Server unavailable")
SUCCESS = ReasonCode(PacketTypes.CONNACK, "Success")


class FakeClient:
    """Record what the push client asks paho to do."""

    def __init__(self, *, callback_api_version: Any, client_id: str) -> None:
        self.client_id = client_id
        self.password: str | None = None
        self.calls: list[str] = []
        self.on_connect: Any = None
        self.on_message: Any = None
        self.on_disconnect: Any = None

    def username_pw_set(self, _username: str, password: str) -> None:
        self.password = password

    def tls_set_context(self, _context: Any) -> None:
        return None

    def connect(self, _host: str, _port: int, _keepalive: int) -> None:
        self.calls.append("connect")

    def loop_start(self) -> None:
        self.calls.append("loop_start")

    def disconnect(self) -> None:
        self.calls.append("disconnect")

    def loop_stop(self) -> None:
        self.calls.append("loop_stop")

    def subscribe(self, _topic: str) -> None:
        self.calls.append("subscribe")

    def connack(self, reason_code: ReasonCode) -> None:
        """Deliver a CONNACK the way paho's network thread does."""
        self.on_connect(self, None, None, reason_code, None)


class FakeApi:
    """Hand out a cached token, and a new one on a forced login."""

    continent = "eu"
    client_device_id = "ABCDEF12"

    def __init__(self) -> None:
        self.token = "token-1"
        self.logins: list[bool] = []
        self.errors: list[Exception] = []

    async def authenticate(self, *, force: bool = False) -> Credentials:
        self.logins.append(force)
        if self.errors:
            raise self.errors.pop(0)
        if force:
            self.token = f"token-{len(self.logins)}"
        return Credentials(user_id="uid", token=self.token, expires_at=0)


@pytest.fixture
def clients(monkeypatch: pytest.MonkeyPatch) -> list[FakeClient]:
    """Replace paho's client and collect every instance the push client creates."""
    created: list[FakeClient] = []

    def _client(**kwargs: Any) -> FakeClient:
        client = FakeClient(**kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(mower_mqtt.mqtt, "Client", _client)
    return created


@pytest.fixture
def delays(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the sign-in backoff instead of waiting for it."""
    waited: list[float] = []
    real_sleep = asyncio.sleep

    async def _sleep(delay: float, *args: Any) -> None:
        if delay:
            waited.append(delay)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return waited


async def _settle(push: MowerMqttClient) -> None:
    """Let a scheduled sign-in run to completion."""
    await asyncio.sleep(0)
    if push._relogin_task is not None:
        await push._relogin_task


def _push(api: FakeApi, **kwargs: Any) -> MowerMqttClient:
    return MowerMqttClient(
        api, DEVICE, asyncio.get_running_loop(), lambda _topic, _payload: None, **kwargs
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reason_code", [NOT_AUTHORIZED, BAD_PASSWORD])
async def test_refused_token_signs_in_again(
    clients: list[FakeClient], delays: list[float], reason_code: ReasonCode
) -> None:
    """A refused token is replaced by a forced login on a new client."""
    api = FakeApi()
    push = _push(api)
    await push.start()

    clients[0].connack(reason_code)
    await _settle(push)

    # paho must stop reconnecting with the refused token right away.
    assert clients[0].calls == ["connect", "loop_start", "disconnect", "disconnect", "loop_stop"]
    assert api.logins == [False, False, True]
    assert delays == [30]
    assert len(clients) == 2
    assert clients[1].password == api.token != "token-1"
    assert clients[1].calls == ["connect", "loop_start"]

    clients[1].connack(SUCCESS)
    assert clients[1].calls[-1] == "subscribe"


@pytest.mark.asyncio
async def test_token_refreshed_elsewhere_is_reused(
    clients: list[FakeClient], delays: list[float]
) -> None:
    """A token the API already renewed is used without another login."""
    api = FakeApi()
    push = _push(api)
    await push.start()
    api.token = "token-renewed"

    clients[0].connack(NOT_AUTHORIZED)
    await _settle(push)

    assert api.logins == [False, False]
    assert clients[1].password == "token-renewed"


@pytest.mark.asyncio
async def test_backoff_doubles_up_to_an_hour_and_resets(
    clients: list[FakeClient], delays: list[float]
) -> None:
    """Every refusal doubles the wait until a connect succeeds."""
    api = FakeApi()
    push = _push(api)
    await push.start()

    for _ in range(9):
        clients[-1].connack(NOT_AUTHORIZED)
        await _settle(push)
    assert delays == [30, 60, 120, 240, 480, 960, 1920, 3600, 3600]

    clients[-1].connack(SUCCESS)
    clients[-1].connack(NOT_AUTHORIZED)
    await _settle(push)
    assert delays[-1] == 30


@pytest.mark.asyncio
async def test_failed_sign_in_retries_with_backoff(
    clients: list[FakeClient], delays: list[float]
) -> None:
    """Network or API errors during sign-in are retried, not given up."""
    api = FakeApi()
    push = _push(api)
    await push.start()
    api.errors = [EcovacsApiError("POST failed"), OSError("unreachable")]

    clients[0].connack(NOT_AUTHORIZED)
    await _settle(push)

    assert delays == [30, 60, 120]
    assert len(clients) == 2
    assert clients[1].calls == ["connect", "loop_start"]


@pytest.mark.asyncio
async def test_only_one_sign_in_at_a_time(
    clients: list[FakeClient], delays: list[float]
) -> None:
    """Repeated refusals while a sign-in is pending do not start another."""
    api = FakeApi()
    push = _push(api)
    await push.start()

    clients[0].connack(NOT_AUTHORIZED)
    clients[0].connack(NOT_AUTHORIZED)
    await asyncio.sleep(0)
    task = push._relogin_task
    clients[0].connack(NOT_AUTHORIZED)
    await asyncio.sleep(0)
    assert push._relogin_task is task
    await _settle(push)

    assert delays == [30]
    assert len(clients) == 2
    assert api.logins == [False, False, True]


@pytest.mark.asyncio
async def test_stop_cancels_pending_sign_in(
    clients: list[FakeClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stopping the client cancels a sign-in that is still waiting."""
    api = FakeApi()
    push = _push(api)
    await push.start()
    waiting = asyncio.Event()
    real_sleep = asyncio.sleep

    async def _sleep(delay: float, *args: Any) -> None:
        if delay:
            waiting.set()
            await asyncio.Event().wait()
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _sleep)

    clients[0].connack(NOT_AUTHORIZED)
    await asyncio.wait_for(waiting.wait(), 5)
    task = push._relogin_task
    assert task is not None

    await push.stop()
    clients[0].connack(NOT_AUTHORIZED)
    await real_sleep(0)

    assert task.cancelled()
    assert push._relogin_task is None
    assert api.logins == [False]
    assert len(clients) == 1


@pytest.mark.asyncio
async def test_dead_session_reports_login_failure(
    clients: list[FakeClient], delays: list[float]
) -> None:
    """A login that needs the user stops retrying and reports the error."""
    api = FakeApi()
    failures: list[EcovacsAuthError] = []
    push = _push(api, on_login_failed=failures.append)
    await push.start()
    error = EcovacsAuthError("ECOVACS login expired; reauthentication required")
    api.errors = [error]

    clients[0].connack(NOT_AUTHORIZED)
    await _settle(push)

    assert failures == [error]
    assert delays == [30]
    assert len(clients) == 1


@pytest.mark.asyncio
async def test_other_connect_failures_keep_paho_reconnecting(
    clients: list[FakeClient], delays: list[float]
) -> None:
    """A refusal that is not about the token leaves the client to paho."""
    api = FakeApi()
    push = _push(api)
    await push.start()

    clients[0].connack(SERVER_UNAVAILABLE)
    await _settle(push)

    assert push._relogin_task is None
    assert clients[0].calls == ["connect", "loop_start"]
    assert api.logins == [False]
    assert delays == []
