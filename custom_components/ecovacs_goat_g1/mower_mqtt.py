"""MQTT push client for ECOVACS GOAT mowers."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
import json
import logging
import ssl
from time import monotonic
from typing import Any

import paho.mqtt.client as mqtt

from .debug_capture import DebugCaptureStore
from .mower_api import Credentials, EcovacsAuthError, EcovacsMowerApi
from .mower_models import MowerDevice

_LOGGER = logging.getLogger(__name__)
APP_PRESENCE_FEATURE_META = {"fv": "1.0.0", "wv": "v2.1.0"}
APP_PRESENCE_ROLE_META = {"app": "user", "st": 10}
# CONNACK reasons the broker sends once the portal token is no longer valid.
MQTT_AUTH_FAILURES = frozenset({"Bad user name or password", "Not authorized"})
MQTT_RELOGIN_MIN_DELAY_SECONDS = 30
MQTT_RELOGIN_MAX_DELAY_SECONDS = 3600


class MowerMqttClient:
    """Small paho-based MQTT client for ECOVACS push messages."""

    def __init__(
        self,
        api: EcovacsMowerApi,
        device: MowerDevice,
        loop: asyncio.AbstractEventLoop,
        on_message: Callable[[str, bytes], None],
        debug_capture: DebugCaptureStore | None = None,
        on_login_failed: Callable[[EcovacsAuthError], object] | None = None,
    ) -> None:
        self._api = api
        self._device = device
        self._loop = loop
        self._on_message = on_message
        self._debug_capture = debug_capture
        self._on_login_failed = on_login_failed
        self._client: mqtt.Client | None = None
        self._token: str | None = None
        self._relogin_task: asyncio.Task[None] | None = None
        self._relogin_delay = MQTT_RELOGIN_MIN_DELAY_SECONDS

    async def start(self) -> None:
        """Connect and subscribe to mower MQTT push topics."""
        await self._connect(await self._api.authenticate())

    async def _connect(self, credentials: Credentials) -> None:
        client_id = f"{credentials.user_id}@ecouser/{self._api.client_device_id}"
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
        )
        client.username_pw_set(credentials.user_id, credentials.token)

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        client.tls_set_context(ssl_context)

        client.on_connect = self._on_connect
        client.on_message = self._on_paho_message
        client.on_disconnect = self._on_disconnect
        self._client = client
        self._token = credentials.token

        host = f"mq-{self._api.continent}.ecouser.net"
        port = 443
        await self._loop.run_in_executor(None, client.connect, host, port, 60)
        client.loop_start()

    async def stop(self) -> None:
        """Disconnect MQTT and cancel a pending sign-in."""
        client = self._client
        self._client = None
        task = self._relogin_task
        self._relogin_task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if client is not None:
            await self._close(client)

    async def _close(self, client: mqtt.Client) -> None:
        await self._loop.run_in_executor(None, client.disconnect)
        client.loop_stop()

    def _schedule_relogin(self, client: mqtt.Client) -> None:
        """Start one sign-in for a client the broker refused."""
        if client is not self._client:
            return
        if self._relogin_task is not None and not self._relogin_task.done():
            return
        self._relogin_task = self._loop.create_task(
            self._async_relogin(), name="ecovacs_goat_mqtt_relogin"
        )

    async def _async_relogin(self) -> None:
        """Replace the refused client with one that uses a fresh token.

        Waits 30 seconds before the first attempt and doubles the wait after
        every failed one, up to an hour. A successful connect resets it.
        """
        rejected_token = self._token
        while True:
            if self._client is not None:
                client = self._client
                self._client = None
                await self._close(client)
            delay = self._relogin_delay
            self._relogin_delay = min(delay * 2, MQTT_RELOGIN_MAX_DELAY_SECONDS)
            _LOGGER.info("Signing in to ECOVACS MQTT again in %s seconds", delay)
            await asyncio.sleep(delay)
            try:
                credentials = await self._api.authenticate()
                if credentials.token == rejected_token:
                    credentials = await self._api.authenticate(force=True)
                await self._connect(credentials)
            except EcovacsAuthError as err:
                _LOGGER.warning(
                    "ECOVACS MQTT sign-in failed; push updates stop until the "
                    "integration is re-authenticated: %s",
                    err,
                )
                if self._on_login_failed is not None:
                    self._on_login_failed(err)
                return
            except Exception as err:  # noqa: BLE001 - keep retrying until stop()
                _LOGGER.warning("ECOVACS MQTT sign-in failed, will retry: %s", err)
                continue
            return

    def _on_connect(
        self,
        client: mqtt.Client,
        _userdata: Any,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code != 0:
            _LOGGER.warning("ECOVACS MQTT connect failed: %s", reason_code)
            if reason_code.getName() in MQTT_AUTH_FAILURES:
                # paho would keep reconnecting with the token the broker refused.
                client.disconnect()
                self._loop.call_soon_threadsafe(self._schedule_relogin, client)
            return

        self._relogin_delay = MQTT_RELOGIN_MIN_DELAY_SECONDS
        path = f"{self._device.did}/{self._device.device_class}/{self._device.resource}"
        topics = [
            f"iot/atr/+/{path}/j",
        ]
        for topic in topics:
            client.subscribe(topic)
            _LOGGER.debug("Subscribed to ECOVACS MQTT topic %s", topic)

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code != 0:
            _LOGGER.warning("ECOVACS MQTT disconnected: %s", reason_code)

    def _on_paho_message(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        topic = str(message.topic)
        payload = bytes(message.payload)
        _LOGGER.debug("ECOVACS MQTT message topic=%s payload=%s", topic, payload)
        if self._debug_capture is not None:
            self._debug_capture.capture_event(
                "mqtt_message",
                {
                    "topic": topic,
                    "payload_size": len(payload),
                    "payload": payload,
                    "device": {
                        "did": self._device.did,
                        "class": self._device.device_class,
                        "resource": self._device.resource,
                        "model": self._device.model,
                    },
                },
            )
        self._loop.call_soon_threadsafe(self._on_message, topic, payload)


class MowerAppPresenceMqttClient:
    """Short-lived MQTT session that mimics the official app's startup presence.

    The official Android app opens this N-GIoT user MQTT connection before the
    map screen is shown. Keeping a matching session open while the custom card
    is visible may be the cloud-side hint that enables fast position pushes.
    """

    def __init__(
        self,
        api: EcovacsMowerApi,
        device: MowerDevice,
        loop: asyncio.AbstractEventLoop,
        debug_capture: DebugCaptureStore | None = None,
    ) -> None:
        self._api = api
        self._device = device
        self._loop = loop
        self._debug_capture = debug_capture
        self._client: mqtt.Client | None = None
        self._started_at: float | None = None

    @property
    def connected(self) -> bool:
        """Return whether the app-presence client has been started."""
        return self._client is not None

    async def start(self) -> None:
        """Open the app-style N-GIoT user MQTT session if needed."""
        if self._client is not None:
            return

        credentials = await self._api.authenticate()
        realm = _jwt_claim(credentials.token, "r")
        if not realm:
            raise RuntimeError("Could not determine ECOVACS N-GIoT realm from token")

        client_id = f"{credentials.user_id}@USER/{realm}"
        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(_app_presence_username(self._device), credentials.token)

        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        client.tls_set_context(ssl_context)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        self._client = client

        host = f"jmq-ngiot-{self._api.continent}.dc.robotww.ecouser.net"
        port = 443
        _LOGGER.info(
            "Starting ECOVACS app-presence MQTT session host=%s client_id=%s",
            host,
            _redact_client_id(client_id),
        )
        await self._loop.run_in_executor(None, client.connect, host, port, 60)
        client.loop_start()
        self._started_at = monotonic()
        self._capture_event(
            "app_presence_mqtt_start",
            {
                "host": host,
                "port": port,
                "client_id_shape": "<uid>@USER/<realm>",
                "device": {
                    "did": self._device.did,
                    "class": self._device.device_class,
                    "resource": self._device.resource,
                    "model": self._device.model,
                },
            },
        )

    async def stop(self) -> None:
        """Close the app-style N-GIoT user MQTT session."""
        if self._client is None:
            return
        client = self._client
        self._client = None
        started_at = self._started_at
        self._started_at = None
        await self._loop.run_in_executor(None, client.disconnect)
        client.loop_stop()
        _LOGGER.info("Stopped ECOVACS app-presence MQTT session")
        self._capture_event(
            "app_presence_mqtt_stop",
            {
                "duration_seconds": round(monotonic() - started_at, 1)
                if started_at is not None
                else None,
            },
        )

    def _on_connect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code != 0:
            _LOGGER.warning(
                "ECOVACS app-presence MQTT connect failed: %s", reason_code
            )
            self._capture_event(
                "app_presence_mqtt_connect_failed", {"reason_code": str(reason_code)}
            )
            return
        _LOGGER.info("ECOVACS app-presence MQTT connected")
        self._capture_event("app_presence_mqtt_connected", {})

    def _on_disconnect(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        _flags: mqtt.DisconnectFlags,
        reason_code: mqtt.ReasonCode,
        _properties: mqtt.Properties | None,
    ) -> None:
        if reason_code != 0:
            _LOGGER.warning(
                "ECOVACS app-presence MQTT disconnected: %s", reason_code
            )
            self._capture_event(
                "app_presence_mqtt_disconnected", {"reason_code": str(reason_code)}
            )

    def _on_message(
        self,
        _client: mqtt.Client,
        _userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        topic = str(message.topic)
        payload = bytes(message.payload)
        _LOGGER.debug(
            "ECOVACS app-presence MQTT message topic=%s payload_size=%s",
            topic,
            len(payload),
        )
        self._capture_event(
            "app_presence_mqtt_message",
            {
                "topic": topic,
                "payload_size": len(payload),
                "payload": payload,
            },
        )

    def _capture_event(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._debug_capture is not None:
            self._debug_capture.capture_event(event_type, payload)


def _app_presence_username(device: MowerDevice) -> str:
    """Return the N-GIoT username shape captured from the Android app."""
    return (
        f"{device.did}`{_base64_json(APP_PRESENCE_FEATURE_META)}"
        f"\n`{_base64_json(APP_PRESENCE_ROLE_META)}"
    )


def _base64_json(value: dict[str, Any]) -> str:
    return base64.b64encode(
        json.dumps(value, separators=(",", ":")).encode()
    ).decode()


def _jwt_claim(token: str, claim: str) -> str | None:
    """Return a claim from an ECOVACS JWT without verifying the signature."""
    try:
        payload = token.split(".", 2)[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode()).decode())
    except (IndexError, ValueError, json.JSONDecodeError):
        return None
    value = data.get(claim)
    return str(value) if value is not None else None


def _redact_client_id(client_id: str) -> str:
    """Return a log-safe app-presence client id shape."""
    prefix, _, suffix = client_id.partition("@")
    tail = prefix[-4:] if len(prefix) >= 4 else prefix
    return f"<uid:...{tail}>@{suffix}" if suffix else "<redacted>"
