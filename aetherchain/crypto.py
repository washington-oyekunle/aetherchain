"""Zero-dependency secp256k1 ECDSA primitives used by AetherChain.

This module is intentionally small and auditable. It is suitable for tests and
experimentation, not a substitute for a constant-time production crypto library.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional, Tuple

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
A = 0
B = 7
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)
Point = Optional[Tuple[int, int]]


def is_on_curve(point: Point) -> bool:
    if point is None:
        return True
    x, y = point
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + B)) % P == 0


def point_add(p1: Point, p2: Point) -> Point:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        if y1 == 0:
            return None
        slope = (3 * x1 * x1) * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow((x2 - x1) % P, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def point_mul(k: int, point: Point) -> Point:
    if point is not None and not is_on_curve(point):
        raise ValueError("point is not on secp256k1")
    if k < 0:
        raise ValueError("scalar must be non-negative")
    result: Point = None
    addend = point
    while k:
        if k & 1:
            result = point_add(result, addend)
        addend = point_add(addend, addend)
        k >>= 1
    return result


def _rfc6979_nonce(private_key: int, digest: bytes):
    x = private_key.to_bytes(32, "big")
    h1 = int.from_bytes(digest, "big") % N
    h1_bytes = h1.to_bytes(32, "big")
    k = b"\x00" * 32
    v = b"\x01" * 32
    k = hmac.new(k, v + b"\x00" + x + h1_bytes, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + x + h1_bytes, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        candidate = int.from_bytes(v, "big")
        if 1 <= candidate < N:
            yield candidate
        k = hmac.new(k, v + b"\x00", hashlib.sha256).digest()
        v = hmac.new(k, v, hashlib.sha256).digest()


def generate_keypair() -> tuple[int, Point]:
    private_key = secrets.randbelow(N - 1) + 1
    return private_key, point_mul(private_key, G)


def sign_hash(private_key: int, digest: bytes) -> tuple[int, int]:
    if not 1 <= private_key < N:
        raise ValueError("private key out of range")
    z = int.from_bytes(digest, "big")
    for k in _rfc6979_nonce(private_key, digest):
        r_point = point_mul(k, G)
        assert r_point is not None
        r = r_point[0] % N
        if r == 0:
            continue
        s = (pow(k, -1, N) * (z + r * private_key)) % N
        if s == 0:
            continue
        return r, min(s, N - s)  # low-s normalization
    raise RuntimeError("unable to produce ECDSA signature")


def verify_signature(public_key: Point, digest: bytes, signature: tuple[int, int]) -> bool:
    if public_key is None or not is_on_curve(public_key):
        return False
    r, s = signature
    if not (1 <= r < N and 1 <= s <= N // 2):
        return False
    z = int.from_bytes(digest, "big")
    w = pow(s, -1, N)
    point = point_add(point_mul((z * w) % N, G), point_mul((r * w) % N, public_key))
    return point is not None and point[0] % N == r


def public_key_bytes(public_key: Point) -> bytes:
    if public_key is None or not is_on_curve(public_key):
        raise ValueError("invalid public key")
    return b"\x04" + public_key[0].to_bytes(32, "big") + public_key[1].to_bytes(32, "big")


def address_from_public_key(public_key: Point) -> str:
    return "AETH" + hashlib.sha256(public_key_bytes(public_key)).hexdigest()[:36]


class Wallet:
    def __init__(self) -> None:
        self.private_key, self.public_key = generate_keypair()
        self.address = address_from_public_key(self.public_key)
