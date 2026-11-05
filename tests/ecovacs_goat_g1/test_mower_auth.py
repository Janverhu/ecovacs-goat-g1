"""Tests for ECOVACS device-verification authentication."""

from __future__ import annotations

from pathlib import Path
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

ecovacs_goat_g1 = types.ModuleType("custom_components.ecovacs_goat_g1")
ecovacs_goat_g1.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.ecovacs_goat_g1", ecovacs_goat_g1)

from custom_components.ecovacs_goat_g1.mower_api import (
    META,
    PRIVATE_API_PATH_FORMAT,
    AccountSession,
    Credentials,
    DeviceVerificationRequiredError,
    EcovacsAuthError,
    EcovacsMowerApi,
    InvalidVerificationCodeError,
    _load_public_key,
)
from custom_components.ecovacs_goat_g1.util import (
    account_fingerprint,
    generate_client_device_id,
    generate_session_store_id,
    get_client_device_id,
    get_session_store_id,
    is_valid_session_store_id,
)


def test_app_version_is_current() -> None:
    """Password login must advertise a current ECOVACS HOME version."""
    assert META["appVersion"] == "3.14.0"
    login_path = PRIVATE_API_PATH_FORMAT.format(
        apiVersion="v1",
        country="us",
        lang="EN",
        deviceId="ABCDEF12",
        appCode="global_e",
        appVersion=META["appVersion"],
        channel="google_play",
        deviceType="1",
        endpoint="user/login",
    )
    assert "/v1/private/us/" in login_path
    assert "/global_e/3.14.0/google_play/1/user/login" in login_path
    assert "1.6.3" not in login_path


def test_check_login_uses_signed_v2_path() -> None:
    """Session rotation must use the signed v2 checkLogin endpoint."""
    check_login_path = PRIVATE_API_PATH_FORMAT.format(
        apiVersion="v2",
        country="us",
        lang="EN",
        deviceId="ABCDEF12",
        appCode="global_e",
        appVersion=META["appVersion"],
        channel="google_play",
        deviceType="1",
        endpoint="user/checkLogin",
    )
    assert "/v2/private/us/" in check_login_path
    assert check_login_path.endswith("/user/checkLogin")


def test_client_device_id_is_stable_when_persisted() -> None:
    """A stored device id is reused; a missing one is generated once."""
    assert get_client_device_id({"device_id": "ABCDEF12"}) == "ABCDEF12"
    first = generate_client_device_id()
    second = generate_client_device_id()
    assert len(first) == 8
    assert first.isalnum()
    assert first.isupper() or any(ch.isdigit() for ch in first)
    assert first != second


def test_session_store_id_is_opaque_and_stable() -> None:
    """Private-store ids are hex, 32 chars, and reused when already valid."""
    stored = "a" * 32
    assert is_valid_session_store_id(stored)
    assert get_session_store_id({"session_store_id": stored}) == stored
    assert not is_valid_session_store_id("ABCDEF12")
    assert not is_valid_session_store_id("../etc")
    first = generate_session_store_id()
    second = generate_session_store_id()
    assert is_valid_session_store_id(first)
    assert first != second


def test_account_fingerprint_binds_identity_not_secrets() -> None:
    """Fingerprints match case-folded accounts and stay non-reversible."""
    first = account_fingerprint("User@example.com", "us", "ABCDEF12")
    second = account_fingerprint("user@example.com", "US", "ABCDEF12")
    other = account_fingerprint("other@example.com", "US", "ABCDEF12")
    assert first == second
    assert first != other
    assert "user@example.com" not in first
    assert len(first) == 64


def _api(**kwargs) -> EcovacsMowerApi:
    return EcovacsMowerApi(
        MagicMock(),
        username="user@example.com",
        password="secret",
        country="US",
        device_id="ABCDEF12",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_auth_error_codes() -> None:
    """Map ECOVACS auth codes to the matching exception types."""
    api = _api()
    api._session.get = MagicMock()

    async def _failed(code: str):
        response = AsyncMock()
        response.raise_for_status = MagicMock()
        response.json = AsyncMock(
            return_value={"code": code, "msg": "nope", "data": None, "success": False}
        )
        response.__aenter__.return_value = response
        response.__aexit__.return_value = False
        api._session.get.return_value = response
        return await api._signed_get("https://example.test", {}, {}, "k", "s")

    with pytest.raises(EcovacsAuthError, match="invalid credentials"):
        await _failed("1005")
    with pytest.raises(InvalidVerificationCodeError):
        await _failed("1012")
    with pytest.raises(DeviceVerificationRequiredError):
        await _failed("1013")


@pytest.mark.asyncio
async def test_authenticate_rotates_stored_session_via_check_login() -> None:
    """Setup after verification must reuse checkLogin instead of password login."""
    persisted = AsyncMock()
    api = _api(
        account_session=AccountSession(user_id="uid-long", access_token="access-1"),
        account_session_update_callback=persisted,
    )
    api._login_password = AsyncMock(side_effect=AssertionError("password login"))
    api._check_login = AsyncMock(
        return_value=AccountSession(user_id="uid-long", access_token="access-2")
    )
    api._complete_login = AsyncMock(
        return_value=Credentials(
            user_id="uid-short", token="portal-token", expires_at=9_999_999_999
        )
    )

    credentials = await api.authenticate()

    api._login_password.assert_not_called()
    api._check_login.assert_awaited_once()
    api._complete_login.assert_awaited_once_with("uid-long", "access-2")
    persisted.assert_awaited_once_with(
        AccountSession(user_id="uid-long", access_token="access-2")
    )
    assert credentials.token == "portal-token"
    assert api.account_session == AccountSession("uid-long", "access-2")


@pytest.mark.asyncio
async def test_authenticate_keeps_session_on_transient_check_login_error() -> None:
    """A non-credential checkLogin failure must not be retried or password-login."""
    persisted = AsyncMock()
    existing = AccountSession(user_id="uid-long", access_token="access-1")
    api = _api(
        account_session=existing,
        account_session_update_callback=persisted,
    )
    api._check_login = AsyncMock(side_effect=EcovacsAuthError("auth call failed"))
    api._login_password = AsyncMock(side_effect=AssertionError("password login"))

    api._sst_token = AsyncMock(side_effect=AssertionError("mower command"))

    with pytest.raises(EcovacsAuthError, match="auth call failed"):
        await api.authenticate()
    with pytest.raises(EcovacsAuthError, match="reauthentication required"):
        await api.authenticate()
    with pytest.raises(EcovacsAuthError, match="reauthentication required"):
        await api.control(MagicMock(), "appping", {})

    api._login_password.assert_not_called()
    api._sst_token.assert_not_called()
    assert api._check_login.await_count == 1
    persisted.assert_not_called()
    assert api.account_session == existing
    assert api.reauth_required


@pytest.mark.asyncio
async def test_unattended_login_failure_does_not_retry() -> None:
    """A rejected stored session must not password-login or try checkLogin again."""
    existing = AccountSession(user_id="uid-long", access_token="access-1")
    api = _api(account_session=existing)
    api._check_login = AsyncMock(side_effect=EcovacsAuthError("invalid credentials"))
    api._login_password = AsyncMock(side_effect=AssertionError("password login"))
    api._sst_token = AsyncMock(side_effect=AssertionError("mower command"))

    with pytest.raises(EcovacsAuthError, match="invalid credentials"):
        await api.authenticate()
    with pytest.raises(EcovacsAuthError, match="reauthentication required"):
        await api.authenticate()
    with pytest.raises(EcovacsAuthError, match="reauthentication required"):
        await api.control(MagicMock(), "getInfo", ["getBattery"])

    api._login_password.assert_not_called()
    api._sst_token.assert_not_called()
    assert api._check_login.await_count == 1
    assert api.account_session == existing
    assert api.reauth_required


@pytest.mark.asyncio
async def test_missing_session_does_not_password_login() -> None:
    """Startup without a stored session must ask for a sign-in, not log in itself."""
    api = _api()
    api._login_password = AsyncMock(side_effect=AssertionError("password login"))

    with pytest.raises(EcovacsAuthError, match="reauthentication required"):
        await api.authenticate()
    with pytest.raises(EcovacsAuthError, match="reauthentication required"):
        await api.authenticate()

    api._login_password.assert_not_called()
    assert api.reauth_required


@pytest.mark.asyncio
async def test_reauth_form_password_login_after_other_check_login_error() -> None:
    """The reauth form must not get stuck on a stored session checkLogin rejects."""
    api = _api(
        account_session=AccountSession(user_id="uid-long", access_token="access-1"),
        allow_password_login=True,
    )
    api._check_login = AsyncMock(side_effect=EcovacsAuthError("auth call failed"))
    api._login_password = AsyncMock(
        return_value={"uid": "uid-long", "accessToken": "access-2"}
    )
    api._complete_login = AsyncMock(
        return_value=Credentials(
            user_id="uid-short", token="portal-token", expires_at=9_999_999_999
        )
    )

    credentials = await api.authenticate()

    api._login_password.assert_awaited_once()
    assert credentials.token == "portal-token"
    assert api.account_session == AccountSession("uid-long", "access-2")
    assert not api.reauth_required


@pytest.mark.asyncio
async def test_authenticate_clears_session_on_definitive_auth_failure() -> None:
    """A user-submitted login drops a dead session, then uses the password."""
    persisted = AsyncMock()
    api = _api(
        account_session=AccountSession(user_id="uid-long", access_token="access-1"),
        account_session_update_callback=persisted,
        allow_password_login=True,
    )
    api._check_login = AsyncMock(side_effect=EcovacsAuthError("invalid credentials"))
    api._login_password = AsyncMock(side_effect=DeviceVerificationRequiredError("1013"))

    with pytest.raises(DeviceVerificationRequiredError):
        await api.authenticate()

    persisted.assert_awaited_once_with(None)
    api._login_password.assert_awaited_once()
    assert api.account_session is None


def test_load_public_key_roundtrip() -> None:
    """The ECOVACS getConfig public-key blob loads as an RSA key."""
    import base64
    import json

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    loaded = _load_public_key(json.dumps({"publicKey": base64.b64encode(der).decode()}))
    assert loaded.key_size == 2048
