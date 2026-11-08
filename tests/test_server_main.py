"""Startup: the command line, the token rules and the limit overrides
(planning/milestone-4.md, D1, D2 and D4). uvicorn and the stdio loop are
replaced by recorders, so nothing is served."""

import importlib
import logging
import os
import subprocess
import sys

import pytest
from starlette.testclient import TestClient

from data_quality_mcp import config, server

TOKEN = "t0ken-for-tests_0123456789abcdefghijklmnop"
LIMITS = {
    "MAX_FILE_SIZE_MB": 50,
    "MAX_ROWS": 500_000,
    "MAX_XLSX_UNCOMPRESSED_MB": 200,
    "MAX_INLINE_OUTPUT_MB": 1,
    "MAX_HTTP_REQUEST_MB": 16,
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith(config.ENV_PREFIX):
            monkeypatch.delenv(name)


@pytest.fixture
def served(monkeypatch) -> dict:
    """What main() would serve: uvicorn.run's and app.run's arguments."""
    record: dict = {}
    monkeypatch.setattr(
        server.uvicorn, "run", lambda app, **kwargs: record.update(app=app, **kwargs)
    )
    monkeypatch.setattr(server.app, "run", lambda: record.update(stdio=True))
    return record


@pytest.fixture
def reload_config(monkeypatch):
    """Reload config under a patched environment; restore it afterwards."""
    patched: list[str] = []

    def reload(**env: str):
        for name, value in env.items():
            monkeypatch.setenv(config.ENV_PREFIX + name, value)
            patched.append(config.ENV_PREFIX + name)
        importlib.reload(config)

    yield reload
    for name in patched:
        os.environ.pop(name, None)
    importlib.reload(config)


def _refused(capsys, caplog, argv, expected: str) -> None:
    with pytest.raises(SystemExit) as exc:
        server.main(argv)
    assert exc.value.code == 2
    assert expected in caplog.text
    assert capsys.readouterr().out == ""  # stdout stays empty, even on errors


# --- the token (D2) ---


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "127.0.0.2", "::1"])
@pytest.mark.parametrize("value", [None, "", "   ", "\r\n"], ids=repr)
def test_http_needs_a_token_on_every_host(
    monkeypatch, capsys, caplog, served, host, value
):
    if value is not None:
        monkeypatch.setenv("DATA_QUALITY_MCP_TOKEN", value)
    _refused(
        capsys,
        caplog,
        ["--transport", "http", "--host", host],
        "Set DATA_QUALITY_MCP_TOKEN to serve over HTTP",
    )
    assert served == {}


@pytest.mark.parametrize(
    "token",
    ["a" * 31, "a" * 39 + "!", '"' + "a" * 40 + '"', "a" * 20 + " " + "a" * 20],
    ids=["short", "outside-the-set", "quoted", "space"],
)
def test_a_malformed_token_is_refused(monkeypatch, capsys, caplog, served, token):
    monkeypatch.setenv("DATA_QUALITY_MCP_TOKEN", token)
    _refused(capsys, caplog, ["--transport", "http"], "must be at least 32 characters")
    assert token not in caplog.text  # the error never echoes the token
    assert served == {}


@pytest.mark.parametrize("token", [TOKEN, TOKEN + "\r", f"  {TOKEN}\r\n", TOKEN + "=="])
def test_a_valid_token_is_served(monkeypatch, caplog, served, token):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("DATA_QUALITY_MCP_TOKEN", token)
    server.main(["--transport", "http", "--port", "8123"])

    assert (served["host"], served["port"]) == ("127.0.0.1", 8123)
    assert served["access_log"] is False
    assert served["log_config"] is server.LOG_CONFIG
    assert "auth: bearer token" in caplog.text
    assert token.strip() not in caplog.text
    with TestClient(served["app"], base_url="http://127.0.0.1:8123") as client:
        headers = {
            "Authorization": f"Bearer {token.strip()}",
            "Accept": "application/json, text/event-stream",
        }
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
        assert client.post("/mcp", json=body, headers=headers).status_code == 200


def test_main_drops_the_sdks_rich_handler(monkeypatch, served):
    from rich.logging import RichHandler

    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [*root.handlers, RichHandler()])
    server.main([])
    assert not any(isinstance(h, RichHandler) for h in root.handlers)


def test_logs_are_plain_lines_on_stderr():
    # A real process: no test handler is in the way of basicConfig
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATA_QUALITY")}
    result = subprocess.run(
        [sys.executable, "-m", "data_quality_mcp.server", "--transport", "http"],
        env={**env, "COLUMNS": "60"},  # rich would wrap at 60 columns
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""
    lines = result.stderr.strip().splitlines()
    assert len(lines) == 1
    assert " ERROR data_quality_mcp.server: data-quality-mcp: Set " in lines[0]


def test_uvicorn_logs_go_to_stderr():
    handlers = server.LOG_CONFIG["handlers"].values()
    assert {h["stream"] for h in handlers} == {"ext://sys.stderr"}
    assert server.LOG_CONFIG["disable_existing_loggers"] is False


# --- --no-auth (D2) ---


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_no_auth_is_accepted_on_loopback(caplog, served, host):
    server.main(["--transport", "http", "--host", host, "--no-auth"])
    assert served["host"] == host
    assert "Auth is off (--no-auth)" in caplog.text
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {"Accept": "application/json, text/event-stream"}
    with TestClient(served["app"], base_url="http://127.0.0.1:8000") as client:
        response = client.post("/mcp", json=body, headers=headers)
    assert response.status_code == 200
    assert len(response.json()["result"]["tools"]) == 5


@pytest.mark.parametrize("host", ["0.0.0.0", "127.0.0.2", "192.168.1.5"])
def test_no_auth_is_refused_elsewhere(capsys, caplog, served, host):
    _refused(
        capsys,
        caplog,
        ["--transport", "http", "--host", host, "--no-auth"],
        "--no-auth is only accepted on a loopback host",
    )
    assert served == {}


def test_no_auth_with_a_token_is_refused(monkeypatch, capsys, caplog, served):
    monkeypatch.setenv("DATA_QUALITY_MCP_TOKEN", TOKEN)
    _refused(
        capsys,
        caplog,
        ["--transport", "http", "--no-auth"],
        "--no-auth and DATA_QUALITY_MCP_TOKEN are both set",
    )
    assert served == {}


def test_no_auth_with_stdio_is_a_usage_error(capsys, served):
    with pytest.raises(SystemExit) as exc:
        server.main(["--no-auth"])
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "--no-auth only applies to --transport http" in captured.err
    assert captured.out == ""
    assert served == {}


@pytest.mark.parametrize("port", ["0", "65536", "http"])
def test_a_bad_port_is_a_usage_error(capsys, served, port):
    with pytest.raises(SystemExit) as exc:
        server.main(["--transport", "http", "--port", port])
    assert exc.value.code == 2
    assert served == {}


def test_stdio_is_the_default(served):
    server.main([])
    assert served == {"stdio": True}


def test_allowed_hosts_are_parsed():
    environ = {"DATA_QUALITY_MCP_ALLOWED_HOSTS": " a.example , ,b.example:443,"}
    assert server.parse_allowed_hosts(environ) == ["a.example", "b.example:443"]
    assert server.parse_allowed_hosts({}) == []


# --- limit overrides (D4) ---


@pytest.mark.parametrize("name", LIMITS)
@pytest.mark.parametrize(
    "value", ["abc", "0", "-1", "inf", "nan", "1e400", "1" + "0" * 400]
)
def test_an_invalid_limit_keeps_the_default_and_is_reported(reload_config, name, value):
    reload_config(**{name: value})
    expected = "a positive whole number" if name == "MAX_ROWS" else "a positive number"
    assert config.CONFIG_ERRORS == [
        f"invalid DATA_QUALITY_MCP_{name}={value!r}: expected {expected}"
    ]
    assert getattr(config, name) == LIMITS[name]
    assert config.CONFIG_OVERRIDES == {}


def test_rows_must_be_a_whole_number(reload_config):
    reload_config(MAX_ROWS="1.5")
    assert config.CONFIG_ERRORS == [
        "invalid DATA_QUALITY_MCP_MAX_ROWS='1.5': expected a positive whole number"
    ]


@pytest.mark.parametrize("transport", [[], ["--transport", "http"]])
def test_main_exits_on_an_invalid_limit(
    reload_config, capsys, caplog, served, transport
):
    reload_config(MAX_ROWS="abc")
    _refused(
        capsys,
        caplog,
        transport,
        "invalid DATA_QUALITY_MCP_MAX_ROWS='abc': expected a positive whole number",
    )
    assert served == {}


def test_help_works_with_an_invalid_limit(reload_config, capsys):
    reload_config(MAX_ROWS="abc")
    with pytest.raises(SystemExit) as exc:
        server.main(["--help"])
    assert exc.value.code == 0
    assert "--transport" in capsys.readouterr().out


def test_valid_overrides_are_applied_and_logged(reload_config, caplog, served):
    caplog.set_level(logging.INFO)
    reload_config(MAX_FILE_SIZE_MB=" 20 ", MAX_HTTP_REQUEST_MB="0.5", MAX_ROWS="")
    assert config.MAX_FILE_SIZE_MB == 20 and isinstance(config.MAX_FILE_SIZE_MB, int)
    assert config.MAX_HTTP_REQUEST_MB == 0.5
    assert config.MAX_ROWS == 500_000  # blank means unset
    assert config.CONFIG_ERRORS == []

    server.main([])
    assert "Limit override: DATA_QUALITY_MCP_MAX_FILE_SIZE_MB=20" in caplog.text
    assert "Limit override: DATA_QUALITY_MCP_MAX_HTTP_REQUEST_MB=0.5" in caplog.text
    assert served == {"stdio": True}


@pytest.mark.parametrize(
    ("value", "text"),
    [(50, "50"), (500_000, "500,000"), (1_000_000, "1,000,000"), (0.5, "0.5")],
)
def test_limits_are_formatted_for_the_agent(value, text):
    assert config.format_limit(value) == text


def test_an_override_reaches_the_core():
    # Core defaults are bound when the modules are defined, so check a fresh
    # process: the override must be read before them
    code = (
        "from data_quality_mcp.profiler_core.loader import load_data\n"
        "load_data(inline_content='a\\n' + '1\\n' * 10, filename_hint='t.csv')\n"
    )
    env = {**os.environ, "DATA_QUALITY_MCP_MAX_ROWS": "5"}
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "TooManyRowsError" in result.stderr
    assert "5" in result.stderr.splitlines()[-1]
