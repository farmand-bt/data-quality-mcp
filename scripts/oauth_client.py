"""Profile a generated CSV on the live server, signing in with OAuth
(planning/milestone-4.5.md, D7).

    uv run python scripts/oauth_client.py https://<name>.onrender.com/mcp --rows 50000

A local tool for the server's owner, never run in CI. It signs in the way
Claude Code does: it registers itself with the authorization server, opens
the sign-in page in your browser, and receives the result on a localhost
callback. Then it sends a CSV of `--rows` rows inline to `profile_dataset` and
prints the quality score and the time the call took.

Tokens live in memory only, for this run. The script never prints or saves
them, and holds no credentials of its own.
"""

import argparse
import asyncio
import http.server
import random
import threading
import time
import webbrowser
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)

SIGN_IN_TIMEOUT = 300  # seconds to finish signing in, in the browser


class MemoryStorage(TokenStorage):
    """Tokens and the client registration, for this run only."""

    def __init__(self) -> None:
        self._tokens: OAuthToken | None = None
        self._client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client = client_info


class Callback:
    """A one-shot HTTP server on localhost that receives the sign-in redirect."""

    def __init__(self, port: int) -> None:
        self.received = threading.Event()
        self.params: dict[str, str] = {}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                query = parse_qs(urlsplit(self.path).query)
                outer.params = {key: values[0] for key, values in query.items()}
                outer.received.set()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Signed in. You can close this tab.")

            def log_message(self, *args) -> None:  # the query holds the code
                pass

        self._server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    async def wait(self) -> AuthorizationCodeResult:
        done = await anyio.to_thread.run_sync(self.received.wait, SIGN_IN_TIMEOUT)
        self._server.shutdown()
        if not done:
            raise TimeoutError("sign-in wasn't completed in time")
        if "code" not in self.params:
            error = self.params.get("error", "no code in the redirect")
            raise RuntimeError(f"sign-in failed: {error}")
        return AuthorizationCodeResult(
            code=self.params["code"],
            state=self.params.get("state"),
            iss=self.params.get("iss"),
        )


async def open_browser(url: str) -> None:
    # The sign-in URL holds no secret (a state value and a PKCE challenge)
    print(f"Opening the sign-in page. If no browser opens, visit:\n{url}\n")
    webbrowser.open(url)


def make_csv(rows: int) -> str:
    rng = random.Random(1)
    cities = ["Oslo", "Bergen", "Trondheim", "Stavanger", "Tromso"]
    lines = ["id,price,qty,city,score,flag,date,note"]
    for i in range(rows):
        price = "" if i % 50 == 0 else f"{rng.uniform(1, 500):.2f}"
        lines.append(
            f"{i},{price},{rng.randint(1, 20)},{rng.choice(cities)},"
            f"{rng.gauss(50, 10):.3f},{rng.random() < 0.5},"
            f"2024-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d},"
            f"n{rng.randint(0, 999)}"
        )
    return "\n".join(lines) + "\n"


async def run(url: str, rows: int, port: int) -> None:
    callback = Callback(port)
    # The same loopback literal the callback listens on (RFC 8252)
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    auth = OAuthClientProvider(
        server_url=url,
        client_metadata=OAuthClientMetadata(
            client_name="data-quality-mcp scripted client",
            redirect_uris=[redirect_uri],
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            token_endpoint_auth_method="none",
        ),
        storage=MemoryStorage(),
        redirect_handler=open_browser,
        callback_handler=callback.wait,
    )
    csv = make_csv(rows)
    print(f"CSV: {rows:,} rows, {len(csv) / 1e6:.1f} MB")
    timeout = httpx2.Timeout(30, read=600)  # a large profile takes a while
    async with httpx2.AsyncClient(auth=auth, timeout=timeout) as http_client:
        transport = streamable_http_client(url, http_client=http_client)
        async with Client(transport) as client:
            tools = await client.list_tools()
            print(f"Signed in; {len(tools.tools)} tools")
            started = time.monotonic()
            result = await client.call_tool(
                "profile_dataset",
                {"inline_content": csv, "filename_hint": "generated.csv"},
                read_timeout_seconds=600,
            )
            elapsed = time.monotonic() - started
    if result.isError:
        text = result.content[0].text if result.content else ""  # type: ignore[union-attr]
        print(f"Tool error after {elapsed:.1f} s: {text}")
        return
    dataset = (result.structuredContent or {}).get("dataset", {})
    print(
        f"profile_dataset: score {dataset.get('quality_score')}, "
        f"{dataset.get('rows')} rows, in {elapsed:.1f} s"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("url", help="the server's /mcp URL")
    parser.add_argument("--rows", type=int, default=1_000)
    parser.add_argument("--port", type=int, default=3030, help="localhost callback")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.rows, args.port))


if __name__ == "__main__":
    main()
