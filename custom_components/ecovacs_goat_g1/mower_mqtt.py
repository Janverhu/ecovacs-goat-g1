"""MQTT push client for ECOVACS GOAT mowers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
import ssl
from typing import Any

import paho.mqtt.client as mqtt

from .mower_api import EcovacsMowerApi
from .mower_models import MowerDevice

_LOGGER = logging.getLogger(__name__)


class MowerMqttClient:
    """Small paho-based MQTT client for ECOVACS push messages."""

    def __init__(
        self,
        api: EcovacsMowerApi,
        device: MowerDevice,
        loop: asyncio.AbstractEventLoop,
        on_message: Callable[[str, bytes], None],
    ) -> None:
        self._api = api
        self._device = device
        self._loop = loop
        self._on_message = on_message
        self._client: mqtt.Client | None = None

    async def start(self) -> None:
        """Connect and subscribe to mower MQTT push topics."""
        credentials = await self._api.authenticate()
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

        host = f"mq-{self._api.continent}.ecouser.net"
        port = 443
        await self._loop.run_in_executor(None, client.connect, host, port, 60)
        client.loop_start()

    async def stop(self) -> None:
        """Disconnect MQTT."""
        if self._client is None:
            return
        client = self._client
        self._client = None
        await self._loop.run_in_executor(None, client.disconnect)
        client.loop_stop()

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
            return

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
        self._loop.call_soon_threadsafe(self._on_message, topic, payload)
