"""Tests for ECOVACS device-verification auth helpers."""

import base64
import json
from pathlib import Path
import sys
import types

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
import pytest

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(PACKAGE_PATH.parent)]
sys.modules.setdefault("custom_components", custom_components)

ecovacs_goat_g1 = types.ModuleType("custom_components.ecovacs_goat_g1")
ecovacs_goat_g1.__path__ = [str(PACKAGE_PATH)]
sys.modules.setdefault("custom_components.ecovacs_goat_g1", ecovacs_goat_g1)

from custom_components.ecovacs_goat_g1.mower_api import (  # noqa: E402
    EcovacsAuthError,
    _expect_dict,
    _load_public_key,
)


def _config_value(encoded_key: str) -> str:
    return json.dumps({"publicKey": encoded_key})


def test_load_public_key_parses_valid_rsa_der() -> None:
    """A getConfig-shaped value wrapping a real RSA DER key parses cleanly."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    encoded = base64.b64encode(der).decode()

    key = _load_public_key(_config_value(encoded))

    assert isinstance(key, rsa.RSAPublicKey)


def test_load_public_key_rejects_invalid_json() -> None:
    with pytest.raises(EcovacsAuthError):
        _load_public_key("not json")


def test_load_public_key_rejects_invalid_base64() -> None:
    with pytest.raises(EcovacsAuthError):
        _load_public_key(_config_value("not-base64!!"))


def test_load_public_key_rejects_non_rsa_key() -> None:
    """A structurally valid DER key of the wrong type is rejected, not misused."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    der = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    encoded = base64.b64encode(der).decode()

    with pytest.raises(EcovacsAuthError):
        _load_public_key(_config_value(encoded))


def test_expect_dict_passes_through_dicts() -> None:
    payload = {"uid": "abc"}
    assert _expect_dict(payload, "error") is payload


def test_expect_dict_rejects_non_dicts() -> None:
    with pytest.raises(EcovacsAuthError):
        _expect_dict(["not", "a", "dict"], "invalid shape")
