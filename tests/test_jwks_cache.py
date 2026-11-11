"""The signing-key cache and the JWKS fetch (planning/milestone-4.5.md, D3, D9).

A fake fetcher (which can fail, or block until released) and a fake clock
drive the cache; the real fetch runs against a local HTTP server.
"""

import http.server
import json
import logging
import threading
import time
import urllib.error
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

import pytest
from oauth_helpers import (
    KID,
    OTHER_KEY,
    FakeClock,
    FakeFetcher,
    jwk,
    key_set,
    mint,
    settings,
)

from data_quality_mcp import http_auth
from data_quality_mcp.http_auth import (
    FETCH_BACKOFF,
    JWKS_TTL,
    JwksCache,
    JwksError,
    OAuthTokenCheck,
    _fetch_jwks,
)

URL = "https://example-tenant.authkit.app/oauth2/jwks"


def make_cache(fetcher: FakeFetcher, clock: FakeClock | None = None) -> JwksCache:
    return JwksCache(URL, fetch=fetcher, clock=clock or FakeClock())


def fetch_failures(caplog) -> list[str]:
    messages = [record.getMessage() for record in caplog.records]
    return [message for message in messages if "JWKS fetch failed" in message]


# --- freshness and refreshing ---


def test_the_set_is_fetched_once_while_fresh():
    fetcher, clock = FakeFetcher(), FakeClock()
    cache = make_cache(fetcher, clock)
    assert cache.get_key(KID) is not None
    clock.advance(JWKS_TTL - 1)
    assert cache.get_key(KID) is not None
    assert fetcher.calls == 1
    clock.advance(1)
    assert cache.get_key(KID) is not None
    assert fetcher.calls == 2


def test_parallel_checks_at_expiry_share_one_fetch_and_never_wait():
    clock = FakeClock()
    fetcher = FakeFetcher()
    cache = make_cache(fetcher, clock)
    old = cache.get_key(KID)
    clock.advance(JWKS_TTL + 1)
    fetcher._gate.clear()  # the refresh now blocks until released
    fetcher.started.clear()

    with ThreadPoolExecutor(max_workers=1) as refresher:
        refreshing = refresher.submit(cache.get_key, KID)
        assert fetcher.started.wait(5)
        # Others with a known kid go ahead with the old key at once
        with ThreadPoolExecutor(max_workers=8) as pool:
            started = time.monotonic()
            keys = list(pool.map(cache.get_key, [KID] * 8))
            assert time.monotonic() - started < 1
        assert all(key is old for key in keys)
        fetcher.release()
        assert refreshing.result(5) is not None
    assert fetcher.calls == 2


def test_a_check_with_no_key_waits_for_the_fetch_in_flight():
    fetcher = FakeFetcher(blocked=True)
    cache = make_cache(fetcher)
    with ThreadPoolExecutor(max_workers=4) as pool:
        first = pool.submit(cache.get_key, KID)
        assert fetcher.started.wait(5)
        waiting = [pool.submit(cache.get_key, KID) for _ in range(3)]
        time.sleep(0.1)
        assert not any(future.done() for future in waiting)
        fetcher.release()
        assert first.result(5) is not None
        assert all(future.result(5) is not None for future in waiting)
    assert fetcher.calls == 1


def test_waiting_ends_after_the_fetch_timeout(monkeypatch):
    monkeypatch.setattr(http_auth, "FETCH_TIMEOUT", 0.2)
    fetcher = FakeFetcher(blocked=True)
    cache = make_cache(fetcher)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cache.get_key, KID)
        assert fetcher.started.wait(5)
        started = time.monotonic()
        assert cache.get_key(KID) is None
        assert time.monotonic() - started < 2
        fetcher.release()
        first.result(5)


# --- failures ---


def test_a_failed_refresh_keeps_the_last_good_set(caplog):
    fetcher, clock = FakeFetcher(), FakeClock()
    cache = make_cache(fetcher, clock)
    old = cache.get_key(KID)
    clock.advance(JWKS_TTL + 1)
    fetcher.result = OSError("unreachable")
    assert cache.get_key(KID) is old  # stale if error
    assert fetcher.calls == 2
    assert fetch_failures(caplog) == [
        "JWKS fetch failed (OSError); keeping the last good key set, "
        f"next attempt in {FETCH_BACKOFF} s"
    ]


def test_with_no_set_failures_back_off(caplog):
    fetcher, clock = FakeFetcher(OSError("down")), FakeClock()
    cache = make_cache(fetcher, clock)
    assert cache.get_key(KID) is None
    for _ in range(FETCH_BACKOFF - 1):
        clock.advance(1)
        assert cache.get_key(KID) is None  # inside the window: at once, no fetch
    assert fetcher.calls == 1
    clock.advance(1)
    assert cache.get_key(KID) is None
    assert fetcher.calls == 2
    assert len(fetch_failures(caplog)) == 2  # one line per fetch, not per check
    fetcher.result = key_set()
    clock.advance(FETCH_BACKOFF)
    assert cache.get_key(KID) is not None


def test_with_an_expired_set_failures_back_off_too():
    fetcher, clock = FakeFetcher(), FakeClock()
    cache = make_cache(fetcher, clock)
    old = cache.get_key(KID)
    clock.advance(JWKS_TTL)
    fetcher.result = OSError("down")
    for _ in range(10):
        assert cache.get_key(KID) is old
        assert cache.get_key("unknown") is None
        clock.advance(2)
    assert fetcher.calls == 2  # the first refresh only: 20 s < the backoff
    clock.advance(FETCH_BACKOFF)
    assert cache.get_key(KID) is old
    assert fetcher.calls == 3


def test_unknown_kids_refresh_at_most_once_per_backoff_window():
    fetcher, clock = FakeFetcher(), FakeClock()
    cache = make_cache(fetcher, clock)
    assert cache.get_key(KID) is not None
    for _ in range(10):
        clock.advance(1)
        assert cache.get_key(f"random-{clock.now}") is None
    assert fetcher.calls == 1
    clock.advance(FETCH_BACKOFF)
    assert cache.get_key("rotated-in") is None
    assert fetcher.calls == 2


def test_a_rotated_key_is_found_by_its_refresh():
    fetcher, clock = FakeFetcher(), FakeClock()
    cache = make_cache(fetcher, clock)
    cache.get_key(KID)
    fetcher.result = key_set(jwk(), jwk(OTHER_KEY, kid="key-2"))
    clock.advance(FETCH_BACKOFF)
    assert cache.get_key("key-2") is not None


def test_only_rs256_signing_keys_with_a_kid_are_kept():
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt.algorithms import ECAlgorithm

    ec_key = ECAlgorithm.to_jwk(
        ec.generate_private_key(ec.SECP256R1()).public_key(), as_dict=True
    )
    fetcher = FakeFetcher(
        key_set(
            jwk(),
            jwk(kid="rs512", alg="RS512"),
            jwk(kid="encryption", use="enc"),
            jwk(kid=None),
            dict(ec_key, kid="ec"),
        )
    )
    cache = make_cache(fetcher)
    assert cache.get_key(KID) is not None
    for kid in ("rs512", "encryption", "ec"):
        assert cache.get_key(kid) is None


def test_prechecks_never_reach_the_fetcher():
    fetcher = FakeFetcher()
    check = OAuthTokenCheck(settings(), cache=make_cache(fetcher))
    for token in (
        mint(iss="https://evil.authkit.app"),
        mint(aud="https://other.example/mcp"),
        mint(sub="user_SOMEONE_ELSE"),
        mint(kid=None),
        "garbage",
    ):
        assert check.check(token) is None
    assert fetcher.calls == 0


# --- the real fetch ---


class _JwksHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/ok":
            self._send(200, json.dumps({"keys": [jwk()]}).encode())
        elif self.path == "/empty":
            self._send(204, b"")
        elif self.path == "/big":
            self._send(200, b" " * (http_auth.MAX_JWKS_BYTES + 10))
        elif self.path == "/bad":
            self._send(200, b"not json")
        else:
            self._send(404, b"{}")

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *args) -> None:  # keep test output quiet
        pass


@pytest.fixture
def jwks_server() -> Iterator[str]:
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _JwksHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_the_fetch_reads_a_key_set(jwks_server):
    keys = _fetch_jwks(jwks_server + "/ok", 5)
    assert [key.key_id for key in keys.keys] == [KID]


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("/missing", urllib.error.HTTPError),
        ("/empty", JwksError),
        ("/big", JwksError),
        ("/bad", json.JSONDecodeError),
    ],
)
def test_a_bad_response_is_a_failed_fetch(jwks_server, path, error):
    with pytest.raises(error):
        _fetch_jwks(jwks_server + path, 5)


def test_the_cache_survives_a_real_failed_fetch(jwks_server, caplog):
    caplog.set_level(logging.WARNING)
    cache = JwksCache(jwks_server + "/missing", clock=FakeClock())
    assert cache.get_key(KID) is None
    assert fetch_failures(caplog) == [
        "JWKS fetch failed (HTTPError); keeping no keys, "
        f"next attempt in {FETCH_BACKOFF} s"
    ]
