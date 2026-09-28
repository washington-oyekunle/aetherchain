"""Password-protected wallet files for developer use.

This is an encrypted convenience format, not a replacement for an audited HSM or
production wallet. The format uses PBKDF2-HMAC-SHA256, an HMAC-authenticated
SHA-256 keystream, and JSON metadata.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from .crypto import G, Wallet, point_mul

ITERATIONS = 200_000


def _stream(key: bytes, length: int) -> bytes:
    return b"".join(hashlib.sha256(key + counter.to_bytes(4, "big")).digest() for counter in range((length + 31) // 32))[:length]


def encrypt_wallet(wallet: Wallet, password: str) -> dict[str, object]:
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS, 32)
    plaintext = str(wallet.private_key).encode()
    ciphertext = bytes(a ^ b for a, b in zip(plaintext, _stream(key + nonce, len(plaintext))))
    mac = hmac.new(key, nonce + ciphertext, hashlib.sha256).hexdigest()
    return {"version": 1, "address": wallet.address, "salt": salt.hex(), "nonce": nonce.hex(),
            "iterations": ITERATIONS, "ciphertext": ciphertext.hex(), "mac": mac}


def decrypt_wallet(payload: dict[str, object], password: str) -> Wallet:
    if payload.get("version") != 1 or not password:
        raise ValueError("unsupported keystore or empty password")
    salt, nonce = bytes.fromhex(str(payload["salt"])), bytes.fromhex(str(payload["nonce"]))
    ciphertext = bytes.fromhex(str(payload["ciphertext"]))
    iterations = int(payload.get("iterations", ITERATIONS))
    key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, 32)
    expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(payload.get("mac", ""))):
        raise ValueError("invalid wallet password or corrupted keystore")
    try:
        private_key = int(bytes(a ^ b for a, b in zip(ciphertext, _stream(key + nonce, len(ciphertext)))).decode())
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("invalid private key in keystore") from exc
    if not 1 <= private_key < 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141:
        raise ValueError("invalid private key in keystore")
    wallet = Wallet.from_private_key(private_key)
    if wallet.address != payload.get("address"):
        raise ValueError("keystore address mismatch")
    return wallet


def save_keystore(wallet: Wallet, path: str | Path, password: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(encrypt_wallet(wallet, password), sort_keys=True, indent=2) + "\n", encoding="utf-8")


def load_keystore(path: str | Path, password: str) -> Wallet:
    return decrypt_wallet(json.loads(Path(path).read_text(encoding="utf-8")), password)
