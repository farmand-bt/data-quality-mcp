"""Smoke-test a running server over HTTP (planning/milestone-4.md, D7).

    DATA_QUALITY_MCP_TOKEN=... python scripts/docker_smoke_test.py http://127.0.0.1:8000

Standard library only, so it doesn't depend on which HTTP client `mcp` ships.
Exits non-zero, with the failed check on stderr, if anything is off.
"""

import json
import os
import sys
import urllib.error
import urllib.request

INLINE_CSV = "id,price,city\n1,10,Oslo\n2,12,Bergen\n3,,Oslo\n3,,Oslo\n4,1000,Bergen\n"


def request(url: str, body=None, token: str | None = None) -> tuple[int, dict]:
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
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def rpc(base: str, method: str, params: dict, token: str | None) -> tuple[int, dict]:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return request(f"{base}/mcp", body, token)


def check(condition: bool, label: str) -> None:
    if not condition:
        sys.exit(f"FAIL: {label}")
    print(f"ok: {label}")


def main() -> None:
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    token = os.environ["DATA_QUALITY_MCP_TOKEN"]

    status, body = request(f"{base}/health")
    check(status == 200 and body == {"status": "ok"}, "GET /health is open")

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


if __name__ == "__main__":
    main()
