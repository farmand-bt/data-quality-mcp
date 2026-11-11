"""OAuth mode through the real HTTP app (planning/milestone-4.5.md, D3, D4, D6, D9).

Tokens are signed with a local RSA key, and a fake fetcher stands in for
AuthKit's JWKS. Every request goes through the request log, the SDK's auth
middleware and host checks, and the tools behind them.
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterator

import pytest
from mcp.server.auth.middleware.bearer_auth import RequireAuthMiddleware
from oauth_helpers import (
    ISSUER,
    KID,
    METADATA_PATH,
    OTHER_KEY,
    OWNER,
    PUBLIC_URL,
    RESOURCE,
    FakeFetcher,
    hs256_with_public_key,
    mint,
    settings,
    unsigned,
)
from starlette.testclient import TestClient

from data_quality_mcp.http_auth import JwksCache, OAuthTokenCheck, read_oauth_settings
from data_quality_mcp.server import (
    OwnerTokenVerifier,
    create_http_app,
    effective_allowed_hosts,
)

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
LIST_TOOLS = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def make_check(fetcher: FakeFetcher | None = None, **kwargs) -> OAuthTokenCheck:
    cache = JwksCache(ISSUER + "/oauth2/jwks", fetch=fetcher or FakeFetcher())
    return OAuthTokenCheck(settings(**kwargs), cache=cache)


@contextlib.contextmanager
def serve(
    check: OAuthTokenCheck | None = None, *, allowed_hosts=()
) -> Iterator[TestClient]:
    check = check or make_check()
    app = create_http_app(token=None, oauth=check, allowed_hosts=allowed_hosts)
    with TestClient(app, base_url=check.settings.public_url) as client:
        yield client


@pytest.fixture
def client() -> Iterator[TestClient]:
    with serve() as c:
        yield c


def post_mcp(client: TestClient, token: str | None = None, headers=None):
    all_headers = dict(MCP_HEADERS)
    if token is not None:
        all_headers["Authorization"] = f"Bearer {token}"
    all_headers.update(headers or {})
    return client.post("/mcp", json=LIST_TOOLS, headers=all_headers)


def refusals(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "data_quality_mcp.http" and "token refused" in r.getMessage()
    ]


# --- accepted (D3, D4) ---


@pytest.mark.parametrize("aud", [RESOURCE, ["https://other.example/mcp", RESOURCE]])
def test_the_owners_token_is_accepted(client, aud):
    response = post_mcp(client, mint(aud=aud))
    assert response.status_code == 200
    assert len(response.json()["result"]["tools"]) == 5


def test_the_verifier_builds_the_access_token():
    verifier = OwnerTokenVerifier(make_check())
    token = mint(client_id=None, azp="client_azp", scope="a b")
    access = asyncio.run(verifier.verify_token(token))
    assert access is not None
    assert (access.subject, access.client_id, access.scopes) == (
        OWNER,
        "client_azp",
        ["a", "b"],
    )
    assert access.resource == RESOURCE
    assert access.token == token
    assert isinstance(access.expires_at, int)


def test_missing_optional_claims_have_defaults():
    verified = make_check().check(mint(client_id=None, scope=None))
    assert verified is not None
    assert (verified.client_id, verified.scopes) == ("", [])


# --- refused (D3, D4) ---


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        (lambda: mint(aud=["https://other.example/mcp"]), "wrong audience"),
        (lambda: mint(aud="https://other.example/mcp"), "wrong audience"),
        (lambda: mint(aud=None), "wrong audience"),
        (lambda: mint(sub="user_SOMEONE_ELSE"), "not the owner"),
        (lambda: mint(sub=None), "not the owner"),
        (lambda: mint(sub=12345), "not the owner"),
        (lambda: mint(iss="https://evil.authkit.app"), "wrong issuer"),
        (lambda: mint(iss=ISSUER + "/"), "wrong issuer"),
        (lambda: mint(exp=1_000_000), "ExpiredSignatureError"),
        (lambda: mint(exp=None), "MissingRequiredClaimError"),
        (lambda: mint(iat=None), "MissingRequiredClaimError"),
        (lambda: unsigned({"alg": "none", "kid": KID}), "not RS256 with a kid"),
        (hs256_with_public_key, "not RS256 with a kid"),
        (lambda: mint(kid=None), "not RS256 with a kid"),
        (lambda: mint(kid="unknown-kid"), "no signing key"),
        (lambda: mint(key=OTHER_KEY), "InvalidSignatureError"),
        (lambda: mint(scope=["a", "b"]), "a claim has the wrong type"),
        (lambda: mint(client_id=42), "a claim has the wrong type"),
        (lambda: mint(client_id=None, azp=["x"]), "a claim has the wrong type"),
        (lambda: "garbage", "DecodeError"),
        (lambda: "a.b.c", "DecodeError"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_refused_tokens_get_401_with_the_reason_logged(client, caplog, token, reason):
    caplog.set_level(logging.INFO)
    value = token()
    response = post_mcp(client, value)
    assert response.status_code == 401
    assert refusals(caplog) == [f"token refused: {reason}"]
    assert value not in caplog.text


def test_the_401_points_to_the_metadata(client):
    response = post_mcp(client)
    assert response.status_code == 401
    header = response.headers["www-authenticate"]
    assert header.startswith("Bearer ")
    assert f'resource_metadata="{PUBLIC_URL}{METADATA_PATH}"' in header


def test_a_check_that_fails_unexpectedly_refuses_the_token(caplog):
    class BrokenCache:
        def get_key(self, kid):
            raise RuntimeError("boom")

    caplog.set_level(logging.INFO)
    check = OAuthTokenCheck(settings(), cache=BrokenCache())  # type: ignore[arg-type]
    assert check.check(mint()) is None
    assert refusals(caplog) == ["token refused: RuntimeError"]


# --- the metadata and the open routes (D3) ---


def test_the_metadata_document(client):
    response = client.get(METADATA_PATH)
    assert response.status_code == 200
    assert response.json() == {
        "resource": RESOURCE,
        "authorization_servers": [ISSUER],
        "bearer_methods_supported": ["header"],
    }


@pytest.mark.parametrize("path", ["/health", METADATA_PATH])
@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Bearer garbage",
        "Bearer a.b.c",
        "Bearer " + mint(scope=["not", "a", "string"]),
        "Bearer " + mint(sub="user_SOMEONE_ELSE"),
    ],
    ids=["none", "garbage", "dots", "bad-claim-type", "not-the-owner"],
)
def test_open_routes_never_fail_on_a_bad_token(client, path, authorization):
    headers = {} if authorization is None else {"Authorization": authorization}
    assert client.get(path, headers=headers).status_code == 200


def test_every_route_is_open_by_design_or_needs_the_token():
    # The static-token middleware is off in OAuth mode, so a route the SDK adds
    # later must not slip in unauthenticated: this list has to be updated
    app = create_http_app(token=None, oauth=make_check())
    routes = {route.path: route for route in app.app.routes}
    assert set(routes) == {"/mcp", "/health", METADATA_PATH}
    assert isinstance(routes["/mcp"].app, RequireAuthMiddleware)


def test_no_key_fetch_until_a_token_needs_one():
    fetcher = FakeFetcher()
    with serve(make_check(fetcher)) as c:
        c.get("/health")
        c.get(METADATA_PATH)
        post_mcp(c)
        post_mcp(c, mint(sub="user_SOMEONE_ELSE"))
        assert fetcher.calls == 0
        assert post_mcp(c, mint()).status_code == 200
        assert post_mcp(c, mint()).status_code == 200
        assert fetcher.calls == 1


# --- host and origin checks (D6) ---


def test_the_default_allowed_host_is_the_public_urls_netloc():
    assert effective_allowed_hosts((), make_check()) == ["localhost:8000"]
    public = make_check(public_url="https://dq.example.onrender.com")
    assert effective_allowed_hosts((), public) == ["dq.example.onrender.com"]
    assert effective_allowed_hosts(["a.example"], public) == ["a.example"]
    assert effective_allowed_hosts((), None) == []


def test_the_public_host_is_accepted_and_a_foreign_one_refused(client):
    assert post_mcp(client, mint()).status_code == 200  # Host: localhost:8000
    foreign = post_mcp(client, mint(), headers={"Host": "evil.example"})
    assert foreign.status_code == 421
    no_port = post_mcp(client, mint(), headers={"Host": "localhost"})
    assert no_port.status_code == 421


def test_a_public_url_in_another_form_still_matches_the_sdk():
    # Upper case and a default port: the audience, the advertised resource and
    # the allowed Host must still agree
    environ = {
        "DATA_QUALITY_MCP_OAUTH_ISSUER": ISSUER,
        "DATA_QUALITY_MCP_PUBLIC_URL": "https://DQ.Example.com:443/",
        "DATA_QUALITY_MCP_OAUTH_OWNER": OWNER,
    }
    configured = read_oauth_settings(environ)
    assert configured is not None
    check = OAuthTokenCheck(configured, cache=JwksCache("x", fetch=FakeFetcher()))
    app = create_http_app(token=None, oauth=check)
    with TestClient(app, base_url="https://dq.example.com") as c:
        assert c.get(METADATA_PATH).json()["resource"] == configured.resource
        token = mint(aud="https://dq.example.com/mcp")
        assert post_mcp(c, token).status_code == 200


def test_an_explicit_allowed_hosts_list_replaces_the_default():
    with serve(allowed_hosts=["mcp.example.com"]) as c:
        assert post_mcp(c, mint()).status_code == 421
        headers = {"Host": "mcp.example.com"}
        assert post_mcp(c, mint(), headers=headers).status_code == 200


def test_a_request_with_an_origin_is_refused(client):
    response = post_mcp(client, mint(), headers={"Origin": "https://claude.ai"})
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/health", METADATA_PATH])
def test_open_routes_are_outside_the_host_check(client, path):
    assert client.get(path, headers={"Host": "evil.example"}).status_code == 200


# --- the request log (D3) ---


def test_requests_are_logged_once_and_never_with_the_token(client, caplog):
    caplog.set_level(logging.INFO)
    token = mint()
    post_mcp(client, token)
    post_mcp(client, "made-up-token")
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "data_quality_mcp.http" and "'/mcp'" in r.getMessage()
    ]
    assert lines == ["'POST' '/mcp' 200", "'POST' '/mcp' 401"]
    assert token not in caplog.text
    assert "made-up-token" not in caplog.text
    assert OWNER not in caplog.text


def test_websockets_are_refused_in_oauth_mode(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/mcp"):
        pass
