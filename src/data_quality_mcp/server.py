"""MCP server entry point.

Builds the MCPServer app and registers every tools module on it. Two
transports (planning/milestone-4.md, D1):

- stdio (the default): `data-quality-mcp`, or `python -m data_quality_mcp.server`.
  Inspect with `uv run mcp dev src/data_quality_mcp/server.py:app`.
- Streamable HTTP: `data-quality-mcp --transport http [--host H] [--port P]`,
  stateless with JSON responses at /mcp, behind a bearer token (http_auth.py).
  Over HTTP the tools refuse file paths: callers send their data inline (D3).

Over stdio, stdout carries the JSON-RPC stream: never print. Logs go to stderr,
on both transports.
"""

import argparse
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import uvicorn
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from data_quality_mcp import __version__, config
from data_quality_mcp.http_auth import (
    HEALTH_PATH,
    LOOPBACK_HOSTS,
    BearerAuthMiddleware,
    StartupError,
    read_token,
    resolve_auth,
)
from data_quality_mcp.tools import cleaning_tools, profiling_tools, prompts, resources
from data_quality_mcp.tools.options import ServerOptions, options_lifespan

SERVER_NAME = "data-quality"
ALLOWED_HOSTS_ENV = "DATA_QUALITY_MCP_ALLOWED_HOSTS"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_ABOUT = (
    "profile_dataset returns the full report: quality score and grade, "
    "prioritized recommendations, and per-column types, type mismatches, "
    "missing values, statistics, outliers and warnings, plus duplicate rows and "
    "correlations. For a narrow question use detect_outliers, find_duplicates "
    "or analyze_correlations; these tools only read."
)
_REFERENCES = (
    "The resource dataquality://docs/quality-score explains the score, and "
    "dataquality://schema/report is the report's JSON Schema. The "
    "review_data_quality prompt runs a complete review."
)

INSTRUCTIONS = (
    "Deterministic data quality profiling and cleaning for CSV and Excel data. "
    "Pass an absolute file_path, or the data itself as inline_content (CSV as "
    "plain text, .xlsx as base64) with a filename_hint. "
    + _ABOUT
    + " clean_dataset applies a list of cleaning steps and writes the result "
    "to a new file (it never overwrites one) or returns it inline, with a log "
    "and the quality score before and after. " + _REFERENCES
)

HTTP_INSTRUCTIONS = (
    "Deterministic data quality profiling and cleaning for CSV and Excel data. "
    "This server is remote and can't see your files: send the data itself as "
    "inline_content (CSV as plain text, .xlsx as base64) with a filename_hint. "
    f"A request can carry up to {config.format_limit(config.MAX_HTTP_REQUEST_MB)} "
    "MB, and base64 "
    "adds about a third. "
    + _ABOUT
    + " clean_dataset applies a list of cleaning steps and returns the cleaned "
    "data inline, with a log and the quality score before and after. " + _REFERENCES
)

# uvicorn's default log config sends the access log to stdout, with query
# strings. Every handler here writes to stderr, the access log is off
# (access_log=False), and BearerAuthMiddleware logs each request instead.
LOG_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,  # keep this package's loggers
    "formatters": {"default": {"format": LOG_FORMAT}},
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stderr",
        }
    },
    "loggers": {
        "uvicorn": {"handlers": ["stderr"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
    },
}

logger = logging.getLogger("data_quality_mcp.server")


def create_server(*, allow_file_path: bool = True) -> MCPServer:
    """Build the app and register every tools module on it.

    `allow_file_path=False` is the HTTP server: its tools refuse file paths,
    and its instructions tell the agent to send data inline.
    """
    app = MCPServer(
        SERVER_NAME,
        version=__version__,
        instructions=INSTRUCTIONS if allow_file_path else HTTP_INSTRUCTIONS,
        lifespan=options_lifespan(ServerOptions(allow_file_path=allow_file_path)),
    )
    profiling_tools.register(app)
    cleaning_tools.register(app)
    resources.register(app)
    prompts.register(app)
    return app


app = create_server()


def create_http_app(
    *,
    host: str = "127.0.0.1",
    token: str | None,
    allowed_hosts: Sequence[str] = (),
) -> ASGIApp:
    """The Streamable HTTP app: stateless, JSON responses, /mcp and /health,
    the request-body cap, the host checks, and the bearer token around it all.

    `token=None` serves without auth; `resolve_auth` only allows that on
    loopback with --no-auth.
    """
    server = create_server(allow_file_path=False)

    @server.custom_route(HEALTH_PATH, methods=["GET"], include_in_schema=False)
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    # None keeps the SDK's default: DNS-rebinding protection on loopback (D5)
    security = None
    if allowed_hosts:
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts),
            allowed_origins=[],
        )
    starlette_app = server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        # At least one byte: a tiny override must not reach the SDK as 0
        max_request_body_size=max(1, int(config.MAX_HTTP_REQUEST_MB * 1024 * 1024)),
        transport_security=security,
        host=host,
    )
    return BearerAuthMiddleware(starlette_app, token=token)


def parse_allowed_hosts(environ: Mapping[str, str]) -> list[str]:
    """DATA_QUALITY_MCP_ALLOWED_HOSTS as a list (comma-separated, blanks dropped)."""
    raw = environ.get(ALLOWED_HOSTS_ENV, "")
    return [host.strip() for host in raw.split(",") if host.strip()]


def _port(value: str) -> int:
    port = int(value)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"{value} is not a port (1-65535)")
    return port


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="data-quality-mcp",
        description="Data quality MCP server (stdio by default, or Streamable HTTP).",
    )
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1", help="HTTP only")
    parser.add_argument("--port", type=_port, default=8000, help="HTTP only")
    parser.add_argument(
        "--no-auth",
        action="store_true",
        help=(
            "HTTP only: serve without a token, for a local experiment. Only "
            "accepted on 127.0.0.1, localhost or ::1. Never use it behind a "
            "reverse proxy or a tunnel."
        ),
    )
    args = parser.parse_args(argv)
    if args.no_auth and args.transport != "http":
        parser.error("--no-auth only applies to --transport http")
    return args


def _host_check(host: str, allowed_hosts: Sequence[str]) -> str:
    if allowed_hosts:
        return ", ".join(allowed_hosts)
    return "loopback only" if host in LOOPBACK_HOSTS else "off"


def _url(host: str, port: int) -> str:
    return f"http://[{host}]:{port}/mcp" if ":" in host else f"http://{host}:{port}/mcp"


def _configure_logging() -> None:
    """Plain, one-line log records on stderr.

    MCPServer installs a rich handler on the root logger when it is created,
    which wraps a record over several lines, so it is swapped for a plain
    StreamHandler. Other handlers (a test's capture handler) are kept.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if type(handler).__module__.startswith("rich."):
            root.removeHandler(handler)
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format=LOG_FORMAT)


def main(argv: Sequence[str] | None = None) -> None:
    """Console-script entry point: serve over stdio, or over HTTP."""
    _configure_logging()
    if config.CONFIG_ERRORS:
        for message in config.CONFIG_ERRORS:
            logger.error("data-quality-mcp: %s", message)
        sys.exit(2)
    for variable, value in config.CONFIG_OVERRIDES.items():
        logger.info("Limit override: %s=%s", variable, value)

    args = _parse_args(argv)
    if args.transport == "stdio":
        app.run()
        return

    try:
        token = resolve_auth(args.host, read_token(os.environ), args.no_auth)
    except StartupError as exc:
        logger.error("data-quality-mcp: %s", exc)
        sys.exit(2)
    allowed_hosts = parse_allowed_hosts(os.environ)
    logger.info(
        "Serving MCP over HTTP at %s (auth: %s; host check: %s)",
        _url(args.host, args.port),
        "bearer token" if token else "off",
        _host_check(args.host, allowed_hosts),
    )
    if token is None:
        logger.warning(
            "Auth is off (--no-auth): any program on this machine can call the "
            "tools, and so can anything a reverse proxy or tunnel on this "
            "machine forwards here."
        )
    uvicorn.run(
        create_http_app(host=args.host, token=token, allowed_hosts=allowed_hosts),
        host=args.host,
        port=args.port,
        log_config=LOG_CONFIG,
        access_log=False,
    )


if __name__ == "__main__":
    main()
