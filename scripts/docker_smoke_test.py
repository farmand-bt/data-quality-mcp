"""Smoke-test a running server over HTTP (planning/milestone-4.md, D7, and
milestone-4.5.md, D9).

    DATA_QUALITY_MCP_TOKEN=... python scripts/docker_smoke_test.py http://127.0.0.1:8000
    python scripts/docker_smoke_test.py --oauth http://localhost:8001

The default checks a static-token server, tool calls included. `--oauth` checks
a server in OAuth mode without signing in: the 401 and where it points, the
protected-resource metadata, and that a made-up token is refused.

Standard library only, so it doesn't depend on which HTTP client `mcp` ships.
Exits non-zero, with the failed check on stderr, if anything is off.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from email.message import Message

INLINE_CSV = "id,price,city\n1,10,Oslo\n2,12,Bergen\n3,,Oslo\n3,,Oslo\n4,1000,Bergen\n"
METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"
# Not JWT-shaped, so the server refuses it before any key lookup. The CI job
# checks that the container log never contains it.
MADE_UP_TOKEN = "not-a-jwt-smoke-test-token-0123456789"  # nosec B105


def request(url: str, body=None, token: str | None = None) -> tuple[int, dict, Message]:
    headers = {"Accept": "application/json, text/event-stream"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return (
                response.status,
                json.loads(response.read() or b"{}"),
                response.headers,
            )
    except urllib.error.HTTPError as exc:
        return exc.code, {}, exc.headers


def rpc(base: str, method: str, params: dict, token: str | None) -> tuple[int, dict]:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    status, payload, _ = request(f"{base}/mcp", body, token)
    return status, payload


def check(condition: bool, label: str) -> None:
    if not condition:
        sys.exit(f"FAIL: {label}")
    print(f"ok: {label}")


def check_health(base: str) -> None:
    status, body, _ = request(f"{base}/health")
    check(status == 200 and body == {"status": "ok"}, "GET /health is open")


def static_token(base: str) -> None:
    token = os.environ["DATA_QUALITY_MCP_TOKEN"]
    check_health(base)

    check(rpc(base, "tools/list", {}, None)[0] == 401, "no token: 401")
    check(rpc(base, "tools/list", {}, "x" * 43)[0] == 401, "wrong token: 401")

    init = {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "smoke-test", "version": "1"},
    }
    status, body = rpc(base, "initialize", init, token)
    check(status == 200 and "result" in body, "initialize with the token: 200")

    status, body = rpc(base, "tools/list", {}, token)
    names = sorted(tool["name"] for tool in body["result"]["tools"])
    check(len(names) == 5, f"tools/list lists 5 tools: {names}")

    arguments = {"inline_content": INLINE_CSV, "filename_hint": "smoke.csv"}
    status, body = rpc(
        base, "tools/call", {"name": "profile_dataset", "arguments": arguments}, token
    )
    score = body["result"]["structuredContent"]["dataset"]["quality_score"]
    check(isinstance(score, int), f"inline profile_dataset: quality score {score}")

    arguments = {"file_path": "/etc/passwd"}
    status, body = rpc(
        base, "tools/call", {"name": "profile_dataset", "arguments": arguments}, token
    )
    text = body["result"]["content"][0]["text"]
    check(
        body["result"]["isError"] and "can't read or write files" in text,
        "file_path is refused over HTTP",
    )


def oauth(base: str, issuer: str) -> None:
    check_health(base)

    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    status, _, headers = request(f"{base}/mcp", body)
    challenge = headers.get("WWW-Authenticate", "")
    check(status == 401, "no token: 401")
    check(
        f'resource_metadata="{base}{METADATA_PATH}"' in challenge,
        f"the 401 points to the metadata: {challenge}",
    )

    status, metadata, _ = request(f"{base}{METADATA_PATH}")
    expected = {
        "resource": f"{base}/mcp",
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
    }
    check(status == 200 and metadata == expected, f"the metadata: {metadata}")

    status, _, _ = request(f"{base}/mcp", body, MADE_UP_TOKEN)
    check(status == 401, "a made-up token: 401")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    parser.add_argument("--oauth", action="store_true", help="OAuth-mode checks")
    parser.add_argument("--issuer", default="https://issuer.invalid")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    if args.oauth:
        oauth(base, args.issuer)
    else:
        static_token(base)


if __name__ == "__main__":
    main()
