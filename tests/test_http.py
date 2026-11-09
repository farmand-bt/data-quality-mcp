"""The Streamable HTTP transport, in process (planning/milestone-4.md, D7).

Every request goes through the real app: the bearer middleware, the SDK's host
checks and body cap, and the tools behind them. Starlette's TestClient runs the
app's lifespan when used as a context manager, as uvicorn would.
"""

import contextlib
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from data_quality_mcp import config
from data_quality_mcp.cleaner_core import cleaning
from data_quality_mcp.profiler_core import loader
from data_quality_mcp.server import create_http_app

TOKEN = "t0ken-for-tests_0123456789abcdefghijklmnop"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
STEPS = [{"action": "remove_duplicates"}]
NOT_ALLOWED = "can't read or write files on its own disk"


@contextlib.contextmanager
def serve(
    *, token: str | None = TOKEN, host: str = "127.0.0.1", allowed_hosts=()
) -> Iterator[TestClient]:
    base = f"http://[{host}]:8000" if ":" in host else f"http://{host}:8000"
    app = create_http_app(host=host, token=token, allowed_hosts=allowed_hosts)
    with TestClient(app, base_url=base) as client:
        yield client


@pytest.fixture
def client() -> Iterator[TestClient]:
    with serve() as c:
        yield c


def rpc(client: TestClient, method: str, params=None, headers=None, path="/mcp"):
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(
        path, json=body, headers={**MCP_HEADERS, **AUTH, **(headers or {})}
    )


def call_tool(client: TestClient, name: str, arguments: dict) -> dict:
    response = rpc(client, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200
    return response.json()["result"]


# --- auth (D2) ---


def _post_mcp(client: TestClient, authorization) -> int:
    headers = dict(MCP_HEADERS)
    if authorization is not None:
        headers["Authorization"] = authorization
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return client.post("/mcp", json=body, headers=headers)


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        f"Basic {TOKEN}",
        f"Bearer {'x' * len(TOKEN)}",
        f"Bearer {TOKEN} extra",
        f"Bearer{TOKEN}",
        "Bearer",
        TOKEN,
        f"Bearer {TOKEN[:-1]}",
        ("Bearer " + "é" * 40).encode("utf-8"),  # non-ASCII: 401, not a 500
    ],
    ids=[
        "missing",
        "basic",
        "wrong",
        "trailing-text",
        "no-space",
        "no-token",
        "no-scheme",
        "prefix",
        "non-ascii",
    ],
)
def test_requests_without_the_right_token_get_401(client, authorization):
    response = _post_mcp(client, authorization)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"error": "unauthorized"}  # never says which case


@pytest.mark.parametrize(
    "authorization",
    [f"Bearer {TOKEN}", f"bearer {TOKEN}", f"BEARER  {TOKEN}", f"Bearer {TOKEN} "],
    ids=["exact", "lowercase", "double-space", "trailing-space"],
)
def test_the_right_token_is_accepted(client, authorization):
    response = _post_mcp(client, authorization)
    assert response.status_code == 200
    assert len(response.json()["result"]["tools"]) == 5


def test_a_token_in_the_url_is_never_read(client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    response = client.post(f"/mcp?token={TOKEN}", json=body, headers=MCP_HEADERS)
    assert response.status_code == 401


@pytest.mark.parametrize("token", [TOKEN, None], ids=["auth", "no-auth"])
def test_websockets_are_refused(token):
    # Fail closed: only lifespan and http scopes reach the app
    with serve(token=token) as c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("/mcp", headers=AUTH):
                pass
    assert exc.value.code == 1008


def test_no_auth_serves_without_a_token():
    with serve(token=None) as c:
        assert _post_mcp(c, None).status_code == 200


# --- /health (D1, D2, D5) ---


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_health_is_open(client, method):
    response = client.request(method, "/health")
    assert response.status_code == 200
    if method == "GET":
        assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/health/"),
        ("GET", "/healthz"),
        ("GET", "/health/x"),
        ("GET", "/mcp/"),
        ("GET", "/"),
        ("OPTIONS", "/health"),
        ("POST", "/health"),
    ],
)
def test_only_exactly_get_health_is_exempt(client, method, path):
    assert client.request(method, path).status_code == 401


def test_health_is_outside_the_host_check():
    with serve(host="0.0.0.0", allowed_hosts=["example.com"]) as c:
        assert c.get("/health").status_code == 200  # Host: 0.0.0.0:8000
        assert rpc(c, "tools/list").status_code == 421
        assert rpc(c, "tools/list", headers={"Host": "example.com"}).status_code == 200


# --- host and origin checks (D5) ---


def test_loopback_rejects_a_foreign_host(client):
    response = rpc(client, "tools/list", headers={"Host": "evil.example:8000"})
    assert response.status_code == 421


def test_loopback_rejects_a_foreign_origin(client):
    response = rpc(client, "tools/list", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_loopback_accepts_the_ipv6_host():
    with serve(host="::1") as c:
        assert rpc(c, "tools/list").status_code == 200  # Host: [::1]:8000


def test_allowed_hosts_are_applied():
    with serve(host="0.0.0.0", allowed_hosts=["mcp.example.com"]) as c:
        assert rpc(c, "tools/list", headers={"Host": "mcp.example.com"}).is_success
        assert rpc(c, "tools/list", headers={"Host": "other.example"}).status_code == (
            421
        )
        with_origin = {"Host": "mcp.example.com", "Origin": "https://mcp.example.com"}
        assert rpc(c, "tools/list", headers=with_origin).status_code == 403


def test_without_allowed_hosts_a_public_interface_has_no_host_check():
    with serve(host="0.0.0.0") as c:
        assert rpc(c, "tools/list", headers={"Host": "anything.example"}).is_success


# --- body cap (D4) ---


@pytest.fixture
def one_mb_cap(monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setattr(config, "MAX_HTTP_REQUEST_MB", 1)
    with serve() as c:
        yield c


def test_a_body_over_the_cap_gets_413(one_mb_cap):
    big = "a" * (1024 * 1024 + 1)
    response = one_mb_cap.post("/mcp", content=big, headers={**MCP_HEADERS, **AUTH})
    assert response.status_code == 413


def test_a_chunked_body_over_the_cap_gets_413(one_mb_cap):
    def chunks():
        for _ in range(20):
            yield b"a" * 65536  # 1.25 MiB in all, with no Content-Length

    response = one_mb_cap.post(
        "/mcp", content=chunks(), headers={**MCP_HEADERS, **AUTH}
    )
    assert response.status_code == 413


def test_a_cap_below_one_byte_still_builds(monkeypatch):
    # A tiny override must not reach the SDK as 0, which it rejects
    monkeypatch.setattr(config, "MAX_HTTP_REQUEST_MB", 1e-9)
    with serve() as c:
        assert rpc(c, "tools/list").status_code == 413


def test_a_body_under_the_cap_is_served(one_mb_cap):
    csv = "a,b\n" + "1,2\n" * 100_000  # 400 KB
    result = call_tool(
        one_mb_cap, "profile_dataset", {"inline_content": csv, "filename_hint": "t.csv"}
    )
    assert result["isError"] is False


# --- no file access over HTTP (D3) ---


@pytest.fixture
def no_filesystem(monkeypatch) -> list[str]:
    """Spies that fail the test if anything reaches the filesystem checks."""
    calls: list[str] = []

    def spy(name):
        def record(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} was called")

        return record

    monkeypatch.setattr(loader, "_read_file", spy("_read_file"))
    monkeypatch.setattr(cleaning, "check_output_path", spy("check_output_path"))
    return calls


@pytest.mark.parametrize(
    ("tool", "extra"),
    [
        ("profile_dataset", {}),
        ("detect_outliers", {}),
        ("find_duplicates", {}),
        ("analyze_correlations", {}),
        ("clean_dataset", {"steps": STEPS}),
    ],
)
def test_every_tool_rejects_file_path(client, no_filesystem, messy_path, tool, extra):
    result = call_tool(client, tool, {"file_path": str(messy_path), **extra})
    assert result["isError"] is True
    assert NOT_ALLOWED in result["content"][0]["text"]
    assert no_filesystem == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"inline_content": "a\n1\n", "filename_hint": "t.csv"},
        {"file_path": "C:\\data\\in.csv"},
        {},
    ],
    ids=["inline", "file", "no-input"],
)
def test_clean_dataset_rejects_output_path_first(client, no_filesystem, arguments):
    # An output_path alone must not probe the disk (does its folder exist?)
    result = call_tool(
        client,
        "clean_dataset",
        {"steps": STEPS, "output_path": "C:\\Windows\\probe.csv", **arguments},
    )
    assert result["isError"] is True
    assert NOT_ALLOWED in result["content"][0]["text"]
    assert no_filesystem == []


def test_inline_content_works_over_http(client, messy_path: Path):
    content = messy_path.read_text(encoding="utf-8")
    result = call_tool(
        client,
        "profile_dataset",
        {"inline_content": content, "filename_hint": messy_path.name},
    )
    assert result["isError"] is False
    assert result["structuredContent"]["dataset"]["quality_score"] == 77

    cleaned = call_tool(
        client,
        "clean_dataset",
        {"steps": STEPS, "inline_content": "a,b\n1,x\n1,x\n", "filename_hint": "t.csv"},
    )
    assert cleaned["structuredContent"]["output"]["content"] == "a,b\n1,x\n"


def test_http_instructions_and_prompt(client):
    init = {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    }
    instructions = rpc(client, "initialize", init).json()["result"]["instructions"]
    assert "inline_content" in instructions
    assert "file_path" not in instructions
    assert "16 MB" in instructions

    prompt = rpc(
        client,
        "prompts/get",
        {"name": "review_data_quality", "arguments": {"file_path": "C:\\d\\s.csv"}},
    ).json()["result"]
    text = prompt["messages"][0]["content"]["text"]
    assert "can't read files" in text
    assert "inline_content" in text


def test_ctx_is_not_in_any_input_schema(client):
    tools = rpc(client, "tools/list").json()["result"]["tools"]
    assert len(tools) == 5
    for tool in tools:
        assert "ctx" not in tool["inputSchema"]["properties"], tool["name"]


# --- logging (D1) ---


def _records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "data_quality_mcp.http"]


def test_requests_are_logged_without_the_token(client, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    rpc(client, "tools/list")  # token in the header
    client.post(f"/mcp?token={TOKEN}", json={}, headers=MCP_HEADERS)  # in the URL

    # Every server-side record; httpx2 is the test client's own request log
    server = [r for r in caplog.records if not r.name.startswith("httpx")]
    assert server
    assert not any(TOKEN in r.getMessage() for r in server)
    assert [r.getMessage() for r in _records(caplog)] == [
        "'POST' '/mcp' 200",
        "'POST' '/mcp' 401",  # the query string isn't logged
    ]
    assert capsys.readouterr().out == ""  # stdout stays empty


def test_a_line_break_in_the_path_is_escaped(client, caplog):
    caplog.set_level(logging.INFO, logger="data_quality_mcp.http")
    client.get("/mcp%0Afake%20entry")
    records = _records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert "\n" not in message
    assert message == "'GET' '/mcp\\nfake entry' 401"


def test_a_long_path_is_truncated(client, caplog):
    caplog.set_level(logging.INFO, logger="data_quality_mcp.http")
    client.get("/" + "x" * 1000)
    assert len(_records(caplog)[0].getMessage()) < 230


def test_health_checks_are_logged_at_debug_only(client, caplog):
    caplog.set_level(logging.DEBUG, logger="data_quality_mcp.http")
    client.get("/health")
    rpc(client, "tools/list")
    assert [(r.levelno, r.getMessage()) for r in _records(caplog)] == [
        (logging.DEBUG, "'GET' '/health' 200"),
        (logging.INFO, "'POST' '/mcp' 200"),
    ]
