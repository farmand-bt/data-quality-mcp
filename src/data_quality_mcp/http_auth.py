"""Auth and request logging for the HTTP transport.

Three auth modes, exactly one per server (planning/milestone-4.5.md, D5):

- **Static token** (milestone-4.md, D2): DATA_QUALITY_MCP_TOKEN, checked by
  `BearerAuthMiddleware`, a small pure ASGI middleware around the whole app.
  The only request without a token is exactly GET or HEAD /health.
- **OAuth** (milestone-4.5.md, D3, D4): DATA_QUALITY_MCP_OAUTH_ISSUER. The SDK
  does the HTTP side (the 401, the protected-resource metadata), and asks
  `OAuthTokenCheck` here whether a token is good: an RS256 JWT from the issuer,
  for this server's /mcp URL, belonging to the owner. Its signing keys come
  from `JwksCache`.
- **None:** `--no-auth`, and only on a loopback host. A same-host reverse proxy
  forwards public traffic with a loopback Host, so "only local processes can
  reach it" doesn't hold on its own.

`BearerAuthMiddleware` runs outermost in every mode. Each request is logged as
one line: method, path (escaped, truncated, no query string) and status.
Headers, bodies, tokens and their claims never are.

This module uses Starlette, PyJWT and the standard library, never `mcp`.
server.py turns a `VerifiedToken` into the SDK's `AccessToken`.
"""

import hmac
import json
import logging
import re
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt
from jwt import PyJWK, PyJWKSet
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from data_quality_mcp import __version__

# The variable's name, not a secret (Bandit B105 matches names like *TOKEN*)
TOKEN_ENV = "DATA_QUALITY_MCP_TOKEN"  # nosec B105
OAUTH_ISSUER_ENV = "DATA_QUALITY_MCP_OAUTH_ISSUER"
PUBLIC_URL_ENV = "DATA_QUALITY_MCP_PUBLIC_URL"
OAUTH_OWNER_ENV = "DATA_QUALITY_MCP_OAUTH_OWNER"
OAUTH_JWKS_URL_ENV = "DATA_QUALITY_MCP_OAUTH_JWKS_URL"
# The SDK enables its DNS-rebinding protection for exactly these hosts (D5)
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MIN_TOKEN_LENGTH = 32
HEALTH_PATH = "/health"
MCP_PATH = "/mcp"
MAX_LOGGED_PATH = 200
GENERATE_HINT = 'python -c "import secrets; print(secrets.token_urlsafe(32))"'

# OAuth (milestone-4.5.md, D3)
ALGORITHM = "RS256"
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub"]
LEEWAY = 30  # seconds of clock skew, for iat and nbf (the SDK checks exp itself)
JWKS_TTL = 300  # a fetched key set is fresh for 5 minutes
FETCH_TIMEOUT = 5  # seconds, for one JWKS fetch and for waiting on one
FETCH_BACKOFF = 30  # seconds between fetch attempts after any fetch
MAX_JWKS_BYTES = 1024 * 1024

# RFC 6750 b64token: letters, digits and -._~+/, then optional trailing "="
_TOKEN_FORMAT = re.compile(r"[A-Za-z0-9\-._~+/]+=*")
_BEARER = re.compile(r"bearer +(\S+)", re.IGNORECASE)

logger = logging.getLogger("data_quality_mcp.http")


class StartupError(Exception):
    """The HTTP server can't start with this configuration."""


# --- the static token (milestone-4.md, D2) ---


def read_token(environ: Mapping[str, str]) -> str | None:
    """The configured token, or None when it is unset, empty or blank.

    Surrounding whitespace is stripped, so a `.env` saved with Windows line
    endings doesn't leave a `\\r` in the token.
    """
    token = environ.get(TOKEN_ENV, "").strip()
    if not token:
        return None
    if len(token) < MIN_TOKEN_LENGTH or not _TOKEN_FORMAT.fullmatch(token):
        raise StartupError(
            f"{TOKEN_ENV} must be at least {MIN_TOKEN_LENGTH} characters of "
            "letters, digits and -._~+/ (optionally ending in =), with no quotes "
            f"or spaces. Generate one with: {GENERATE_HINT}"
        )
    return token


# --- OAuth settings (milestone-4.5.md, D6) ---


@dataclass(frozen=True)
class OAuthSettings:
    """OAuth mode's configuration. None of it is secret."""

    issuer: str  # exactly as the token's `iss` must read
    public_url: str  # no trailing slash
    owner: str  # the owner's user ID, compared with `sub`
    jwks_url: str

    @property
    def resource(self) -> str:
        """The protected resource, and the audience every token must name."""
        return self.public_url + MCP_PATH

    @property
    def allowed_host(self) -> str:
        """The public URL's netloc (with its port, if any): the Host to accept."""
        return urlsplit(self.public_url).netloc


def _check_url(name: str, value: str, *, path_allowed: bool) -> str:
    """Validate an OAuth URL; return its path. Raises StartupError."""
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        raise StartupError(f"{name} isn't a valid URL: {value!r}") from None
    if parts.scheme == "http" and host not in LOOPBACK_HOSTS:
        raise StartupError(
            f"{name} must use https (http only on localhost, 127.0.0.1 or "
            f"[::1]): {value!r}"
        )
    if parts.scheme not in ("https", "http") or not host:
        raise StartupError(f"{name} must be an https URL with a host: {value!r}")
    if parts.username is not None or parts.password is not None:
        raise StartupError(f"{name} must not contain a user name or password")
    if parts.query or parts.fragment or value.endswith(("?", "#")):
        raise StartupError(f"{name} must not have a query or fragment: {value!r}")
    if not path_allowed and parts.path not in ("", "/"):
        raise StartupError(f"{name} must not have a path: {value!r}")
    return parts.path


def read_oauth_settings(environ: Mapping[str, str]) -> OAuthSettings | None:
    """OAuth mode's settings, or None when DATA_QUALITY_MCP_OAUTH_ISSUER is unset.

    A blank value counts as unset. In OAuth mode a missing or malformed setting
    raises StartupError naming the variable, so the server never starts half
    configured.
    """
    issuer = environ.get(OAUTH_ISSUER_ENV, "").strip()
    if not issuer:
        return None
    if _check_url(OAUTH_ISSUER_ENV, issuer, path_allowed=True):
        raise StartupError(
            f"{OAUTH_ISSUER_ENV} must be exactly the issuer, with no path or "
            f"trailing slash (e.g. https://<tenant>.authkit.app): {issuer!r}"
        )

    public_url = environ.get(PUBLIC_URL_ENV, "").strip()
    if not public_url:
        raise StartupError(
            f"{PUBLIC_URL_ENV} is required in OAuth mode: the server's public "
            "address, e.g. https://<service>.onrender.com"
        )
    _check_url(PUBLIC_URL_ENV, public_url, path_allowed=False)
    public_url = public_url.rstrip("/")

    owner = environ.get(OAUTH_OWNER_ENV, "").strip()
    if not owner:
        raise StartupError(
            f"{OAUTH_OWNER_ENV} is required in OAuth mode: the owner's user ID "
            "(the token's `sub`). Only that user's tokens are accepted."
        )
    if any(char.isspace() for char in owner):
        raise StartupError(f"{OAUTH_OWNER_ENV} must not contain whitespace")

    jwks_url = environ.get(OAUTH_JWKS_URL_ENV, "").strip()
    if jwks_url:
        _check_url(OAUTH_JWKS_URL_ENV, jwks_url, path_allowed=True)
    else:
        jwks_url = issuer + "/oauth2/jwks"
    return OAuthSettings(
        issuer=issuer, public_url=public_url, owner=owner, jwks_url=jwks_url
    )


def leftover_oauth_variables(environ: Mapping[str, str]) -> list[str]:
    """OAuth variables that are set while the issuer isn't, so they do nothing."""
    if environ.get(OAUTH_ISSUER_ENV, "").strip():
        return []
    names = (PUBLIC_URL_ENV, OAUTH_OWNER_ENV, OAUTH_JWKS_URL_ENV)
    return [name for name in names if environ.get(name, "").strip()]


def resolve_auth(
    host: str,
    token: str | None,
    no_auth: bool,
    oauth: OAuthSettings | None = None,
) -> str | None:
    """The static token every request must carry, or None when there is none
    (OAuth mode, where the SDK checks tokens, or --no-auth).

    Raises StartupError for every combination that would serve without the
    protection the operator expects, or that mixes two modes.
    """
    if oauth is not None:
        if token is not None:
            raise StartupError(
                f"{OAUTH_ISSUER_ENV} and {TOKEN_ENV} are both set. Use one auth "
                "mode: unset the token for OAuth, or the issuer for the token."
            )
        if no_auth:
            raise StartupError(
                f"--no-auth and {OAUTH_ISSUER_ENV} are both set. Drop --no-auth "
                "to use OAuth, or unset the issuer."
            )
        return None
    if no_auth:
        if token is not None:
            raise StartupError(
                f"--no-auth and {TOKEN_ENV} are both set. Drop --no-auth to "
                "require the token, or unset the token."
            )
        if host not in LOOPBACK_HOSTS:
            raise StartupError(
                "--no-auth is only accepted on a loopback host (127.0.0.1, "
                f"localhost or ::1), not {host!r}."
            )
        return None
    if token is None:
        raise StartupError(
            f"Set {TOKEN_ENV} to serve over HTTP: every request must carry it "
            f"as 'Authorization: Bearer <token>'. Generate one with: "
            f"{GENERATE_HINT}. For OAuth sign-in, set {OAUTH_ISSUER_ENV} instead "
            "(docs/deployment.md)."
        )
    return token


# --- the signing keys (milestone-4.5.md, D3) ---


class JwksError(Exception):
    """The key set couldn't be fetched or read."""


def _fetch_jwks(url: str, timeout: float) -> PyJWKSet:
    """Fetch and parse a JWKS. Raises on any failure."""
    headers = {
        "Accept": "application/json",
        "User-Agent": f"data-quality-mcp/{__version__}",
    }
    request = urllib.request.Request(url, headers=headers)
    # The URL's scheme was checked at startup (https, or http on loopback)
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        if response.status != 200:
            raise JwksError(f"status {response.status}")
        body = response.read(MAX_JWKS_BYTES + 1)
    if len(body) > MAX_JWKS_BYTES:
        raise JwksError("key set too large")
    return PyJWKSet.from_dict(json.loads(body))


def _usable(key: PyJWK) -> bool:
    """An RSA signing key with a kid, for RS256 (PyJWT's default for RSA when
    the key states no algorithm)."""
    return (
        key.key_type == "RSA"
        and isinstance(key.key_id, str)
        and bool(key.key_id)
        and key.algorithm_name == ALGORITHM
        and key.public_key_use in (None, "sig")
    )


class JwksCache:
    """The issuer's signing keys, fetched lazily and kept by `kid`.

    - Fresh for JWKS_TTL; after that, the next check refreshes it.
    - One fetch at a time. A check whose kid is in the current set, even an
      expired one, never waits for another check's fetch. A check with no
      usable key waits for the fetch in flight, up to FETCH_TIMEOUT.
    - A fetch starts only FETCH_BACKOFF after the previous attempt. That is the
      backoff after a failure, and the limit on refreshes for unknown kids.
    - A failed refresh keeps the last good set (stale-if-error): a short outage
      of the issuer doesn't sign the owner out. The trade-off: while it lasts,
      a key the issuer has rotated out keeps working.
    """

    def __init__(
        self,
        url: str,
        *,
        fetch: Callable[[str, float], PyJWKSet] = _fetch_jwks,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = url
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Condition()
        self._keys: dict[str, PyJWK] | None = None
        self._fetched_at = 0.0
        self._last_attempt: float | None = None
        self._in_flight = False

    def _may_fetch(self, now: float) -> bool:
        return not self._in_flight and (
            self._last_attempt is None or now - self._last_attempt >= FETCH_BACKOFF
        )

    def get_key(self, kid: str) -> PyJWK | None:
        """The key for `kid`, or None if there isn't one (now)."""
        with self._lock:
            now = self._clock()
            if self._keys is not None and kid in self._keys:
                expired = now - self._fetched_at >= JWKS_TTL
                if not (expired and self._may_fetch(now)):
                    return self._keys[kid]
            elif self._in_flight:
                deadline = time.monotonic() + FETCH_TIMEOUT
                while self._in_flight:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._lock.wait(remaining)
                return None if self._keys is None else self._keys.get(kid)
            elif not self._may_fetch(now):
                return None
            self._in_flight = True
            self._last_attempt = now
        self._refresh()
        with self._lock:
            return None if self._keys is None else self._keys.get(kid)

    def _refresh(self) -> None:
        """Fetch the set; on failure keep the old one. Never raises."""
        keys: dict[str, PyJWK] | None = None
        try:
            fetched = self._fetch(self._url, FETCH_TIMEOUT)
            keys = {key.key_id: key for key in fetched.keys if _usable(key)}
        except Exception as exc:
            logger.warning(
                "JWKS fetch failed (%s); keeping %s, next attempt in %d s",
                type(exc).__name__,
                "the last good key set" if self._keys else "no keys",
                FETCH_BACKOFF,
            )
        with self._lock:
            if keys is not None:
                self._keys = keys
                self._fetched_at = self._clock()
            self._in_flight = False
            self._lock.notify_all()


# --- the token check (milestone-4.5.md, D3, D4) ---


@dataclass(frozen=True)
class VerifiedToken:
    """What server.py needs from an accepted token to build the SDK's AccessToken."""

    subject: str
    client_id: str
    scopes: list[str]
    expires_at: int


class _Refused(Exception):
    """A token is refused for a reason worth naming in the log."""


class OAuthTokenCheck:
    """Accept only the owner's RS256 JWTs from the issuer, for this resource."""

    def __init__(self, settings: OAuthSettings, *, cache: JwksCache | None = None):
        self.settings = settings
        self._cache = cache if cache is not None else JwksCache(settings.jwks_url)

    def _precheck(self, token: str) -> str:
        """Cheap checks on the unverified token; return its kid.

        Only a filter, not a security boundary: the owner ID isn't a secret.
        What decides is the signature check, and what limits fetches is the
        key cache. The order makes the logged reason useful: `not the owner`
        means the issuer and the audience already matched.
        """
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        if header.get("alg") != ALGORITHM or not isinstance(kid, str) or not kid:
            raise _Refused(f"not {ALGORITHM} with a kid")
        claims = jwt.decode(token, options={"verify_signature": False})
        if claims.get("iss") != self.settings.issuer:
            raise _Refused("wrong issuer")
        audience = claims.get("aud")
        audiences = audience if isinstance(audience, list) else [audience]
        if self.settings.resource not in audiences:
            raise _Refused("wrong audience")
        if claims.get("sub") != self.settings.owner:
            raise _Refused("not the owner")
        return kid

    def _verify(self, token: str) -> VerifiedToken:
        kid = self._precheck(token)
        key = self._cache.get_key(kid)
        if key is None:
            raise _Refused("no signing key")
        claims = jwt.decode(
            token,
            key.key,
            algorithms=[ALGORITHM],
            issuer=self.settings.issuer,
            audience=self.settings.resource,
            leeway=LEEWAY,
            options={"require": REQUIRED_CLAIMS},
        )
        subject, scope, expires = claims["sub"], claims.get("scope", ""), claims["exp"]
        client_id = claims.get("client_id", claims.get("azp", ""))
        # Defense in depth: the precheck already matched `sub` on these same
        # claims, and PyJWT has already checked that `exp` is a number
        if subject != self.settings.owner:  # pragma: no cover
            raise _Refused("not the owner")
        if isinstance(expires, bool) or not isinstance(expires, (int, float)):
            raise _Refused("a claim has the wrong type")  # pragma: no cover
        if not isinstance(scope, str) or not isinstance(client_id, str):
            raise _Refused("a claim has the wrong type")
        return VerifiedToken(
            subject=subject,
            client_id=client_id,
            scopes=scope.split(),
            expires_at=int(expires),
        )

    def check(self, token: str) -> VerifiedToken | None:
        """The verified token, or None. Never raises: the SDK's auth middleware
        runs on every route, and an exception there would be a 500."""
        try:
            return self._verify(token)
        except _Refused as reason:
            logger.info("token refused: %s", reason)
        except Exception as exc:
            logger.info("token refused: %s", type(exc).__name__)
        return None

    async def verify(self, token: str) -> VerifiedToken | None:
        """`check` in a worker thread: a key fetch blocks, the event loop must not."""
        return await run_in_threadpool(self.check, token)


# --- the middleware ---


def _is_health_check(scope: Scope) -> bool:
    return scope["method"] in ("GET", "HEAD") and scope["path"] == HEALTH_PATH


class BearerAuthMiddleware:
    """Log every request, refuse WebSockets, and, with a static token, require
    `Authorization: Bearer <token>` on every request but /health.

    `token=None` skips the token check: for --no-auth, and for OAuth mode,
    where the SDK's own middleware checks tokens on /mcp.
    """

    def __init__(self, app: ASGIApp, token: str | None) -> None:
        self.app = app
        self._token = None if token is None else token.encode("ascii")

    def _authorized(self, scope: Scope) -> bool:
        if self._token is None:
            return True
        value = Headers(scope=scope).get("authorization")
        match = _BEARER.fullmatch(value.strip()) if value else None
        if match is None:
            return False
        # Headers are decoded as latin-1; compare bytes, since compare_digest
        # raises TypeError on a non-ASCII str (a 500 instead of a 401)
        return hmac.compare_digest(match.group(1).encode("latin-1"), self._token)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":  # the SDK starts its session manager here
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            # Fail closed: nothing here serves WebSockets, and a later SDK
            # route must not get around the token
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return

        status = 500  # if the app fails before it starts a response

        async def send_and_record(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        health = _is_health_check(scope)
        try:
            if health or self._authorized(scope):
                await self.app(scope, receive, send_and_record)
            else:
                response = JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send_and_record)
        finally:
            # %r escapes control characters: ASGI percent-decodes the path, so
            # /mcp%0Afake would otherwise write a fake log line
            logger.log(
                logging.DEBUG if health else logging.INFO,
                "%r %r %d",
                scope["method"],
                scope["path"][:MAX_LOGGED_PATH],
                status,
            )
