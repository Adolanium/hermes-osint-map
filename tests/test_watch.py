"""Watches and the shared area filter: baselines, new-item detection, rules, state bounds, the
cron prompt, and the map-card directive the tools hint at."""

import json

import pytest

from osintmap import area as areas
from osintmap import layers, tools, watch
from osintmap.http import UpstreamError


class MemoryState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return json.loads(json.dumps(self.data.get(key, default)))

    def set(self, key, value):
        self.data[key] = json.loads(json.dumps(value))


def quake(key, mag, lat=24.0, lon=121.5, severity=0.6):
    return {"id": f"quakes:{key}", "layer": "quakes", "kind": "earthquake", "lat": lat, "lon": lon,
            "title": f"M{mag} near Hualien", "subtitle": None, "severity": severity, "time": 1793880000000,
            "country": "TW", "url": f"https://earthquake.usgs.gov/{key}", "props": [["Magnitude", f"{mag} mww"]]}


def disaster(key, level, lat=24.2, lon=121.0):
    return {"id": f"disasters:{key}", "layer": "disasters", "kind": "flood", "lat": lat, "lon": lon,
            "title": f"Flood {key}", "subtitle": None, "severity": 0.5, "time": None, "country": "TW", "url": None,
            "props": [["Alert level", level.capitalize()]]}


def jet(key, kind="military", lat=23.5, lon=120.0):
    return {"id": f"aircraft:{key}", "layer": "aircraft", "kind": kind, "lat": lat, "lon": lon, "title": key.upper(),
            "subtitle": None, "severity": 0.3, "time": None, "country": None, "url": None, "props": []}


@pytest.fixture
def world(monkeypatch):
    data = {"quakes": [quake("a", 5.1)], "disasters": [disaster("x", "green")], "aircraft": [jet("rch1")]}

    def fetch(layer_id, query=None):
        items = data.get(layer_id, [])
        return {"id": layer_id, "status": "ok", "error": None, "updated": 1, "count": len(items), "items": items}

    monkeypatch.setattr(layers, "fetch", fetch)
    return data


AREA = {"lat": 23.7, "lon": 121.0, "radius_km": 300, "label": "Taiwan"}


def test_first_check_is_a_silent_baseline(world):
    state = MemoryState()
    answer = watch.check(state, "taiwan", AREA)
    assert answer["baseline"] and answer["new"] == 0 and answer["text"].startswith(watch.NO_NEW)
    assert answer["count"] == 2  # the M5.1 quake and the military jet; the green flood is under the bar
    assert state.data["watches"]["taiwan"]["label"] == "Taiwan"


def test_later_checks_report_only_new_items_that_pass_the_rules(world):
    state = MemoryState()
    watch.check(state, "taiwan", AREA)
    assert watch.check(state, "taiwan", AREA)["text"].startswith(watch.NO_NEW)
    world["quakes"].append(quake("b", 6.2, lat=24.1, lon=121.6))
    world["quakes"].append(quake("small", 3.0))
    world["disasters"].append(disaster("y", "red"))
    world["aircraft"].append(jet("airliner", kind="airliner"))
    answer = watch.check(state, "taiwan", AREA)
    assert answer["new"] == 2 and not answer["text"].startswith(watch.NO_NEW)
    assert "M6.2" in answer["text"] and "Flood y" in answer["text"] and "km from the watch centre" in answer["text"]
    assert "AIRLINER" not in answer["text"] and "M3.0" not in answer["text"]
    record = state.data["watch:taiwan"]
    assert record["alerts"][0]["count"] == 2 and record["checks"] == 3
    assert watch.check(state, "taiwan", AREA)["new"] == 0


def test_items_outside_the_radius_never_alert(world):
    state = MemoryState()
    watch.check(state, "taiwan", AREA)
    world["quakes"].append(quake("tokyo", 7.0, lat=35.6, lon=139.7))
    assert watch.check(state, "taiwan", AREA)["new"] == 0


def test_rules_and_ids_are_validated(world):
    state = MemoryState()
    with pytest.raises(UpstreamError):
        watch.check(state, "Bad Id!", AREA)
    with pytest.raises(UpstreamError):
        watch.check(state, "nowhere", {"label": "x"})
    with pytest.raises(UpstreamError):
        watch.check(state, "taiwan", {**AREA, "min_alert_level": "purple"})
    with pytest.raises(UpstreamError):
        watch.check(state, "taiwan", {**AREA, "layers": ["volcanoes"]})


def test_seen_ids_and_alerts_stay_bounded(world, monkeypatch):
    monkeypatch.setattr(watch, "SEEN_CAP", 5)
    monkeypatch.setattr(watch, "ALERT_CAP", 2)
    state = MemoryState()
    watch.check(state, "taiwan", AREA)
    for n in range(6):
        world["quakes"].append(quake(f"n{n}", 5.5))
        watch.check(state, "taiwan", AREA)
    record = state.data["watch:taiwan"]
    assert len(record["seen"]) == 5 and len(record["alerts"]) == 2


def test_listing_and_forget(world):
    state = MemoryState()
    watch.check(state, "taiwan", AREA)
    watch.check(state, "japan", {"country": "JP", "label": "Japan"})
    assert {w["id"] for w in watch.listing(state)} == {"taiwan", "japan"}
    assert watch.forget(state, "taiwan")
    assert [w["id"] for w in watch.listing(state)] == ["japan"]
    assert not watch.forget(state, "taiwan")


def test_job_prompt_tells_the_job_to_stay_silent():
    prompt = watch.job_prompt("taiwan", {"lat": 23.7, "lon": 121.0, "radius_km": 300, "layers": ["quakes"]}, "Taiwan")
    assert 'watch_id="taiwan"' in prompt and "lat=23.7" in prompt and "layers=quakes" in prompt
    assert "[SILENT]" in prompt and watch.NO_NEW in prompt


def test_watch_tool_needs_state_and_answers_json(world, monkeypatch):
    monkeypatch.setattr(tools, "_state", None)
    assert json.loads(tools.HANDLERS["osint_watch"]({"watch_id": "taiwan", **AREA}))["ok"] is False
    tools.bind_state(MemoryState())
    answer = json.loads(tools.HANDLERS["osint_watch"]({"watch_id": "taiwan", **AREA}))
    assert answer["ok"] and answer["baseline"] is True
    assert json.loads(tools.HANDLERS["osint_watch"]({"watch_id": "x"}))["ok"] is False


def test_card_directive_and_hint():
    point = areas.parse({"lat": 23.7, "lon": 121, "radius_km": 300, "layers": "quakes,aircraft"})
    directive = areas.card_directive(point, 'Taiwan "Strait"')
    assert directive == '::osint-map{lat="23.700" lon="121.000" radius="300" layers="quakes,aircraft" title="Taiwan Strait"}'
    assert areas.card_directive(areas.Area(country="JP")) == '::osint-map{country="JP"}'
    assert areas.card_hint(areas.Area()) == ""
    assert "never in messaging platforms" in areas.card_hint(areas.Area(country="JP"))


def test_events_text_carries_the_map_card_hint(world):
    answer = json.loads(tools.HANDLERS["osint_events"]({"lat": 23.7, "lon": 121.0, "radius_km": 300}))
    assert answer["ok"] and "::osint-map{lat=" in answer["text"]
    worldwide = json.loads(tools.HANDLERS["osint_events"]({}))
    assert "::osint-map" not in worldwide["text"]


def test_magnitude_and_alert_level_parsing():
    assert areas.magnitude(quake("a", 5.6)) == 5.6
    assert areas.magnitude({"props": [["Magnitude", "unknown"]]}) is None
    assert areas.alert_level(disaster("x", "orange")) == "orange"
