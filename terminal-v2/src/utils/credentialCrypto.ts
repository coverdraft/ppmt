/**
 * PPMT Credential Encryption — Frontend Fernet-compatible encryption.
 *
 * v0.45.0: ENTREGABLE 6 — API keys are NEVER sent in plaintext over WebSocket.
 *
 * Protocol:
 *   1. User enters a "session password" in the UI.
 *   2. We derive a Fernet key from that password using PBKDF2-SHA256
 *      (same parameters as the Python backend).
 *   3. We encrypt api_key and api_secret with that key.
 *   4. We send the SHA-256 hash of the password + encrypted fields.
 *
 * The Python backend derives the same key from the hash and decrypts.
 *
 * IMPORTANT: This must stay in sync with ppmt/execution/crypto.py:
 *   - PBKDF2 iterations: 480,000
 *   - Salt: b"ppmt-v0.45-session-key-derivation"
 *   - Hash: SHA-256
 *   - Output: 32 bytes → URL-safe base64 (Fernet key)
 */

import CryptoJS from 'crypto-js';

// Must match Python backend exactly
const PBKDF2_ITERATIONS = 480_000;
const PBKDF2_SALT = 'ppmt-v0.45-session-key-derivation';

/**
 * Derive a Fernet-compatible key from a password hash.
 * The password_hash is the SHA-256 hex digest of the user's session password.
 *
 * This mirrors Python's: PBKDF2HMAC(SHA256, length=32, salt, iterations)
 * → base64.urlsafe_b64encode(raw_key)
 */
function deriveFernetKey(passwordHash: string): string {
  const salt = CryptoJS.enc.Utf8.parse(PBKDF2_SALT);
  const key256 = CryptoJS.PBKDF2(passwordHash, salt, {
    keySize: 256 / 32, // 8 words = 32 bytes
    iterations: PBKDF2_ITERATIONS,
    hasher: CryptoJS.algo.SHA256,
  });

  // Get raw bytes as WordArray, convert to Uint8Array, then base64url
  const words = key256.words;
  const sigBytes = key256.sigBytes; // 32
  const bytes = new Uint8Array(sigBytes);
  for (let i = 0; i < sigBytes; i++) {
    bytes[i] = (words[i >>> 2] >>> (24 - (i % 4) * 8)) & 0xff;
  }

  // URL-safe base64 (no padding, replace +/ with -_)
  let base64 = btoa(String.fromCharCode(...bytes));
  base64 = base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return base64;
}

/**
 * Compute SHA-256 hex digest of the session password.
 */
export function hashPassword(password: string): string {
  return CryptoJS.SHA256(password).toString(CryptoJS.enc.Hex);
}

/**
 * Encrypt a plaintext string with Fernet-compatible encryption.
 *
 * Fernet format: Version (1 byte) || Timestamp (8 bytes) || IV (16 bytes) ||
 *                Ciphertext (N bytes, AES-CBC PKCS7) || HMAC (32 bytes)
 *
 * We build this manually to be compatible with Python's cryptography.fernet.
 */
export function encryptField(plaintext: string, sessionPassword: string): string {
  const passwordHash = hashPassword(sessionPassword);
  const fernetKey = deriveFernetKey(passwordHash);

  // Decode the Fernet key back to bytes (signing_key || encryption_key)
  const keyBytes = base64urlDecode(fernetKey);
  const signingKey = keyBytes.slice(0, 16);
  const encryptionKey = keyBytes.slice(16, 32);

  // Version byte
  const version = 0x80;

  // Timestamp: current time in seconds since epoch (big-endian int64)
  const timestamp = Math.floor(Date.now() / 1000);
  const timestampBytes = new Uint8Array(8);
  // JS doesn't have BigInt in all contexts, use DataView
  const dv = new DataView(timestampBytes.buffer);
  dv.setBigUint64(0, BigInt(timestamp), false); // big-endian

  // IV: random 16 bytes
  const iv = CryptoJS.lib.WordArray.random(16);
  const ivBytes = wordArrayToUint8Array(iv);

  // Encrypt: AES-128-CBC with PKCS7 padding
  const plaintextWA = CryptoJS.enc.Utf8.parse(plaintext);
  const encKeyWA = uint8ArrayToWordArray(encryptionKey);
  const encrypted = CryptoJS.AES.encrypt(plaintextWA, encKeyWA, {
    iv: iv,
    mode: CryptoJS.mode.CBC,
    padding: CryptoJS.pad.Pkcs7,
  });
  const ciphertextBytes = wordArrayToUint8Array(CryptoJS.enc.Base64.parse(encrypted.ciphertext.toString(CryptoJS.enc.Base64)));

  // Build the signed data: version || timestamp || iv || ciphertext
  const signedData = new Uint8Array([
    version,
    ...timestampBytes,
    ...ivBytes,
    ...ciphertextBytes,
  ]);

  // HMAC-SHA256 over signed data using signing key
  const signingKeyWA = uint8ArrayToWordArray(signingKey);
  const signedDataWA = uint8ArrayToWordArray(signedData);
  const hmac = CryptoJS.HmacSHA256(signedDataWA, signingKeyWA);
  const hmacBytes = wordArrayToUint8Array(hmac);

  // Full token: signed_data || hmac
  const token = new Uint8Array([...signedData, ...hmacBytes]);

  // URL-safe base64 encode
  return base64urlEncode(token);
}

/**
 * Build the auth payload to send over WebSocket.
 *
 * Returns the complete auth message object ready to send.
 */
export function buildAuthPayload(
  apiKey: string,
  apiSecret: string,
  sessionPassword: string,
): {
  type: 'auth';
  api_key: string;
  api_secret: string;
  session_password_hash: string;
} {
  const passwordHash = hashPassword(sessionPassword);
  return {
    type: 'auth',
    api_key: encryptField(apiKey, sessionPassword),
    api_secret: encryptField(apiSecret, sessionPassword),
    session_password_hash: passwordHash,
  };
}

// ── Helpers ────────────────────────────────────────────────────

function base64urlEncode(data: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < data.length; i++) {
    binary += String.fromCharCode(data[i]);
  }
  let base64 = btoa(binary);
  return base64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function base64urlDecode(str: string): Uint8Array {
  let base64 = str.replace(/-/g, '+').replace(/_/g, '/');
  // Add padding
  while (base64.length % 4 !== 0) {
    base64 += '=';
  }
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function wordArrayToUint8Array(wa: CryptoJS.lib.WordArray): Uint8Array {
  const bytes = new Uint8Array(wa.sigBytes);
  for (let i = 0; i < wa.sigBytes; i++) {
    bytes[i] = (wa.words[i >>> 2] >>> (24 - (i % 4) * 8)) & 0xff;
  }
  return bytes;
}

function uint8ArrayToWordArray(arr: Uint8Array): CryptoJS.lib.WordArray {
  const words: number[] = [];
  for (let i = 0; i < arr.length; i += 4) {
    words.push(
      ((arr[i] || 0) << 24) |
      ((arr[i + 1] || 0) << 16) |
      ((arr[i + 2] || 0) << 8) |
      (arr[i + 3] || 0),
    );
  }
  return CryptoJS.lib.WordArray.create(words, arr.length);
}
