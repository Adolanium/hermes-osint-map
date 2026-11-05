"""osint_show: scene validation, the shared queue, and the tool's answer."""

import json

import pytest

from osintmap import show, tools
from osintmap.http import UpstreamError


class MemoryState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return json.loads(json.dumps(self.data.get(key, default)))

    def set(self, key, value):
        self.data[key] = json.loads(json.dumps(value))


def test_a_scene_keeps_valid_marks_and_drops_the_rest():
    sc = show.scene({"caption": "  Red Sea   shipping ", "lat": 14.5, "lon": 42.5, "radius_km": 900,
                     "pins": [{"lat": 12.6, "lon": 43.4, "label": "Bab-el-Mandeb" * 9}] * 20,
                     "ring": {"lat": 14, "lon": 42, "radius_km": 400}, "layers_on": ["cyber", "volcanoes", "aircraft"],
                     "layers_off": ["aircraft", "quakes"], "vision": "nvg"})
    assert sc["caption"] == "Red Sea shipping" and sc["focus"]["radius_km"] == 900
    assert len(sc["pins"]) == show.MAX_PINS and len(sc["pins"][0]["label"]) == show.LABEL
    assert sc["layers_on"] == ["cyber", "aircraft"] and sc["layers_off"] == ["quakes"]
    assert show.scene({"caption": "x", "country": "ye"})["focus"] == {"country": "YE"}


@pytest.mark.parametrize("args", [
    {"lat": 1, "lon": 1},
    {"caption": "nowhere"},
    {"caption": "x", "lat": 95, "lon": 0},
    {"caption": "x", "country": "Yemen"},
    {"caption": "x", "country": "YE", "vision": "xray"},
    {"caption": "x", "pins": [{"lat": "a", "lon": 1, "label": "p"}]},
])
def test_bad_scenes_are_refused(args):
    with pytest.raises(UpstreamError):
        show.scene(args)


def test_scenes_queue_through_state_and_the_page_reads_only_newer_ones():
    state = MemoryState()
    first = show.show(state, {"caption": "One", "country": "JP"})
    second = show.show(state, {"caption": "Two", "country": "KR"})
    assert (first["seq"], second["seq"]) == (1, 2)
    assert show.latest(state, 0)["scene"]["caption"] == "Two"
    assert show.latest(state, 2)["scene"] is None and show.latest(state, 2)["seq"] == 2
    assert show.latest(MemoryState())["seq"] == 0


def test_the_tool_needs_state_and_describes_what_the_user_sees(monkeypatch):
    monkeypatch.setattr(tools, "_state", None)
    assert json.loads(tools.HANDLERS["osint_show"]({"caption": "x", "country": "YE"}))["ok"] is False
    tools.bind_state(MemoryState())
    answer = json.loads(tools.HANDLERS["osint_show"]({"caption": "Yemen", "country": "YE",
                                                      "pins": [{"lat": 15.35, "lon": 44.2, "label": "Sanaa"}]}))
    assert answer["ok"] and answer["seq"] == 1
    assert "On the OSINT Map now: Yemen." in answer["text"] and "Sanaa" in answer["text"]
    assert json.loads(tools.HANDLERS["osint_show"]({"caption": "x"}))["ok"] is False
