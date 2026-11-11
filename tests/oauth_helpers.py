"""Shared pieces for the OAuth tests (planning/milestone-4.5.md, D9).

Tokens are signed with a local RSA key, and a fake fetcher hands the key cache
its key set, so no test touches the network.
"""

import base64
import hashlib
import hmac
import json
import threading
import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKSet
from jwt.algorithms import RSAAlgorithm

from data_quality_mcp.http_auth import OAuthSettings

ISSUER = "https://example-tenant.authkit.app"
PUBLIC_URL = "http://localhost:8000"
RESOURCE = PUBLIC_URL + "/mcp"
OWNER = "user_01OWNER"
KID = "key-1"
METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def settings(public_url: str = PUBLIC_URL) -> OAuthSettings:
    return OAuthSettings(
        issuer=ISSUER,
        public_url=public_url,
        owner=OWNER,
        jwks_url=ISSUER + "/oauth2/jwks",
    )


def jwk(key=KEY, kid: str | None = KID, **extra) -> dict:
    """A public JWK for `key`; `kid=None` leaves the kid out."""
    data = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    if kid is not None:
        data["kid"] = kid
    data.update(extra)
    return data


def key_set(*keys: dict) -> PyJWKSet:
    return PyJWKSet.from_dict({"keys": list(keys) or [jwk()]})


def claims(**overrides) -> dict:
    """Good claims for the owner; an override of None drops that claim."""
    now = int(time.time())
    values = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": OWNER,
        "exp": now + 300,
        "iat": now,
        "client_id": "client_test",
        "scope": "openid profile",
    }
    values.update(overrides)
    return {name: value for name, value in values.items() if value is not None}


def mint(*, key=KEY, kid: str | None = KID, alg: str = "RS256", **overrides) -> str:
    headers = {} if kid is None else {"kid": kid}
    return jwt.encode(claims(**overrides), key, algorithm=alg, headers=headers)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unsigned(header: dict, **overrides) -> str:
    """A token with an empty signature (e.g. alg: none)."""
    head = _b64(json.dumps(header).encode())
    body = _b64(json.dumps(claims(**overrides)).encode())
    return f"{head}.{body}."


def hs256_with_public_key() -> str:
    """The classic key-confusion attack: HS256, keyed with the public key's PEM.
    PyJWT refuses to sign it, so it is built by hand."""
    pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": KID}).encode())
    body = _b64(json.dumps(claims()).encode())
    signature = hmac.new(pem, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(signature)}"


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeFetcher:
    """Returns `result` (a key set), or raises it (an exception). With
    `blocked`, each call waits until `release()`; `started` is set on entry."""

    def __init__(self, result=None, *, blocked: bool = False) -> None:
        self.result = key_set() if result is None else result
        self.calls = 0
        self.started = threading.Event()
        self._gate = threading.Event()
        if not blocked:
            self._gate.set()

    def release(self) -> None:
        self._gate.set()

    def __call__(self, url: str, timeout: float) -> PyJWKSet:
        self.calls += 1
        self.started.set()
        self._gate.wait(10)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result
