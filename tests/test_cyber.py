"""Feed parsing, validation, buffering and the shared upstream connection."""

import json
import threading
import time
from contextlib import contextmanager

import pytest

from osintmap.checkpoint import TTLCache, parse_country, parse_top
from osintmap.cyberfeed import (
    EventBuffer,
    FeedHub,
    SSEParser,
    backoff_delay,
    normalize_attack,
    normalize_counter,
)
from osintmap.http import UpstreamError

# A real event and counter as captured from the feed on 2026-10-22, in the field order the
# server uses (data before event).
ATTACK = {"a_c": 2, "a_n": "NULL Encoding detected within a HTTP request", "a_t": "exploit", "d_co": "BR",
          "d_la": -15.597, "d_lo": -56.0958, "d_s": "MT", "s_co": "US", "s_lo": -97.822, "s_la": 37.751,
          "s_s": None, "t": None}
COUNTER = {"recentPeriod": [16473787, 15266108, 16521559], "today": 9124259}
WIRE = (
    f"data:{json.dumps(COUNTER)}\nevent:counter\nretry:10000\n\n"
    ":keep-alive\nretry:10000\n\n"
    f"event:attack\ndata:{json.dumps(ATTACK)}\nretry:10000\n\n"
).encode()


# --- SSE ---------------------------------------------------------------------------------------


def parse_all(chunks):
    parser = SSEParser()
    return [event for chunk in chunks for event in parser.feed(chunk)], parser


def test_parses_the_real_wire_format_with_data_before_event():
    events, parser = parse_all([WIRE])
    assert [kind for kind, _ in events] == ["counter", "attack"]
    assert json.loads(events[1][1]) == ATTACK
    assert parser.retry_ms == 10000


@pytest.mark.parametrize("size", [1, 2, 3, 7, 64])
def test_chunk_boundaries_anywhere_give_the_same_events(size):
    whole, _ = parse_all([WIRE])
    pieces, _ = parse_all([WIRE[i : i + size] for i in range(0, len(WIRE), size)])
    assert pieces == whole


def test_crlf_and_cr_line_ends_and_split_crlf():
    crlf = WIRE.replace(b"\n", b"\r\n")
    assert parse_all([crlf])[0] == parse_all([WIRE])[0]
    assert parse_all([crlf[:5], crlf[5:]])[0] == parse_all([WIRE])[0]
    cut = crlf.index(b"\r\n") + 1  # CR at the end of one chunk, LF at the start of the next
    assert parse_all([crlf[:cut], crlf[cut:]])[0] == parse_all([WIRE])[0]
    # A trailing CR could be half a CRLF, so CR-only streams dispatch on the next byte (keep-alives).
    assert parse_all([WIRE.replace(b"\n", b"\r") + b":"])[0] == parse_all([WIRE])[0]


def test_multiline_data_comments_and_default_event_type():
    events, _ = parse_all([b": hello\ndata: one\ndata:two\n\ndata\n\n"])
    assert events == [("message", "one\ntwo"), ("message", "")]


def test_events_without_data_are_not_dispatched():
    assert parse_all([b"event:attack\n\n:keep-alive\n\n"])[0] == []


def test_oversized_events_are_dropped_and_counted():
    parser = SSEParser(max_event_bytes=100)
    big = b"event:attack\ndata:" + b"x" * 500 + b"\n\nevent:attack\ndata:ok\n\n"
    assert list(parser.feed(big)) == [("attack", "ok")]
    assert parser.oversized == 1


def test_a_line_that_never_ends_does_not_grow_the_buffer_forever():
    parser = SSEParser(max_event_bytes=100)
    for _ in range(50):
        assert list(parser.feed(b"y" * 1000)) == []
    assert len(parser._buffer) <= 1000
    assert list(parser.feed(b"\n\nevent:attack\ndata:ok\n\n")) == [("attack", "ok")]


# --- Validation ---------------------------------------------------------------------------------


def test_normalizes_a_real_attack():
    event = normalize_attack(json.dumps(ATTACK))
    assert event == {
        "type": "exploit",
        "name": "NULL Encoding detected within a HTTP request",
        "count": 2,
        "src": {"country": "US", "lat": 37.751, "lon": -97.822, "region": None, "city": None},
        "dst": {"country": "BR", "lat": -15.597, "lon": -56.0958, "region": "MT", "city": None},
        "source_time": None,
    }


@pytest.mark.parametrize("data", ["", "not json", "[]", "null", "42", '"text"', "{" * 5000])
def test_rejects_payloads_that_are_not_objects(data):
    assert normalize_attack(data) is None


def test_missing_coordinates_keep_the_country_so_the_page_can_place_it():
    raw = {**ATTACK, "s_la": None, "s_lo": "nope", "d_la": 95, "d_lo": 10}
    event = normalize_attack(json.dumps(raw))
    assert event["src"] == {"country": "US", "lat": None, "lon": None, "region": None, "city": None}
    assert event["dst"]["lat"] is None and event["dst"]["country"] == "BR"


def test_zero_zero_is_treated_as_no_fix():
    event = normalize_attack(json.dumps({**ATTACK, "d_la": 0, "d_lo": 0}))
    assert event["dst"]["lat"] is None


def test_an_end_with_neither_country_nor_coordinates_is_unusable():
    assert normalize_attack(json.dumps({**ATTACK, "d_co": None, "d_la": None, "d_lo": None})) is None
    assert normalize_attack(json.dumps({**ATTACK, "s_co": "??", "s_la": None, "s_lo": None})) is None


def test_bad_fields_degrade_to_safe_defaults():
    raw = {**ATTACK, "a_t": "Ransomware", "a_n": "  ", "a_c": "-3", "s_co": "us", "d_s": "x" * 500, "t": True}
    event = normalize_attack(json.dumps(raw))
    assert event["type"] == "other"
    assert event["name"] == "Unnamed attack"
    assert event["count"] == 1
    assert event["src"]["country"] == "US"
    assert len(event["dst"]["region"]) == 80
    assert event["source_time"] is None


def test_names_are_whitespace_collapsed_and_capped():
    event = normalize_attack(json.dumps({**ATTACK, "a_n": "a\n\tb  " + "c" * 400}))
    assert event["name"].startswith("a b c") and len(event["name"]) == 160


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1_700_000_000, 1_700_000_000_000), (1_700_000_000_123, 1_700_000_000_123),
     ("2026-10-22T13:38:47Z", 1792676327000), ("garbage", None), (-5, None)],
)
def test_source_time_formats(value, expected):
    assert normalize_attack(json.dumps({**ATTACK, "t": value}))["source_time"] == expected


def test_counter_validation():
    assert normalize_counter(json.dumps(COUNTER)) == {"today": 9124259, "recent": [16473787, 15266108, 16521559]}
    assert normalize_counter(json.dumps({"today": "12"})) == {"today": 12, "recent": []}
    assert normalize_counter(json.dumps({"today": 5, "recentPeriod": [1, "x"]})) == {"today": 5, "recent": []}
    assert normalize_counter(json.dumps({"other": 1})) is None
    assert normalize_counter("[1,2]") is None
    assert len(normalize_counter(json.dumps({"recentPeriod": list(range(500))}))["recent"]) == 62


# --- Buffer -------------------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_event(name="x", country="US"):
    return normalize_attack(json.dumps({**ATTACK, "a_n": name, "d_co": country}))


def test_buffer_is_bounded_and_reports_gaps():
    buffer = EventBuffer(size=5, clock=Clock())
    for i in range(12):
        buffer.add(make_event(f"e{i}"), i)
    events, gap = buffer.after(0, 100)
    assert [e["seq"] for e in events] == [8, 9, 10, 11, 12]
    assert gap
    events, gap = buffer.after(9, 100)
    assert [e["seq"] for e in events] == [10, 11, 12] and not gap
    assert buffer.after(12, 100) == ([], False)


def test_buffer_limit_keeps_the_newest():
    buffer = EventBuffer(size=50, clock=Clock())
    for i in range(20):
        buffer.add(make_event(f"e{i}"), i)
    events, gap = buffer.after(0, 3)
    assert [e["seq"] for e in events] == [18, 19, 20] and gap


def test_identical_payloads_in_a_burst_are_marked_as_repeats():
    clock = Clock()
    buffer = EventBuffer(clock=clock)
    first = buffer.add(make_event("burst"), 0)
    second = buffer.add(make_event("burst"), 0)
    other = buffer.add(make_event("burst", country="DE"), 0)
    clock.now += 5
    later = buffer.add(make_event("burst"), 0)
    assert first["repeat_of"] is None
    assert second["repeat_of"] == first["seq"]
    assert other["repeat_of"] is None
    assert later["repeat_of"] is None


# --- Hub ----------------------------------------------------------------------------------------


class FakeUpstream:
    """Scripted connections: each item is a list of byte chunks, or an exception to raise."""

    def __init__(self, *script):
        self.script = list(script)
        self.opened = 0
        self.closed = 0
        self.release = threading.Event()

    @contextmanager
    def __call__(self):
        self.opened += 1
        step = self.script.pop(0) if self.script else "hold"
        if isinstance(step, Exception):
            raise step
        try:
            yield self._chunks(step)
        finally:
            self.closed += 1

    def _chunks(self, step):
        if step == "hold":
            while not self.release.wait(0.01):
                yield b":keep-alive\n\n"
            return
        yield from step


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def test_hub_streams_events_to_pollers_and_goes_idle():
    upstream = FakeUpstream([WIRE])
    hub = FeedHub(upstream, idle_after=0.3, backoff=lambda n: 0.01)
    first = hub.poll(0)
    assert first["state"] in {"connecting", "live"}
    wait_for(lambda: hub.status()["seq"] == 1)
    answer = hub.poll(0)
    assert [e["name"] for e in answer["events"]] == [ATTACK["a_n"]]
    assert answer["counter"]["today"] == 9124259
    assert hub.poll(answer["seq"])["events"] == []
    # The server closed the stream; nobody polls during its 10 s retry hint, so the hub gives up
    # waiting and the worker exits without reconnecting.
    wait_for(lambda: hub.status()["state"] == "idle")
    hub.join()
    assert hub._thread is None
    assert upstream.opened == upstream.closed == 1


def test_hub_closes_an_open_stream_when_pollers_leave():
    upstream = FakeUpstream("hold")
    hub = FeedHub(upstream, idle_after=0.2, backoff=lambda n: 0.01)
    hub.poll(0)
    wait_for(lambda: hub.status()["state"] == "live")
    wait_for(lambda: hub.status()["state"] == "idle" and upstream.closed == 1)
    hub.join()
    # A new poll opens a fresh connection.
    hub.poll(0)
    wait_for(lambda: upstream.opened == 2)
    hub.stop()
    upstream.release.set()
    hub.join()


def test_stop_followed_by_a_poll_never_reports_idle_while_streaming():
    # A page reload stops its old poller and starts a new one; the hub must not end up claiming
    # "idle" while its worker is still connected.
    upstream = FakeUpstream("hold")
    hub = FeedHub(upstream, idle_after=5, backoff=lambda n: 0.01)
    hub.poll(0)
    wait_for(lambda: hub.status()["state"] == "live")
    hub.stop()
    hub.poll(0)
    time.sleep(0.2)
    wait_for(lambda: hub.status()["state"] == "live")
    hub.stop()
    upstream.release.set()
    hub.join()
    assert hub.status()["state"] == "idle"


def test_hub_reconnects_after_errors_and_counts_them():
    upstream = FakeUpstream(ConnectionError("boom"), UpstreamError(503, "busy"), [WIRE], "hold")
    hub = FeedHub(upstream, idle_after=5, backoff=lambda n: 0.01)
    hub.poll(0)
    wait_for(lambda: hub.status()["seq"] == 1)
    status = hub.status()
    assert status["stats"]["reconnects"] >= 2
    assert upstream.opened >= 3
    hub.stop()
    upstream.release.set()
    hub.join()
    assert hub.status()["state"] == "idle"


def test_hub_reports_an_unavailable_endpoint_after_repeated_404s():
    upstream = FakeUpstream(*[UpstreamError(404, "gone")] * 3)
    hub = FeedHub(upstream, idle_after=5, backoff=lambda n: 0.01)
    hub.poll(0)
    wait_for(lambda: hub.status()["state"] == "unavailable")
    assert hub.status()["error"] == "gone"
    assert hub.status()["retry_at"] is not None
    hub.stop()
    hub.join()


def test_hub_drops_malformed_events_and_caps_the_rate():
    good = f"event:attack\ndata:{json.dumps(ATTACK)}\n\n".encode()
    bad = b"event:attack\ndata:{oops\n\nevent:counter\ndata:[]\n\n"
    upstream = FakeUpstream([bad + good * 10], "hold")
    hub = FeedHub(upstream, idle_after=5, max_per_second=4, backoff=lambda n: 0.01, clock=lambda: 7.0)
    hub.poll(0)
    wait_for(lambda: hub.status()["stats"]["rate_limited"] == 6)
    stats = hub.status()["stats"]
    assert stats["malformed"] == 2
    assert stats["received"] == 4
    hub.stop()
    upstream.release.set()
    hub.join()


def test_backoff_grows_and_caps():
    assert backoff_delay(1, rand=lambda: 1) == 2
    assert backoff_delay(3, rand=lambda: 1) == 8
    assert backoff_delay(30, rand=lambda: 1) == 60
    assert backoff_delay(30, rand=lambda: 0) == 30


# --- Check Point lookups ------------------------------------------------------------------------


def test_parse_country_reverses_to_chronological_and_rejects_mismatches():
    raw = {
        "trend": {"attacks": [3.0, 2.0, 1.0], "dates": ["2026-10-21", "2026-10-20", "2026-10-19"]},
        "malware_types": {"attacksByType": {"Botnet": [6.3, 6.1, 5.3], "Bad": "x"},
                          "dates": ["2026-10-21", "2026-10-20", "2026-10-19"]},
    }
    parsed = parse_country(raw)
    assert parsed["trend"] == {"dates": ["2026-10-19", "2026-10-20", "2026-10-21"], "values": [1.0, 2.0, 3.0]}
    assert parsed["malware_types"]["series"] == {"Botnet": [5.3, 6.1, 6.3]}
    assert parse_country({"trend": {"attacks": [1], "dates": ["a", "b"]}}) == {"trend": None, "malware_types": None}
    assert parse_country("nope") == {"trend": None, "malware_types": None}


def test_parse_top_filters_bad_rows():
    raw = {"countries": [{"id": "ET", "fraction": 1.0}, {"id": 5, "fraction": 0.5}, {"id": "NG", "fraction": 7}],
           "industries": "x"}
    assert parse_top(raw) == {"countries": [{"id": "ET", "fraction": 1.0}], "industries": [], "malwareTypes": []}


def test_ttl_cache_expires_and_does_not_cache_failures():
    clock = Clock()
    cache = TTLCache(10, clock=clock)
    calls = []
    load = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert cache.get_or_load("k", load) == 1
    assert cache.get_or_load("k", load) == 1
    clock.now += 11
    assert cache.get_or_load("k", load) == 2

    def fail():
        raise UpstreamError(503, "down")

    with pytest.raises(UpstreamError):
        cache.get_or_load("f", fail)
    assert cache.get_or_load("f", load) == 3
