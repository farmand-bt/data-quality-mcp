"""Per-server options, and how a tool reads them during a request.

`server.create_server` gives the app a lifespan that yields a ServerOptions,
so every request sees the options of the server it came through. Tools take an
optional `ctx: Context | None` parameter, which the SDK injects and leaves out
of the input schema (planning/milestone-4.md, D3).
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import Context


@dataclass(frozen=True)
class ServerOptions:
    """What differs between the stdio and the HTTP server."""

    allow_file_path: bool


def options_lifespan(
    options: ServerOptions,
) -> Callable[[Any], AbstractAsyncContextManager[ServerOptions]]:
    """A lifespan for MCPServer that yields `options` to every request."""

    @asynccontextmanager
    async def lifespan(_app: Any) -> AsyncIterator[ServerOptions]:
        yield options

    return lifespan


def file_path_allowed(ctx: Context | None) -> bool:
    """Whether this call may use `file_path` and `output_path`.

    A direct Python call has no context and is local by nature. A call that
    came through a server must find that server's options, or it is refused:
    a mis-wired server can then only break local file access, never open it
    over HTTP.
    """
    if ctx is None:
        return True
    try:
        options = ctx.request_context.lifespan_context
    except (AttributeError, LookupError, ValueError):
        return False
    return isinstance(options, ServerOptions) and options.allow_file_path
