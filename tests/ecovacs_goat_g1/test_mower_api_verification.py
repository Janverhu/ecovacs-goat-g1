"""Tests for ECOVACS client-device verification."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import sys
import time
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, call

from aiohttp import ClientError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

ecovacs_goat_g1 = types.ModuleType("custom_components.ecovacs_goat_g1")
ecovacs_goat_g1.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.ecovacs_goat_g1", ecovacs_goat_g1)

from custom_components.ecovacs_goat_g1.mower_api import (
    AccountSession,
    Credentials,
    EcovacsApiError,
    EcovacsDeviceVerificationRequiredError,
    EcovacsInvalidAuthError,
    EcovacsInvalidVerificationCodeError,
    EcovacsMowerApi,
    PUBLIC_KEY_CONFIG,
)
from custom_components.ecovacs_goat_g1.debug_capture import DebugCaptureStore

ACCOUNT = "test@example.com"
DEVICE_ID = "A1B2C3D4"


def _mock_response(payload: Any) -> Any:
    response = Mock()
    response.raise_for_status = Mock()
    response.json = AsyncMock(return_value=payload)
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(return_value=response)
    context_manager.__aexit__ = AsyncMock(return_value=None)
    return context_manager


def _api(
    session: MagicMock,
    *,
    account_session: AccountSession | None = None,
    account_session_update_callback: AsyncMock | None = None,
) -> EcovacsMowerApi:
    return EcovacsMowerApi(
        session,
        username=ACCOUNT,
        password="password",
        country="RO",
        device_id=DEVICE_ID,
        account_session=account_session,
        account_session_update_callback=account_session_update_callback,
    )


def _public_key_response(private_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    encoded_key = base64.b64encode(
        private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).decode()
    return {
        "code": "0000",
        "data": [
            {
                "key": PUBLIC_KEY_CONFIG,
                "value": f'{{"publicKey":"{encoded_key}"}}',
            }
        ],
    }


def _decrypt(
    private_key: rsa.RSAPrivateKey, params: dict[str, Any], field: str
) -> str:
    return private_key.decrypt(
        base64.b64decode(params[field]), padding.PKCS1v15()
    ).decode()


def test_login_raises_typed_device_verification_error() -> None:
    """Code 1013 must start verification instead of reporting a bad password."""
    session = MagicMock()
    session.get.return_value = _mock_response(
        {
            "code": "1013",
            "msg": "Please update to the latest version to continue.",
            "data": None,
        }
    )

    async def run() -> None:
        try:
            await _api(session).authenticate()
        except EcovacsDeviceVerificationRequiredError:
            pass
        else:
            raise AssertionError("code 1013 did not raise the typed error")

    asyncio.run(run())
    url = session.get.call_args.args[0]
    assert f"/{DEVICE_ID}/global_e/1.6.3/google_play/1/user/login" in url


def test_request_code_encrypts_email_and_uses_verification_api() -> None:
    """The email is RSA encrypted and the stable id signs every request."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    session = MagicMock()
    session.get.side_effect = [
        _mock_response(_public_key_response(private_key)),
        _mock_response({"code": "0000", "data": {"verifyId": "verify-id"}}),
    ]

    asyncio.run(_api(session).request_device_verification_code())

    public_key_call, send_code_call = session.get.call_args_list
    expected_base = f"/v1/private/ro/EN/{DEVICE_ID}/global_e/3.14.0/google_play/1"
    assert public_key_call.args[0].endswith(
        f"{expected_base}/common/getConfig"
    )
    assert send_code_call.args[0].endswith(
        f"{expected_base}/user/sendEmailVerifyCode"
    )
    send_params = send_code_call.kwargs["params"]
    assert _decrypt(private_key, send_params, "encryptEmail") == ACCOUNT
    assert send_params["verifyType"] == "EMAIL_VERIFY_DEVICE"
    assert send_params["supportChar"] == "N"
    assert send_params["isForce"] == "N"
    assert send_params["authAppkey"] == "1520391301804"
    assert send_params["authSign"]


def test_verify_device_completes_login_and_caches_credentials() -> None:
    """A valid OTP verifies the same id and yields reusable credentials."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    session = MagicMock()
    session.get.side_effect = [
        _mock_response(_public_key_response(private_key)),
        _mock_response({"code": "0000", "data": {"verifyId": "verify-id"}}),
        _mock_response(
            {
                "code": "0000",
                "data": {"uid": "user-id", "accessToken": "access-token"},
            }
        ),
        _mock_response({"code": "0000", "data": {"authCode": "auth-code"}}),
    ]
    session.post.return_value = _mock_response(
        {
            "result": "ok",
            "userId": "user-id",
            "token": "portal-token",
            "last": 604800000,
        }
    )
    api = _api(session)

    async def run() -> None:
        await api.request_device_verification_code()
        credentials = await api.verify_device(" 123456 ")
        assert credentials.token == "portal-token"
        assert credentials.user_id == "user-id"
        assert credentials.expires_at > time.time()
        assert await api.authenticate() == credentials

    asyncio.run(run())

    verify_call = session.get.call_args_list[2]
    verify_params = verify_call.kwargs["params"]
    assert _decrypt(private_key, verify_params, "encryptAccount") == ACCOUNT
    assert verify_params["verifyCode"] == "123456"
    assert verify_params["model"] == "Pixel 7"
    assert verify_params["system"] == "Android 14"

    portal_call = session.post.call_args
    assert portal_call.kwargs["json"]["resource"] == DEVICE_ID
    assert portal_call.kwargs["json"]["token"] == "auth-code"
    assert session.get.call_count == 4


def test_fresh_api_reuses_persisted_session_and_gets_devices_without_password() -> None:
    """A process restart can reach GOAT discovery without password login."""
    persisted = AccountSession(
        user_id="sentinel-old-user",
        access_token="sentinel-old-access-token",
    )
    rotated = AccountSession(
        user_id="sentinel-rotated-user",
        access_token="sentinel-rotated-access-token",
    )
    session = MagicMock()
    session.get.side_effect = [
        _mock_response(
            {
                "code": "0000",
                "data": {
                    "uid": rotated.user_id,
                    "accessToken": rotated.access_token,
                },
            }
        ),
        _mock_response(
            {"code": "0000", "data": {"authCode": "sentinel-auth-code"}}
        ),
    ]
    session.post.side_effect = [
        _mock_response(
            {
                "result": "ok",
                "userId": rotated.user_id,
                "token": "sentinel-portal-token",
                "last": 604800000,
            }
        ),
        _mock_response(
            {
                "devices": [
                    {
                        "did": "sentinel-did",
                        "class": "sentinel-class",
                        "resource": "sentinel-resource",
                        "name": "Sentinel GOAT",
                        "company": "eco-ng",
                    }
                ]
            }
        ),
        _mock_response({"devices": []}),
    ]
    callback = AsyncMock()
    api = _api(
        session,
        account_session=persisted,
        account_session_update_callback=callback,
    )

    devices = asyncio.run(api.get_devices())

    assert [device.did for device in devices] == ["sentinel-did"]
    assert api.account_session == rotated
    callback.assert_awaited_once_with(rotated)
    requested_urls = [item.args[0] for item in session.get.call_args_list]
    assert any("/v2/private/" in url and url.endswith("/user/checkLogin") for url in requested_urls)
    assert all(not url.endswith("/user/login") for url in requested_urls)
    check_login_params = session.get.call_args_list[0].kwargs["params"]
    assert check_login_params["uid"] == persisted.user_id
    assert check_login_params["accessToken"] == persisted.access_token


def test_rotated_session_is_persisted_before_portal_exchange() -> None:
    """checkLogin rotation reaches the persistence callback immediately."""
    old_session = AccountSession("sentinel-old-user", "sentinel-old-token")
    rotated_session = AccountSession(
        "sentinel-rotated-user", "sentinel-rotated-token"
    )
    callback = AsyncMock()
    api = _api(
        MagicMock(),
        account_session=old_session,
        account_session_update_callback=callback,
    )
    api._check_login = AsyncMock(return_value=rotated_session)
    api._complete_login = AsyncMock(
        return_value=Credentials(
            user_id=rotated_session.user_id,
            token="sentinel-portal-token",
            expires_at=time.time() + 3600,
        )
    )
    api._login_password = AsyncMock()

    credentials = asyncio.run(api.authenticate())

    assert credentials.user_id == rotated_session.user_id
    callback.assert_awaited_once_with(rotated_session)
    api._complete_login.assert_awaited_once_with(
        rotated_session.user_id, rotated_session.access_token
    )
    api._login_password.assert_not_awaited()


def test_invalid_saved_session_falls_back_once_and_replaces_it() -> None:
    """Only a typed invalid session permits one password fallback."""
    old_session = AccountSession("sentinel-old-user", "sentinel-old-token")
    replacement = AccountSession(
        "sentinel-replacement-user", "sentinel-replacement-token"
    )
    callback = AsyncMock()
    api = _api(
        MagicMock(),
        account_session=old_session,
        account_session_update_callback=callback,
    )
    api._check_login = AsyncMock(side_effect=EcovacsInvalidAuthError())
    api._login_password = AsyncMock(
        return_value={
            "uid": replacement.user_id,
            "accessToken": replacement.access_token,
        }
    )
    api._complete_login = AsyncMock(
        return_value=Credentials(
            user_id=replacement.user_id,
            token="sentinel-portal-token",
            expires_at=time.time() + 3600,
        )
    )

    asyncio.run(api.authenticate())

    assert callback.await_args_list == [call(None), call(replacement)]
    api._login_password.assert_awaited_once_with()
    assert api.account_session == replacement


def test_invalid_saved_session_then_1013_stays_cleared() -> None:
    """A definitively invalid session is not restored after password challenge."""
    old_session = AccountSession("sentinel-old-user", "sentinel-old-token")
    callback = AsyncMock()
    api = _api(
        MagicMock(),
        account_session=old_session,
        account_session_update_callback=callback,
    )
    api._check_login = AsyncMock(side_effect=EcovacsInvalidAuthError())
    api._login_password = AsyncMock(
        side_effect=EcovacsDeviceVerificationRequiredError()
    )

    async def run() -> None:
        try:
            await api.authenticate()
        except EcovacsDeviceVerificationRequiredError:
            pass
        else:
            raise AssertionError("password challenge was accepted")

    asyncio.run(run())

    callback.assert_awaited_once_with(None)
    assert api.account_session is None
    api._login_password.assert_awaited_once_with()


def test_definitive_password_portal_failure_clears_early_raw_session() -> None:
    """Typed invalid/1013 portal failures cannot leave an unusable raw session."""
    acquired = AccountSession("sentinel-user", "sentinel-access-token")

    for failure in (
        EcovacsInvalidAuthError(),
        EcovacsDeviceVerificationRequiredError(),
    ):
        callback = AsyncMock()
        api = _api(
            MagicMock(), account_session_update_callback=callback
        )
        api._login_password = AsyncMock(
            return_value={
                "uid": acquired.user_id,
                "accessToken": acquired.access_token,
            }
        )
        api._complete_login = AsyncMock(side_effect=failure)

        async def run() -> None:
            try:
                await api.authenticate()
            except type(failure):
                pass
            else:
                raise AssertionError("definitive portal failure was accepted")

        asyncio.run(run())

        assert callback.await_args_list == [call(acquired), call(None)]
        assert api.account_session is None


def test_transient_password_portal_failure_retains_early_raw_session() -> None:
    """A retryable portal outage keeps the password-acquired raw session."""
    acquired = AccountSession("sentinel-user", "sentinel-access-token")
    callback = AsyncMock()
    api = _api(MagicMock(), account_session_update_callback=callback)
    api._login_password = AsyncMock(
        return_value={
            "uid": acquired.user_id,
            "accessToken": acquired.access_token,
        }
    )
    api._complete_login = AsyncMock(side_effect=EcovacsApiError("transient"))

    async def run() -> None:
        try:
            await api.authenticate()
        except EcovacsApiError:
            pass
        else:
            raise AssertionError("transient portal failure was accepted")

    asyncio.run(run())

    callback.assert_awaited_once_with(acquired)
    assert api.account_session == acquired


def test_transient_saved_session_failure_never_falls_back_to_password() -> None:
    """Transport/server failures retain the raw session and avoid password auth."""
    persisted = AccountSession("sentinel-user", "sentinel-access-token")
    callback = AsyncMock()
    api = _api(
        MagicMock(),
        account_session=persisted,
        account_session_update_callback=callback,
    )
    api._check_login = AsyncMock(side_effect=EcovacsApiError("transient"))
    api._login_password = AsyncMock()

    async def run() -> None:
        try:
            await api.authenticate()
        except EcovacsApiError:
            pass
        else:
            raise AssertionError("transient failure was accepted")

    asyncio.run(run())

    assert api.account_session == persisted
    callback.assert_not_awaited()
    api._login_password.assert_not_awaited()


def test_rotated_session_survives_transient_portal_exchange_failure() -> None:
    """A rotated raw session remains usable when the portal is temporarily down."""
    persisted = AccountSession("sentinel-old-user", "sentinel-old-token")
    rotated = AccountSession("sentinel-new-user", "sentinel-new-token")
    callback = AsyncMock()
    api = _api(
        MagicMock(),
        account_session=persisted,
        account_session_update_callback=callback,
    )
    api._check_login = AsyncMock(return_value=rotated)
    api._complete_login = AsyncMock(side_effect=EcovacsApiError("transient"))
    api._login_password = AsyncMock()

    async def run() -> None:
        try:
            await api.authenticate()
        except EcovacsApiError:
            pass
        else:
            raise AssertionError("transient portal failure was accepted")

    asyncio.run(run())

    assert api.account_session == rotated
    callback.assert_awaited_once_with(rotated)
    api._login_password.assert_not_awaited()


def test_force_refresh_reuses_raw_session_without_password() -> None:
    """Runtime expiry/force refresh exchanges the saved raw session again."""
    persisted = AccountSession("sentinel-user", "sentinel-access-token")
    api = _api(MagicMock(), account_session=persisted)
    api._check_login = AsyncMock(return_value=persisted)
    api._complete_login = AsyncMock(
        side_effect=[
            Credentials("sentinel-user", "sentinel-portal-one", time.time() + 3600),
            Credentials("sentinel-user", "sentinel-portal-two", time.time() + 3600),
        ]
    )
    api._login_password = AsyncMock()

    first = asyncio.run(api.authenticate())
    cached = asyncio.run(api.authenticate())
    refreshed = asyncio.run(api.authenticate(force=True))

    assert cached is first
    assert refreshed.token == "sentinel-portal-two"
    assert api._check_login.await_count == 2
    assert api._complete_login.await_count == 2
    api._login_password.assert_not_awaited()


def test_verify_device_persists_session_before_portal_exchange() -> None:
    """A consumed OTP remains recoverable if the portal is temporarily down."""
    verified = AccountSession("sentinel-user", "sentinel-access-token")
    callback = AsyncMock()
    api = _api(MagicMock(), account_session_update_callback=callback)
    api._encrypt_account = AsyncMock(return_value="sentinel-encrypted-account")
    api._call_private_api = AsyncMock(
        return_value={
            "uid": verified.user_id,
            "accessToken": verified.access_token,
        }
    )
    api._complete_login = AsyncMock(side_effect=EcovacsApiError("transient"))

    async def fail_once() -> None:
        try:
            await api.verify_device("sentinel-otp")
        except EcovacsApiError:
            pass
        else:
            raise AssertionError("failed portal exchange was accepted")

    asyncio.run(fail_once())
    assert api.account_session == verified
    callback.assert_awaited_once_with(verified)

    credentials = Credentials(
        "sentinel-user", "sentinel-portal-token", time.time() + 3600
    )
    api._check_login = AsyncMock(return_value=verified)
    api._complete_login = AsyncMock(return_value=credentials)
    assert asyncio.run(api.authenticate()) == credentials
    assert api.account_session == verified
    assert callback.await_args_list == [call(verified), call(verified)]


def test_definitive_verify_portal_failure_clears_verified_session() -> None:
    """Typed invalid/1013 exchange responses invalidate verifyDevice output."""
    verified = AccountSession("sentinel-user", "sentinel-access-token")

    for failure in (
        EcovacsInvalidAuthError(),
        EcovacsDeviceVerificationRequiredError(),
    ):
        callback = AsyncMock()
        api = _api(
            MagicMock(), account_session_update_callback=callback
        )
        api._encrypt_account = AsyncMock(
            return_value="sentinel-encrypted-account"
        )
        api._call_private_api = AsyncMock(
            return_value={
                "uid": verified.user_id,
                "accessToken": verified.access_token,
            }
        )
        api._complete_login = AsyncMock(side_effect=failure)

        async def run() -> None:
            try:
                await api.verify_device("sentinel-otp")
            except type(failure):
                pass
            else:
                raise AssertionError("definitive verify failure was accepted")

        asyncio.run(run())

        assert callback.await_args_list == [call(verified), call(None)]
        assert api.account_session is None


def test_account_session_repr_never_contains_raw_values() -> None:
    """Accidental logging of the value object cannot disclose its fields."""
    session = AccountSession("sentinel-user-id", "sentinel-access-token")

    rendered = repr(session)

    assert "sentinel-user-id" not in rendered
    assert "sentinel-access-token" not in rendered


def test_debug_capture_redacts_session_keys_values_and_manifest(
    tmp_path: Path,
) -> None:
    """Neither JSONL nor exported manifest metadata may disclose a session."""
    user_id = "sentinel-private-user-id"
    access_token = "sentinel-private-access-token"
    capture = DebugCaptureStore(
        tmp_path / "captures",
        tmp_path / "exports",
    )
    capture.add_redaction_value(user_id)
    capture.add_redaction_value(access_token)

    capture.start(
        reason=f"diagnose {access_token}",
        include_raw_payloads=True,
    )
    capture.capture_event(
        "auth_probe",
        {
            "uid": user_id,
            "accessToken": access_token,
            "nested": {
                "message": f"user={user_id}; token={access_token}",
            },
        },
    )
    capture.stop()

    session_path = next((tmp_path / "captures").iterdir())
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(session_path.iterdir())
        if path.is_file()
    )
    assert user_id not in persisted
    assert access_token not in persisted
    assert "<redacted>" in persisted


def test_debug_capture_key_redaction_is_recursive(tmp_path: Path) -> None:
    """Known raw-session key spellings are redacted at every nesting level."""
    capture = DebugCaptureStore(
        tmp_path / "captures",
        tmp_path / "exports",
    )
    capture.start(include_raw_payloads=True)
    capture.capture_event(
        "auth_probe",
        {
            "outer": [
                {
                    "access_token": "sentinel-access-token",
                    "user_id": "sentinel-user-id",
                }
            ]
        },
    )
    capture.stop()

    rendered = json.dumps(capture.recent_events())
    assert "sentinel-access-token" not in rendered
    assert "sentinel-user-id" not in rendered
    assert rendered.count("<redacted>") >= 2


def test_invalid_verification_code_is_typed_and_not_logged_in_error() -> None:
    """Code 1012 stays distinct and the exception never echoes the OTP."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    session = MagicMock()
    session.get.side_effect = [
        _mock_response(_public_key_response(private_key)),
        _mock_response(
            {
                "code": "1012",
                "msg": "Incorrect verification code.",
                "data": None,
            }
        ),
    ]

    async def run() -> None:
        try:
            await _api(session).verify_device("secret-otp")
        except EcovacsInvalidVerificationCodeError as err:
            assert "secret-otp" not in str(err)
        else:
            raise AssertionError("code 1012 did not raise the typed error")

    asyncio.run(run())


def test_unknown_auth_error_does_not_echo_response_data() -> None:
    """Access tokens in error payloads are never copied into exceptions."""
    session = MagicMock()
    session.get.return_value = _mock_response(
        {
            "code": "9999",
            "msg": "Request rejected",
            "data": {"accessToken": "must-not-leak"},
        }
    )

    async def run() -> None:
        try:
            await _api(session).authenticate()
        except EcovacsApiError as err:
            assert "must-not-leak" not in str(err)
        else:
            raise AssertionError("unknown auth failure was accepted")

    asyncio.run(run())


def test_transport_error_suppresses_sensitive_request_context() -> None:
    """A logged transport failure cannot reveal query-string OTPs or tokens."""
    session = MagicMock()
    context_manager = MagicMock()
    context_manager.__aenter__ = AsyncMock(
        side_effect=ClientError("https://example.test/?verifyCode=secret-otp")
    )
    context_manager.__aexit__ = AsyncMock(return_value=None)
    session.get.return_value = context_manager

    async def run() -> None:
        try:
            await _api(session).verify_device("secret-otp")
        except EcovacsApiError as err:
            assert "secret-otp" not in str(err)
            assert err.__suppress_context__ is True
        else:
            raise AssertionError("transport failure was accepted")

    asyncio.run(run())
