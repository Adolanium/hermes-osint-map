"""Shared HTTP client, caches and the SSRF guard."""

import threading
import time

import httpx
import pytest

from osintmap import http as net
from osintmap.http import SnapshotCache, TTLCache, UpstreamError


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# --- TTLCache --------------------------------------------------------------------------------------


def test_ttl_cache_serves_until_expiry_then_reloads():
    clock, calls = Clock(), []
    cache = TTLCache(60, clock=clock)
    load = lambda: calls.append(1) or len(calls)
    assert cache.get_or_load("k", load) == 1
    clock.t += 59
    assert cache.get_or_load("k", load) == 1
    clock.t += 2
    assert cache.get_or_load("k", load) == 2


def test_ttl_cache_does_not_store_failures():
    cache = TTLCache(60)
    with pytest.raises(UpstreamError):
        cache.get_or_load("k", lambda: (_ for _ in ()).throw(UpstreamError(503, "down")))
    assert cache.get_or_load("k", lambda: "fine") == "fine"


def test_ttl_cache_is_bounded_and_evicts_least_recently_used():
    cache = TTLCache(60, size=2)
    cache.get_or_load("a", lambda: 1)
    cache.get_or_load("b", lambda: 2)
    cache.get_or_load("a", lambda: 99)  # touch a
    cache.get_or_load("c", lambda: 3)  # evicts b
    assert cache.get_or_load("a", lambda: 99) == 1
    assert cache.get_or_load("b", lambda: 22) == 22


def test_ttl_cache_concurrent_callers_share_one_load():
    cache, calls, gate = TTLCache(60), [], threading.Event()

    def slow():
        calls.append(1)
        gate.wait(2)
        return "v"

    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get_or_load("k", slow))) for _ in range(8)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    gate.set()
    for t in threads:
        t.join()
    assert results == ["v"] * 8 and len(calls) == 1


# --- SnapshotCache ---------------------------------------------------------------------------------


def test_snapshot_ok_then_stale_keeps_the_old_value():
    clock = Clock()
    cache = SnapshotCache(clock=clock, wall_ms=lambda: 5)
    assert cache.peek("k")["status"] == "idle"
    first = cache.fetch("k", 10, lambda: [1, 2], source="USGS")
    assert first == {"value": [1, 2], "updated": 5, "status": "ok", "error": None}
    clock.t += 11

    def boom():
        raise UpstreamError(503, "USGS is having trouble (HTTP 503).")

    second = cache.fetch("k", 10, boom, source="USGS")
    assert second["status"] == "stale" and second["value"] == [1, 2] and "HTTP 503" in second["error"]


def test_snapshot_error_without_data_and_plain_sentence_for_unexpected_exceptions():
    cache = SnapshotCache()
    snap = cache.fetch("k", 10, lambda: 1 / 0, source="GDACS")
    assert snap["status"] == "error" and snap["value"] is None
    assert snap["error"] == "GDACS failed unexpectedly (ZeroDivisionError)."


def test_snapshot_rests_after_a_failure_instead_of_hammering():
    clock, calls = Clock(), []
    cache = SnapshotCache(clock=clock)

    def failing():
        calls.append(1)
        raise UpstreamError(503, "down")

    cache.fetch("k", 300, failing, retry_after=30)
    cache.fetch("k", 300, failing, retry_after=30)
    assert len(calls) == 1
    clock.t += 31
    cache.fetch("k", 300, failing, retry_after=30)
    assert len(calls) == 2


def test_snapshot_recovers_to_ok():
    clock = Clock()
    cache = SnapshotCache(clock=clock)
    cache.fetch("k", 10, lambda: (_ for _ in ()).throw(UpstreamError(503, "down")), retry_after=1)
    clock.t += 2
    assert cache.fetch("k", 10, lambda: "back", retry_after=1)["status"] == "ok"


def test_snapshot_cache_is_bounded():
    cache = SnapshotCache(size=3)
    for i in range(10):
        cache.fetch(f"k{i}", 10, lambda: "x")
    assert len(cache._entries) == 3


# --- HTTP client -----------------------------------------------------------------------------------


@pytest.fixture
def transport(monkeypatch):
    routes = {}

    def handler(request: httpx.Request):
        key = f"{request.url.host}{request.url.path}"
        reply = routes.get(key)
        if reply is None:
            return httpx.Response(404)
        if isinstance(reply, Exception):
            raise reply
        return reply(request) if callable(reply) else reply

    client = httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": net.USER_AGENT})
    monkeypatch.setattr(net, "_client", client)
    return routes


def test_get_json_sends_the_honest_user_agent(transport):
    seen = {}

    def reply(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, json={"a": 1})

    transport["api.example.org/x"] = reply
    assert net.get_json("https://api.example.org/x", source="Example") == {"a": 1}
    assert seen["ua"].startswith("hermes-osint-map/0.1 (+https://github.com/Adolanium/hermes-osint-map)")


@pytest.mark.parametrize("status,code,phrase", [(404, 404, "no record"), (429, 429, "rate limiting"),
                                                 (500, 503, "having trouble"), (403, 403, "refused"),
                                                 (418, 502, "HTTP 418")])
def test_status_codes_become_sentences(transport, status, code, phrase):
    transport["api.example.org/x"] = httpx.Response(status)
    with pytest.raises(UpstreamError) as err:
        net.get("https://api.example.org/x", source="Example")
    assert err.value.status == code and phrase in str(err.value)


def test_errors_never_include_the_url_or_its_secrets(transport):
    transport["api.example.org/x"] = httpx.ConnectError("boom https://api.example.org/x?apikey=SECRET")
    with pytest.raises(UpstreamError) as err:
        net.get("https://api.example.org/x", source="Example", params={"apikey": "SECRET"})
    assert "SECRET" not in str(err.value) and err.value.status == 503
    transport["api.example.org/x"] = httpx.ReadTimeout("slow")
    with pytest.raises(UpstreamError) as err:
        net.get("https://api.example.org/x", source="Example")
    assert err.value.status == 504 and str(err.value) == "Example took too long to answer."


def test_non_json_and_oversized_bodies_are_refused(transport):
    transport["api.example.org/html"] = httpx.Response(200, text="<html>")
    with pytest.raises(UpstreamError, match="not JSON"):
        net.get_json("https://api.example.org/html", source="Example")
    transport["api.example.org/big"] = httpx.Response(200, content=b"x" * 2000)
    with pytest.raises(UpstreamError, match="more data"):
        net.get("https://api.example.org/big", source="Example", max_bytes=1000)


def test_redirects_are_followed_only_when_allowed(transport):
    transport["a.example.org/start"] = httpx.Response(302, headers={"location": "https://b.example.org/end"})
    transport["b.example.org/end"] = httpx.Response(200, text="done")
    with pytest.raises(UpstreamError, match="redirected too many"):
        net.get("https://a.example.org/start", source="Example")
    assert net.get("https://a.example.org/start", source="Example", redirects=2).text() == "done"


def test_guarded_redirects_refuse_private_targets(transport):
    transport["rdap.example.org/ip"] = httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
    with pytest.raises(UpstreamError) as err:
        net.get("https://rdap.example.org/ip", source="RDAP", redirects=5, guard_redirects=True)
    assert err.value.status == 403 and "link-local" in str(err.value)


# --- SSRF guard ------------------------------------------------------------------------------------


@pytest.mark.parametrize("address,reason", [
    ("127.0.0.1", "loopback"), ("127.8.9.10", "loopback"), ("10.0.0.1", "private"), ("172.16.5.4", "private"),
    ("192.168.1.1", "private"), ("169.254.169.254", "link-local"), ("100.64.0.1", "carrier-grade"),
    ("100.127.255.254", "carrier-grade"), ("0.0.0.0", "unspecified"), ("224.0.0.1", "multicast"),
    ("240.0.0.1", "reserved"), ("198.18.0.1", "benchmarking"), ("192.0.2.1", "private"),
    ("::1", "loopback"), ("fe80::1", "link-local"), ("fc00::1", "private"), ("fd12:3456::1", "private"),
    ("ff02::1", "multicast"), ("::", "unspecified"), ("fec0::1", "site-local"),
    ("::ffff:127.0.0.1", "loopback"), ("::ffff:10.0.0.1", "private"), ("::ffff:169.254.169.254", "link-local"),
    ("2002:7f00:1::1", "loopback"), ("64:ff9b::a00:1", "private"), ("2001:db8::1", "private"),
])
def test_blocked_addresses(address, reason):
    found = net.blocked_reason(address)
    assert found is not None and reason in found


@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8", "64:ff9b::808:808"])
def test_public_addresses_pass(address):
    assert net.blocked_reason(address) is None


def test_resolve_public_refuses_a_name_with_any_private_answer():
    resolver = lambda host, port: ["93.184.215.14", "10.0.0.7"]
    with pytest.raises(UpstreamError) as err:
        net.resolve_public("mixed.example.com", 443, resolver)
    assert err.value.status == 403 and "10.0.0.7" in str(err.value)
    assert net.resolve_public("ok.example.com", 443, lambda h, p: ["93.184.215.14"]) == ["93.184.215.14"]


def test_resolve_public_handles_literals_and_dns_failures():
    with pytest.raises(UpstreamError, match="loopback"):
        net.resolve_public("::ffff:127.0.0.1", 80)

    def nxdomain(host, port):
        raise OSError("nope")

    with pytest.raises(UpstreamError) as err:
        net.resolve_public("gone.example.com", 443, nxdomain)
    assert err.value.status == 404


def test_open_guarded_connects_to_the_checked_address(monkeypatch):
    import socket

    seen = []
    monkeypatch.setattr(socket, "create_connection", lambda address, timeout: seen.append(address) or "sock")
    sock, address = net.open_guarded("ok.example.com", 443, 5, resolver=lambda h, p: ["93.184.215.14"])
    assert (sock, address) == ("sock", "93.184.215.14") and seen == [("93.184.215.14", 443)]


@pytest.mark.parametrize("text,expected", [
    ("Example.COM.", "example.com"), ("sub.example.co.uk", "sub.example.co.uk"), ("bücher.de", "xn--bcher-kva.de"),
    ("localhost", None), ("127.0.0.1", None), ("example.com:443", None), ("http://example.com", None),
    ("exa mple.com", None), ("-bad.com", None), ("a..b.com", None), ("x" * 64 + ".com", None), ("", None),
])
def test_parse_domain(text, expected):
    assert net.parse_domain(text) == expected
