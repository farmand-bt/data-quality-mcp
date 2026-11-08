"""Bearer-token auth and request logging for the HTTP transport.

A small, pure ASGI middleware around the whole HTTP app
(planning/milestone-4.md, D2). It uses Starlette and the standard library, and
never imports `mcp`: the SDK's own bearer support is built for OAuth resource
servers and needs an authorization server this server doesn't have.

- The token comes from DATA_QUALITY_MCP_TOKEN and is required on every host.
  `--no-auth` turns auth off on purpose, and only a loopback host accepts it:
  a same-host reverse proxy forwards public traffic with a loopback Host, so
  "only local processes can reach it" doesn't hold on its own.
- The only request without a token is exactly GET or HEAD /health.
- Each request is logged as one line: method, path (escaped, truncated, no
  query string) and status. Headers and bodies never are.
"""

import hmac
import logging
import re
from collections.abc import Mapping

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

TOKEN_ENV = "DATA_QUALITY_MCP_TOKEN"
# The SDK enables its DNS-rebinding protection for exactly these hosts (D5)
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
MIN_TOKEN_LENGTH = 32
HEALTH_PATH = "/health"
MAX_LOGGED_PATH = 200
GENERATE_HINT = 'python -c "import secrets; print(secrets.token_urlsafe(32))"'

# RFC 6750 b64token: letters, digits and -._~+/, then optional trailing "="
_TOKEN_FORMAT = re.compile(r"[A-Za-z0-9\-._~+/]+=*")
_BEARER = re.compile(r"bearer +(\S+)", re.IGNORECASE)

logger = logging.getLogger("data_quality_mcp.http")


class StartupError(Exception):
    """The HTTP server can't start with this configuration."""


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


def resolve_auth(host: str, token: str | None, no_auth: bool) -> str | None:
    """The token every request must carry, or None when auth is off.

    Raises StartupError for every combination that would serve without the
    protection the operator expects.
    """
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
            f"{GENERATE_HINT}"
        )
    return token


def _is_health_check(scope: Scope) -> bool:
    return scope["method"] in ("GET", "HEAD") and scope["path"] == HEALTH_PATH


class BearerAuthMiddleware:
    """Require `Authorization: Bearer <token>` on every request but /health."""

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
