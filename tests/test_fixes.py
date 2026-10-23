"""Regression tests for issues the agent itself reported while writing a brief:
adsb.lol rate limits, months-old EONET volcanoes, and multi-word headline search."""

import pytest

from osintmap import layers, panels
from osintmap.http import UpstreamError


# --- adsb.lol gate ---------------------------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 2))
        self.now += seconds


@pytest.fixture
def gate(monkeypatch):
    monkeypatch.setattr(layers, "_adsb_last", [0.0])
    return FakeClock()


def test_adsb_requests_are_spaced_out(monkeypatch, gate):
    monkeypatch.setattr(layers.http, "get_json", lambda url, source: {"ac": []})
    layers._adsb_get("https://x/1", sleep=gate.sleep, clock=gate)
    gate.now += 0.5
    layers._adsb_get("https://x/2", sleep=gate.sleep, clock=gate)
    assert gate.slept == [0.7]  # the second call waited out the rest of the 1.2 s gap


def test_adsb_retries_once_after_a_429(monkeypatch, gate):
    calls = []

    def flaky(url, source):
        calls.append(url)
        if len(calls) == 1:
            raise UpstreamError(429, "slow down")
        return {"ac": []}

    monkeypatch.setattr(layers.http, "get_json", flaky)
    assert layers._adsb_get("https://x/mil", sleep=gate.sleep, clock=gate) == {"ac": []}
    assert len(calls) == 2
    assert 2.5 in gate.slept


def test_adsb_gives_up_after_a_second_429_and_never_retries_other_errors(monkeypatch, gate):
    calls = []

    def limited(url, source):
        calls.append(url)
        raise UpstreamError(429, "slow down")

    monkeypatch.setattr(layers.http, "get_json", limited)
    with pytest.raises(UpstreamError) as err:
        layers._adsb_get("https://x/mil", sleep=gate.sleep, clock=gate)
    assert err.value.status == 429 and len(calls) == 2

    calls.clear()

    def down(url, source):
        calls.append(url)
        raise UpstreamError(503, "down")

    monkeypatch.setattr(layers.http, "get_json", down)
    with pytest.raises(UpstreamError):
        layers._adsb_get("https://x/mil", sleep=gate.sleep, clock=gate)
    assert len(calls) == 1


# --- EONET window ----------------------------------------------------------------------------------


def test_eonet_asks_only_for_recently_observed_events(monkeypatch):
    seen = {}

    def fake(url, source, params=None, timeout=None):
        seen.update(params or {})
        return {"events": []}

    monkeypatch.setattr(layers.http, "get_json", fake)
    layers._eonet()
    assert seen["status"] == "open"
    assert seen["days"] == layers.EONET_DAYS == 30


# --- Headline search -------------------------------------------------------------------------------

HEADLINES = [
    {"title": "Earthquake strikes Tibet, dozens injured", "summary": "A magnitude 7.0 quake hit Nagqu."},
    {"title": "Typhoon Champi nears Taiwan", "summary": "Super typhoon with winds of 296 km/h."},
    {"title": "Tibet earthquake: rescue teams reach remote villages", "summary": None},
    {"title": "Markets slide as oil jumps", "summary": "Brent at a five-month high."},
]


def titles(entries):
    return [e["title"] for e in entries]


def test_multi_word_queries_match_every_word_in_any_order():
    assert titles(panels.search_headlines(HEADLINES, "tibet earthquake")) == [HEADLINES[0]["title"], HEADLINES[2]["title"]]
    assert titles(panels.search_headlines(HEADLINES, "earthquake in TIBET")) == [HEADLINES[0]["title"], HEADLINES[2]["title"]]
    # Summary words count too.
    assert titles(panels.search_headlines(HEADLINES, "typhoon taiwan winds")) == [HEADLINES[1]["title"]]


def test_without_a_full_match_the_best_partial_matches_come_first():
    found = titles(panels.search_headlines(HEADLINES, "taiwan oil sanctions"))
    assert set(found) == {HEADLINES[1]["title"], HEADLINES[3]["title"]}


def test_words_match_at_word_starts_and_empty_queries_return_everything():
    assert panels.search_headlines(HEADLINES, "ear") != []          # prefix of "earthquake"
    assert panels.search_headlines(HEADLINES, "quake") != []        # standalone word in a summary
    assert panels.search_headlines(HEADLINES, "arthquake") == []    # not mid-word
    assert panels.search_headlines(HEADLINES, "") == HEADLINES
    assert panels.search_headlines(HEADLINES, "the of and") == HEADLINES
