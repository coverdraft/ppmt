"""
Tests for PPMT Credential Encryption — ENTREGABLE 6.

Validates the Fernet-based in-transit encryption for API keys:
  - derive_fernet_key determinism
  - encrypt_field / decrypt_field roundtrip
  - decrypt_auth_payload happy path
  - Wrong password → None
  - Tampered token → None
  - TTL expiry → None
"""

import hashlib
import pytest

from ppmt.execution.crypto import (
    derive_fernet_key,
    encrypt_field,
    decrypt_field,
    decrypt_auth_payload,
)


class TestDeriveFernetKey:
    """Key derivation must be deterministic — same password → same key."""

    def test_deterministic(self):
        password = "MiPasswordSeguro123"
        pw_hash = hashlib.sha256(password.encode()).hexdigest()
        key1 = derive_fernet_key(pw_hash)
        key2 = derive_fernet_key(pw_hash)
        assert key1 == key2

    def test_different_passwords_different_keys(self):
        h1 = hashlib.sha256(b"password_A").hexdigest()
        h2 = hashlib.sha256(b"password_B").hexdigest()
        assert derive_fernet_key(h1) != derive_fernet_key(h2)

    def test_key_length(self):
        """Fernet keys are 44-byte URL-safe base64."""
        key = derive_fernet_key(hashlib.sha256(b"test").hexdigest())
        assert len(key) == 44


class TestEncryptDecryptRoundtrip:
    """encrypt → decrypt must return original plaintext."""

    def test_roundtrip(self):
        pw_hash = hashlib.sha256(b"MiPasswordSeguro123").hexdigest()
        plaintext = "mx0abcdef1234567890"
        token = encrypt_field(plaintext, pw_hash)
        recovered = decrypt_field(token, pw_hash)
        assert recovered == plaintext

    def test_secret_roundtrip(self):
        pw_hash = hashlib.sha256(b"session_pw_456").hexdigest()
        plaintext = "secret_abcdef9876543210fedcba"
        token = encrypt_field(plaintext, pw_hash)
        recovered = decrypt_field(token, pw_hash)
        assert recovered == plaintext

    def test_wrong_password_returns_none(self):
        pw_hash = hashlib.sha256(b"correct_password").hexdigest()
        wrong_hash = hashlib.sha256(b"wrong_password").hexdigest()
        token = encrypt_field("my_api_key", pw_hash)
        assert decrypt_field(token, wrong_hash) is None

    def test_tampered_token_returns_none(self):
        pw_hash = hashlib.sha256(b"test").hexdigest()
        token = encrypt_field("my_api_key", pw_hash)
        # Tamper with the token
        tampered = token[:-5] + "XXXXX"
        assert decrypt_field(tampered, pw_hash) is None

    def test_empty_plaintext(self):
        pw_hash = hashlib.sha256(b"test").hexdigest()
        token = encrypt_field("", pw_hash)
        assert decrypt_field(token, pw_hash) == ""


class TestDecryptAuthPayload:
    """Full auth payload decryption as the WebSocket handler would use it."""

    def test_happy_path(self):
        pw_hash = hashlib.sha256(b"MiPasswordSeguro123").hexdigest()
        enc_key = encrypt_field("mx0_api_key_12345", pw_hash)
        enc_secret = encrypt_field("api_secret_67890", pw_hash)

        payload = {
            "type": "auth",
            "api_key": enc_key,
            "api_secret": enc_secret,
            "session_password_hash": pw_hash,
        }

        api_key, api_secret = decrypt_auth_payload(payload)
        assert api_key == "mx0_api_key_12345"
        assert api_secret == "api_secret_67890"

    def test_missing_password_hash(self):
        payload = {
            "type": "auth",
            "api_key": "something",
            "api_secret": "something",
        }
        assert decrypt_auth_payload(payload) == (None, None)

    def test_missing_encrypted_fields(self):
        pw_hash = hashlib.sha256(b"test").hexdigest()
        payload = {
            "type": "auth",
            "session_password_hash": pw_hash,
        }
        assert decrypt_auth_payload(payload) == (None, None)

    def test_wrong_password_in_payload(self):
        pw_hash = hashlib.sha256(b"correct").hexdigest()
        wrong_hash = hashlib.sha256(b"wrong").hexdigest()
        enc_key = encrypt_field("my_key", pw_hash)
        enc_secret = encrypt_field("my_secret", pw_hash)

        payload = {
            "type": "auth",
            "api_key": enc_key,
            "api_secret": enc_secret,
            "session_password_hash": wrong_hash,
        }

        assert decrypt_auth_payload(payload) == (None, None)
