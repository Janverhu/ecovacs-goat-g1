"""App-style ECOVACS GOAT mower cloud API."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import time
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .debug_capture import DebugCaptureStore
from .mower_models import MowerDevice

REALM = "ecouser.net"
COUNTRY_CHINA = "CN"
PATH_API_USERS_USER = "users/user.do"
PATH_API_APPSVR_APP = "appsvr/app.do"

CLIENT_KEY = "1520391301804"
# Vendor app OAuth client secret (embedded in official Ecovacs app; not a user credential).
CLIENT_SECRET = "6c319b2a5cd3e66e39159c2e28f2fce9"  # nosemgrep
AUTH_CLIENT_KEY = "1520391491841"
# Same as above for the auth-code client.
AUTH_CLIENT_SECRET = "77ef58ce3afbe337da74aa8c5ab963a9"  # nosemgrep
GLOBAL_AUTHCODE_PATH = "/v1/global/auth/getAuthCode"
PRIVATE_API_PATH_FORMAT = (
    "/v1/private/{country}/{lang}/{deviceId}/{appCode}/{appVersion}/"
    "{channel}/{deviceType}/{endpoint}"
)
PUBLIC_KEY_CONFIG = "PUBLIC.KEY.CONFIG"
ANDROID_MODEL = "Pixel 7"
ANDROID_SYSTEM = "Android 14"
META = {
    "lang": "EN",
    "appCode": "global_e",
    "appVersion": "3.14.0",
    "channel": "google_play",
    "deviceType": "1",
}
TIMEOUT = ClientTimeout(total=60)
EU_COUNTRIES = {
    "AD",
    "AL",
    "AT",
    "AX",
    "BA",
    "BE",
    "BG",
    "BY",
    "CH",
    "CY",
    "CZ",
    "DE",
    "DK",
    "EE",
    "ES",
    "FI",
    "FO",
    "FR",
    "GB",
    "GG",
    "GI",
    "GR",
    "HR",
    "HU",
    "IE",
    "IM",
    "IS",
    "IT",
    "JE",
    "LI",
    "LT",
    "LU",
    "LV",
    "MC",
    "MD",
    "ME",
    "MK",
    "MT",
    "NL",
    "NO",
    "PL",
    "PT",
    "RO",
    "RS",
    "SE",
    "SI",
    "SK",
    "SM",
    "UA",
    "VA",
}


class EcovacsApiError(Exception):
    """Base ECOVACS API error."""


class EcovacsAuthError(EcovacsApiError):
    """Authentication failed."""


class EcovacsDeviceVerificationRequiredError(EcovacsAuthError):
    """Device verification (one-time email code) is required before login."""


class EcovacsInvalidVerificationCodeError(EcovacsAuthError):
    """The supplied device-verification code was invalid or expired."""


@dataclass(frozen=True)
class Credentials:
    """ECOVACS credentials."""

    user_id: str
    token: str
    expires_at: float


@dataclass(frozen=True)
class SstToken:
    """Short-lived N-GIoT control token."""

    token: str
    expires_at: float


class EcovacsMowerApi:
    """Small ECOVACS API client for app-captured mower calls."""

    def __init__(
        self,
        session: ClientSession,
        *,
        username: str,
        password: str,
        country: str,
        device_id: str,
        debug_capture: DebugCaptureStore | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password_hash = md5(password)
        self._country = country.upper()
        self._device_id = device_id
        self._continent = country_continent(self._country)
        self._credentials: Credentials | None = None
        self._sst: dict[str, SstToken] = {}
        self._debug_capture = debug_capture
        self._public_key: rsa.RSAPublicKey | None = None

        postfix = "" if self._country == COUNTRY_CHINA else f"-{self._continent}"
        country_lower = self._country.lower()
        api_country = country_api_code(self._country)
        tld = "com" if self._country != COUNTRY_CHINA else country_lower
        self._portal_url = f"https://portal{postfix}.ecouser.net"
        self._login_url = f"https://gl-{api_country}-api.ecovacs.{tld}"
        self._auth_code_url = f"https://gl-{api_country}-openapi.ecovacs.{tld}"

    @property
    def continent(self) -> str:
        """Return ECOVACS continent key."""
        return self._continent

    @property
    def client_device_id(self) -> str:
        """Return the client resource used for account login."""
        return self._device_id

    async def authenticate(self, *, force: bool = False) -> Credentials:
        """Authenticate and cache ECOVACS account credentials."""
        if (
            self._credentials is None
            or force
            or self._credentials.expires_at < time.time()
        ):
            login_resp = _expect_dict(await self._login_password(), "Invalid login response")
            self._credentials = await self._complete_login(login_resp)
        return self._credentials

    async def request_device_verification_code(self) -> None:
        """Request a one-time email code to verify this integration's device id."""
        encrypted_email = await self._encrypt_account(self._username)
        await self._call_private_api(
            "user/sendEmailVerifyCode",
            {
                "encryptEmail": encrypted_email,
                "verifyType": "EMAIL_VERIFY_DEVICE",
                "supportChar": "N",
                "isForce": "N",
            },
        )

    async def verify_device(self, verification_code: str) -> Credentials:
        """Verify this integration's device id using a one-time email code."""
        encrypted_account = await self._encrypt_account(self._username)
        response = await self._call_private_api(
            "user/verifyDevice",
            {
                "encryptAccount": encrypted_account,
                "backUpEmail": "",
                "verifyCode": verification_code.strip(),
                "model": ANDROID_MODEL,
                "system": ANDROID_SYSTEM,
            },
        )
        login_resp = _expect_dict(response, "Invalid verifyDevice response")
        self._credentials = await self._complete_login(login_resp)
        return self._credentials

    async def _complete_login(self, login_resp: dict[str, Any]) -> Credentials:
        """Finish the login sequence given a user/login-shaped response."""
        user_id = login_resp["uid"]
        auth_code = await self._auth_code(login_resp["accessToken"], user_id)
        token_resp = await self._login_by_it_token(user_id, auth_code)
        if token_resp["userId"] != user_id:
            user_id = token_resp["userId"]
        expires_at = time.time() + int(token_resp.get("last", 604800)) / 1000 * 0.99
        return Credentials(
            user_id=user_id,
            token=token_resp["token"],
            expires_at=expires_at,
        )

    async def _get_public_key(self) -> rsa.RSAPublicKey:
        """Fetch (and cache) the RSA public key used to encrypt the account id."""
        if self._public_key is not None:
            return self._public_key

        response = await self._call_private_api(
            "common/getConfig", {"keys": PUBLIC_KEY_CONFIG}
        )
        if not isinstance(response, list):
            raise EcovacsAuthError("Invalid public key configuration response")

        for entry in response:
            if (
                isinstance(entry, dict)
                and entry.get("key") == PUBLIC_KEY_CONFIG
                and isinstance(entry.get("value"), str)
            ):
                self._public_key = _load_public_key(entry["value"])
                return self._public_key

        raise EcovacsAuthError("Ecovacs public key configuration is missing")

    async def _encrypt_account(self, account: str) -> str:
        public_key = await self._get_public_key()
        encrypted = public_key.encrypt(account.encode(), padding.PKCS1v15())
        return base64.b64encode(encrypted).decode()

    async def get_devices(self) -> list[MowerDevice]:
        """Return mower-like eco-ng devices from the account."""
        devices: dict[str, dict[str, Any]] = {}
        for path, todo in (
            (PATH_API_USERS_USER, "GetDeviceList"),
            (PATH_API_APPSVR_APP, "GetGlobalDeviceList"),
        ):
            response = await self._post_authenticated(
                path,
                {"userid": (await self.authenticate()).user_id, "todo": todo},
            )
            for device in response.get("devices", []):
                devices[device["did"]] = device

        return [
            MowerDevice.from_api(device)
            for device in devices.values()
            if device.get("company") == "eco-ng"
        ]

    async def control(
        self,
        device: MowerDevice,
        command: str,
        data: Any | None = None,
    ) -> dict[str, Any]:
        """Execute an N-GIoT app-style command against the mower."""
        if data is None:
            data = {}
        sst = await self._sst_token(device)
        request_id = uuid4().hex
        payload = app_payload(data)
        url = f"https://api-ngiot.dc-{self._continent}.ww.ecouser.net/api/iot/endpoint/control"
        params = {
            "si": request_id,
            "ct": "q",
            "eid": device.did,
            "et": device.device_class,
            "er": device.resource,
            "apn": command,
            "fmt": "j",
        }
        headers = {
            "authorization": f"Bearer {sst.token}",
            "x-eco-request-id": request_id,
            "content-type": "application/octet-stream",
            "user-agent": "okhttp/4.9.1",
        }
        started = time.monotonic()
        self._capture_control_event(
            "api_control_request",
            device,
            command,
            {
                "request_id": request_id,
                "params": params,
                "request": payload,
            },
        )
        try:
            async with self._session.post(
                url,
                params=params,
                json=payload,
                headers=headers,
                timeout=TIMEOUT,
            ) as response:
                response.raise_for_status()
                result = await response.json(content_type=None)
                try:
                    _raise_for_control_error(command, result)
                except EcovacsApiError as err:
                    self._capture_control_event(
                        "api_control_error",
                        device,
                        command,
                        {
                            "request_id": request_id,
                            "duration_ms": round(
                                (time.monotonic() - started) * 1000
                            ),
                            "response": result,
                            "exception": repr(err),
                        },
                    )
                    raise
                self._capture_control_event(
                    "api_control_response",
                    device,
                    command,
                    {
                        "request_id": request_id,
                        "duration_ms": round((time.monotonic() - started) * 1000),
                        "response": result,
                    },
                )
                # Some firmware (e.g. GOAT O800 RTK) returns HTTP 200 with a JSON null body
                # for fire-and-forget controls; treat as an empty success envelope.
                if result is None:
                    return {}
                return result
        except ClientError as err:
            self._capture_control_event(
                "api_control_error",
                device,
                command,
                {
                    "request_id": request_id,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                    "exception": repr(err),
                },
            )
            raise EcovacsApiError(f"Control command {command} failed") from err

    def _capture_control_event(
        self,
        event_type: str,
        device: MowerDevice,
        command: str,
        data: dict[str, Any],
    ) -> None:
        """Write a debug capture control event if capture is active."""
        if self._debug_capture is None:
            return
        self._debug_capture.capture_event(
            event_type,
            {
                "command": command,
                "device": {
                    "did": device.did,
                    "class": device.device_class,
                    "resource": device.resource,
                    "model": device.model,
                },
                **data,
            },
        )

    async def _sst_token(self, device: MowerDevice) -> SstToken:
        cached = self._sst.get(device.did)
        if cached and cached.expires_at > time.time():
            return cached

        credentials = await self.authenticate()
        url = f"https://api-base.dc-{self._continent}.ww.ecouser.net/api/new-perm/token/sst/issue"
        body = {
            "acl": [
                {
                    "policy": [
                        {
                            "obj": [f"Endpoint:{device.device_class}:{device.did}"],
                            "perms": ["Control"],
                        }
                    ],
                    "svc": "dim",
                }
            ],
            "exp": 600,
            "sub": credentials.user_id,
        }
        headers = {
            "authorization": f"Bearer {credentials.token}",
            "content-type": "application/json",
            "user-agent": "okhttp/4.9.1",
        }
        try:
            async with self._session.post(
                url, json=body, headers=headers, timeout=TIMEOUT
            ) as response:
                response.raise_for_status()
                result: dict[str, Any] = await response.json(content_type=None)
        except ClientError as err:
            raise EcovacsApiError("SST token request failed") from err
        token = result["data"]["data"]["token"]
        cached = SstToken(token=token, expires_at=time.time() + 540)
        self._sst[device.did] = cached
        return cached

    async def _login_password(self) -> Any:
        return await self._call_private_api(
            "user/login",
            {"account": self._username, "password": self._password_hash},
        )

    async def _call_private_api(self, endpoint: str, params: dict[str, Any]) -> Any:
        """Call a signed private ECOVACS authentication endpoint."""
        meta = {
            **META,
            "country": country_api_code(self._country),
            "deviceId": self._device_id,
        }
        url = urljoin(
            self._login_url,
            PRIVATE_API_PATH_FORMAT.format(endpoint=endpoint, **meta),
        )
        request_params: dict[str, str | int] = {
            **params,
            "requestId": md5(str(time.time())),
            "authTimespan": int(time.time() * 1000),
            "authTimeZone": "GMT-8",
        }
        return await self._signed_get(url, request_params, meta, CLIENT_KEY, CLIENT_SECRET)

    async def _auth_code(self, access_token: str, user_id: str) -> str:
        params: dict[str, str | int] = {
            "uid": user_id,
            "accessToken": access_token,
            "bizType": "ECOVACS_IOT",
            "deviceId": self._device_id,
            "authTimespan": int(time.time() * 1000),
        }
        url = urljoin(self._auth_code_url, GLOBAL_AUTHCODE_PATH)
        data = _expect_dict(
            await self._signed_get(
                url, params, {"openId": "global"}, AUTH_CLIENT_KEY, AUTH_CLIENT_SECRET
            ),
            "Invalid auth code response",
        )
        return str(data["authCode"])

    async def _login_by_it_token(self, user_id: str, auth_code: str) -> dict[str, Any]:
        data = {
            "edition": "ECOGLOBLE",
            "userId": user_id,
            "token": auth_code,
            "realm": REALM,
            "resource": self._device_id,
            "org": "ECOWW" if self._country != COUNTRY_CHINA else "ECOCN",
            "last": "",
            "country": country_api_code(self._country).upper() if self._country != COUNTRY_CHINA else "Chinese",
            "todo": "loginByItToken",
        }
        for _ in range(3):
            response = await self._post(PATH_API_USERS_USER, data)
            if response.get("result") == "ok":
                return response
            if response.get("result") == "fail" and response.get("error") == "set token error.":
                continue
            raise EcovacsAuthError(f"loginByItToken failed: {response}")
        raise EcovacsAuthError("loginByItToken failed after retries")

    async def _signed_get(
        self,
        url: str,
        params: dict[str, str | int],
        extra: dict[str, str | int],
        key: str,
        secret: str,
    ) -> Any:
        signed = sign_params(params, extra, key, secret)
        async with self._session.get(url, params=signed, timeout=TIMEOUT) as response:
            response.raise_for_status()
            result: dict[str, Any] = await response.json(content_type=None)
        if not isinstance(result, dict):
            raise EcovacsAuthError(f"Invalid auth response: {result!r}")
        code = result.get("code")
        if code == "0000":
            return result.get("data")
        message = result.get("msg", "")
        if code in ("1005", "1010"):
            raise EcovacsAuthError("invalid credentials")
        if code == "1012":
            raise EcovacsInvalidVerificationCodeError(message)
        if code == "1013":
            raise EcovacsDeviceVerificationRequiredError(message)
        raise EcovacsAuthError(f"auth call failed: {result}")

    async def _post_authenticated(
        self, path: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        credentials = await self.authenticate()
        payload = {
            **data,
            "auth": {
                "with": "users",
                "userid": credentials.user_id,
                "realm": REALM,
                "token": credentials.token,
                "resource": self._device_id,
            },
        }
        return await self._post(path, payload)

    async def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        url = urljoin(self._portal_url, "api/" + path)
        try:
            async with self._session.post(url, json=data, timeout=TIMEOUT) as response:
                response.raise_for_status()
                result: dict[str, Any] = await response.json(content_type=None)
                return result
        except ClientResponseError as err:
            raise EcovacsApiError(f"POST {path} failed") from err


def _expect_dict(value: Any, error: str) -> dict[str, Any]:
    """Verify that an ECOVACS API response is an object, not a list/None."""
    if not isinstance(value, dict):
        raise EcovacsAuthError(error)
    return value


def _load_public_key(config_value: str) -> rsa.RSAPublicKey:
    """Parse the RSA public key out of a getConfig PUBLIC.KEY.CONFIG value."""
    try:
        encoded_key = json.loads(config_value)["publicKey"]
    except (KeyError, TypeError, json.JSONDecodeError) as ex:
        raise EcovacsAuthError("Invalid Ecovacs public key") from ex
    if not isinstance(encoded_key, str):
        raise EcovacsAuthError("Invalid Ecovacs public key")

    try:
        key = serialization.load_der_public_key(
            base64.b64decode(encoded_key, validate=True)
        )
    except ValueError as ex:
        raise EcovacsAuthError("Invalid Ecovacs public key") from ex

    if not isinstance(key, rsa.RSAPublicKey):
        raise EcovacsAuthError("Ecovacs public key is not an RSA key")
    return key


def app_payload(data: Any) -> dict[str, Any]:
    """Build the JSON envelope used by the official GOAT app."""
    offset = datetime.now().astimezone().utcoffset()
    tzm = int(offset.total_seconds() // 60) if offset else 0
    return {
        "body": {"data": data},
        "header": {
            "pri": 2,
            "ts": str(int(time.time() * 1000)),
            "tzm": tzm,
            "ver": "0.0.22",
        },
    }


def _raise_for_control_error(command: str, result: Any) -> None:
    """Raise when ECOVACS reports a failed N-GIoT control response."""
    if result is None:
        return
    if not isinstance(result, dict):
        raise EcovacsApiError(f"Control command {command} returned {result!r}")

    if "ret" in result and result["ret"] != "ok":
        raise EcovacsApiError(f"Control command {command} returned {result!r}")

    payload = result
    if "resp" in result:
        try:
            payload = json.loads(result["resp"])
        except (TypeError, json.JSONDecodeError):
            return

    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, dict) or "code" not in body:
        return

    try:
        code = int(body["code"])
    except (TypeError, ValueError):
        code = body["code"]

    if code != 0:
        msg = body.get("msg") or body.get("message") or "unknown error"
        raise EcovacsApiError(
            f"Control command {command} failed with code {code}: {msg}"
        )


def sign_params(
    params: dict[str, str | int],
    extra: dict[str, str | int],
    key: str,
    secret: str,
) -> dict[str, str | int]:
    """Sign ECOVACS auth params."""
    sign_data: dict[str, str | int] = {**extra, **params}
    sign_text = key + "".join(
        k + "=" + str(sign_data[k]) for k in sorted(sign_data)
    ) + secret
    return {**params, "authSign": md5(sign_text), "authAppkey": key}


def md5(value: str) -> str:
    """MD5 hex digest for ECOVACS API compatibility (vendor signing and password wire format)."""
    # codeql[py/weak-sensitive-data-hashing]
    return hashlib.md5(value.encode(), usedforsecurity=False).hexdigest()


def country_api_code(country: str) -> str:
    """Return the country code used by the API for the given country."""
    code = country.lower()
    if code == "gb":
        code = "uk"
    return code


def country_continent(country: str) -> str:
    """Return the ECOVACS data-center continent key."""
    if country == COUNTRY_CHINA:
        return "ww"
    if country in EU_COUNTRIES:
        return "eu"
    return "ww"
