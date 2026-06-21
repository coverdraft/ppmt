"""
PPMT Credential Encryption — Fernet-based in-transit encryption for API keys.

v0.45.0: ENTREGABLE 6 — API keys are NEVER sent in plaintext over WebSocket.

Protocol:
  1. Frontend: user enters a "session password" in the UI.
  2. Frontend: derives a key from the password using PBKDF2-SHA256 (same params
     as backend) → uses it as Fernet key to encrypt api_key and api_secret.
  3. Frontend: sends {"type":"auth", "api_key": "<encrypted>", "api_secret": "<encrypted>",
     "session_password_hash": "<sha256_hex_of_password>"}
  4. Backend: derives the same Fernet key from the password hash, decrypts both fields,
     instantiates the executor, then zeroes the plaintext from memory.

The Fernet key is derived deterministically so frontend and backend produce the
same key from the same password — no key exchange needed.

⚠️  This is NOT long-term key storage. Keys live in plaintext only for the
    minimum time needed to instantiate the executor. No DB, no file, no log.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger("ppmt.execution.crypto")

# PBKDF2 parameters — must match frontend exactly
_PBKDF2_ITERATIONS = 480_000  # OWASP 2023 recommendation
_PBKDF2_SALT = b"ppmt-v0.45-session-key-derivation"  # Fixed salt (per-session key not needed for transit)


def derive_fernet_key(password_hash: str) -> bytes:
    """
    Derive a Fernet-compatible 32-byte key from a password hash.

    The password_hash is the SHA-256 hex digest of the user's session password.
    This is what the frontend sends as `session_password_hash`.

    Both sides (frontend crypto-js, backend cryptography) must use the same
    PBKDF2 parameters to derive the same key.

    Returns:
        44-byte URL-safe base64 key suitable for Fernet().
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_PBKDF2_SALT,
        iterations=_PBKDF2_ITERATIONS,
    )
    raw_key = kdf.derive(password_hash.encode("utf-8"))
    # Fernet requires URL-safe base64-encoded 32-byte key
    return base64.urlsafe_b64encode(raw_key)


def encrypt_field(plaintext: str, password_hash: str) -> str:
    """
    Encrypt a single field (api_key or api_secret) with Fernet.

    Used primarily for testing — the frontend does its own encryption
    with crypto-js using the same PBKDF2 parameters.

    Args:
        plaintext: The raw API key or secret.
        password_hash: SHA-256 hex of the session password.

    Returns:
        Fernet token as a string (URL-safe base64).
    """
    key = derive_fernet_key(password_hash)
    f = Fernet(key)
    token = f.encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt_field(token: str, password_hash: str) -> Optional[str]:
    """
    Decrypt a Fernet-encrypted field.

    Args:
        token: The encrypted Fernet token string from the frontend.
        password_hash: SHA-256 hex of the session password.

    Returns:
        Decrypted plaintext, or None if decryption fails (wrong password,
        tampered token, etc.).
    """
    key = derive_fernet_key(password_hash)
    f = Fernet(key)
    try:
        plaintext = f.decrypt(token.encode("ascii"), ttl=120)  # 2-minute TTL
        return plaintext.decode("utf-8")
    except InvalidToken:
        logger.warning("[CRYPTO] Decryption failed — wrong password or tampered token")
        return None
    except Exception as e:
        logger.error(f"[CRYPTO] Unexpected decryption error: {e}")
        return None


def decrypt_auth_payload(payload: dict) -> tuple[Optional[str], Optional[str]]:
    """
    Decrypt an auth payload from the frontend.

    Expected payload format:
        {
            "type": "auth",
            "api_key": "<Fernet-encrypted>",
            "api_secret": "<Fernet-encrypted>",
            "session_password_hash": "<sha256_hex>"
        }

    Returns:
        (api_key, api_secret) — both as plaintext strings.
        (None, None) if decryption fails.
    """
    password_hash = payload.get("session_password_hash", "")
    if not password_hash:
        logger.error("[CRYPTO] Missing session_password_hash in auth payload")
        return None, None

    enc_key = payload.get("api_key", "")
    enc_secret = payload.get("api_secret", "")

    if not enc_key or not enc_secret:
        logger.error("[CRYPTO] Missing encrypted api_key or api_secret")
        return None, None

    api_key = decrypt_field(enc_key, password_hash)
    api_secret = decrypt_field(enc_secret, password_hash)

    if api_key is None or api_secret is None:
        return None, None

    return api_key, api_secret
