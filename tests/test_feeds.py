"""Operator feeds: the layer that draws the operator's own RSS/Atom sources and digest files.

Unit tests never reach the internet (see conftest): the RSS bodies here are fixtures, and the digest
files are written to tmp_path.
"""

import json

import pytest

from osintmap import feeds, layers
from osintmap.http import UpstreamError

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>World</title>
<item><title>Tanker traffic rerouted after strike near the Strait of Hormuz</title>
<link>https://example.com/a</link><description>Shipping rerouted.</description>
<pubDate>Mon, 09 Nov 2026 16:00:00 GMT</pubDate></item>
<item><title>Taiwan tightens chip export rules</title>
<link>https://example.com/b</link><description>Policy shift in Taipei.</description>
<pubDate>Mon, 09 Nov 2026 15:00:00 GMT</pubDate></item>
<item><title>A think piece about the future of work</title>
<link>https://example.com/c</link><description>No geography at all.</description></item>
</channel></rss>"""


@pytest.fixture(autouse=True)
def unconfigured(monkeypatch):
    """Every test starts with no operator feeds and no inherited reader."""
    monkeypatch.setattr(feeds, "_READER", None)
    monkeypatch.delenv(feeds.ENV, raising=False)
    layers.reset()


def configure(entries):
    feeds.use_settings(lambda key, default=None: entries if key == "feeds" else default)


# --- The setting ------------------------------------------------------------------------------------


def test_unset_setting_reports_disabled_with_a_sentence():
    assert feeds.specs() == []
    assert "No operator feeds are configured" in feeds.missing()
    entry = next(r for r in layers.registry() if r["id"] == "feeds")
    assert entry["status"] == "disabled"
    assert entry["needs_key"] == feeds.ENV
    assert layers.fetch("feeds") == {
        "id": "feeds", "count": 0, "status": "disabled", "error": feeds.missing(),
        "updated": None, "items": [],
    }


def test_malformed_setting_is_not_a_crash():
    feeds.use_settings(lambda key, default=None: "{not json")
    assert feeds.specs() == []
    assert "could not be read" in feeds.missing()
    feeds.use_settings(lambda key, default=None: {"sources": "nope"})
    assert feeds.specs() == []


def test_specs_accept_json_text_and_a_list():
    text = json.dumps([{"url": "https://example.com/world.xml"}])
    feeds.use_settings(lambda key, default=None: text)
    assert feeds.specs() == [{"kind": "rss", "url": "https://example.com/world.xml",
                              "label": "example.com", "ttl": feeds.DEFAULT_TTL}]
    configure([{"file": "~/digest.json", "label": "Morning", "country": "sa", "ttl": 5}])
    spec = feeds.specs()[0]
    assert spec["kind"] == "file" and spec["label"] == "Morning" and spec["country"] == "SA"
    assert spec["ttl"] == feeds.MIN_TTL  # clamped, never a 5 s hammer
    assert spec["file"].startswith("/")  # ~ expanded


def test_specs_skip_entries_without_a_usable_source_and_cap_the_count():
    configure([{"label": "nothing"}, {"url": "ftp://nope"}, "string", {"url": "https://a.example/f"}]
              + [{"url": f"https://h{i}.example/f"} for i in range(20)])
    specs = feeds.specs()
    assert len(specs) == feeds.MAX_SOURCES
    assert specs[0]["url"] == "https://a.example/f"


def test_ttl_defaults_and_ceilings():
    assert feeds._ttl(None) == feeds.DEFAULT_TTL
    assert feeds._ttl("nonsense") == feeds.DEFAULT_TTL
    assert feeds._ttl(10 ** 9) == feeds.MAX_TTL


# --- Placement --------------------------------------------------------------------------------------


def test_rss_items_land_on_a_chokepoint_or_a_country_and_the_rest_are_dropped():
    configure([{"url": "https://example.com/world.xml", "label": "Example"}])
    out = feeds.from_rss(RSS, feeds.specs()[0])
    assert out["dropped"] == 1  # "future of work" names no place
    hormuz, taiwan = out["items"]
    assert (round(hormuz["lat"], 2), round(hormuz["lon"], 2)) == (26.57, 56.25)
    assert hormuz["props"] == [["Source", "Example"], ["Anchor", "approx. - Strait of Hormuz"]]
    assert taiwan["lat"] is None and taiwan["lon"] is None  # the page places it from the gazetteer
    assert taiwan["country"] == "TW"
    assert taiwan["props"] == [["Source", "Example"], ["Anchor", "approx. - country label point"]]
    assert taiwan["time"] is not None and taiwan["url"] == "https://example.com/b"
    assert all(i["id"].startswith("feeds:") for i in out["items"])


def test_ids_are_stable_across_refreshes_and_unique_per_item():
    configure([{"url": "https://example.com/world.xml"}])
    first = feeds.from_rss(RSS, feeds.specs()[0])["items"]
    second = feeds.from_rss(RSS, feeds.specs()[0])["items"]
    assert [i["id"] for i in first] == [i["id"] for i in second]
    assert len({i["id"] for i in first}) == len(first)


def test_chokepoint_names_need_a_passage_not_a_country_word():
    from osintmap import chokepoints

    assert chokepoints.find("Traffic through Bab el-Mandeb slows")[0] == "bab-el-mandeb"
    assert chokepoints.find("Panama Canal drought")[0] == "panama"
    assert chokepoints.find("Panama held an election") is None


def test_country_names_are_matched_longest_first():
    from osintmap import geo

    assert geo.country_in("Fighting continues in the Democratic Republic of the Congo") == "CD"
    assert geo.country_in("Congo basin loggers") == "CG"
    assert geo.country_in("us and them") is None  # too short to be a country match
    assert geo.country_in("Albania's protests grow") == "AL"


# --- Digests ----------------------------------------------------------------------------------------


def test_digest_items_use_place_country_and_their_own_coordinates():
    configure([{"file": "~/digest.json", "label": "Digest"}])
    doc = {"version": 1, "items": [
        {"title": "Aramco tower targeted", "country": "SA", "severity": 0.8, "tag": "strike"},
        {"title": "Red Sea traffic reroutes", "place": "Bab-el-Mandeb"},
        {"title": "Explosion at a port", "lat": 50.1, "lon": 20.2},
        {"title": "Lunch options in Lisbon", "summary": "no place here"},
        "not an object",
        {"title": ""},
    ]}
    out = feeds.from_digest(doc, feeds.specs()[0])
    assert out["dropped"] == 3
    saudi, red_sea, port = out["items"]
    assert saudi["country"] == "SA" and saudi["severity"] == 0.8 and saudi["kind"] == "strike"
    assert saudi["props"] == [["Source", "Digest"], ["Anchor", "approx. - country label point"]]
    assert red_sea["lat"] == pytest.approx(12.58, abs=0.01)
    assert red_sea["props"] == [["Source", "Digest"], ["Anchor", "approx. - Bab-el-Mandeb"]]
    assert (port["lat"], port["lon"]) == (50.1, 20.2)
    assert port["props"] == [["Source", "Digest"], ["Anchor", "coordinates from the item"]]


def test_digest_source_country_is_only_a_fallback():
    configure([{"file": "~/d.json", "country": "US"}])
    doc = {"items": [{"title": "Something unplaced"}, {"title": "Suez blockage", "country": "EG"}]}
    out = feeds.from_digest(doc, feeds.specs()[0])
    assert [i["country"] for i in out["items"]] == ["US", "EG"]
    assert out["items"][1]["lat"] is not None  # the chokepoint in the text beats the country


def test_digest_times_accept_iso_and_epoch_milliseconds():
    configure([{"file": "~/d.json"}])
    doc = {"items": [{"title": "Kenya flood", "time": "2026-11-09T16:17:53Z"},
                     {"title": "Kenya drought", "sent_at": 1794241073000}]}
    out = feeds.from_digest(doc, feeds.specs()[0])
    assert out["items"][0]["time"] == out["items"][1]["time"] == 1794241073000


def test_a_document_that_is_not_a_digest_is_a_502_with_a_sentence():
    configure([{"file": "~/d.json", "label": "Digest"}])
    with pytest.raises(UpstreamError) as err:
        feeds.from_digest({"not_items": []}, feeds.specs()[0])
    assert "not a digest" in str(err.value)


# --- Loading and the layer --------------------------------------------------------------------------


def test_a_digest_file_loads_and_a_missing_file_is_reported(tmp_path):
    good = tmp_path / "digest.json"
    good.write_text(json.dumps({"items": [{"title": "Fiji cyclone", "country": "FJ"}]}), encoding="utf-8")
    configure([{"file": str(good), "label": "Digest"}])
    out = feeds.load(feeds.specs()[0])
    assert out["items"][0]["country"] == "FJ"
    configure([{"file": str(tmp_path / "gone.json"), "label": "Gone"}])
    with pytest.raises(UpstreamError) as err:
        feeds.load(feeds.specs()[0])
    assert "could not be read" in str(err.value)


def test_the_layer_fetches_every_source_and_one_broken_source_only_makes_it_stale(tmp_path):
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"items": [{"title": "Chile mine collapse", "country": "CL"}]}), encoding="utf-8")
    configure([{"file": str(ok), "label": "OK"}, {"file": str(tmp_path / "missing.json"), "label": "Missing"}])
    result = layers.fetch("feeds")
    assert result["status"] == "stale"
    assert result["count"] == 1
    assert "Missing" in result["error"] and "could not be read" in result["error"]
    assert result["items"][0]["country"] == "CL"
    entry = next(r for r in layers.registry() if r["id"] == "feeds")
    assert entry["status"] == "stale" and entry["count"] == 1


def test_the_layer_draws_from_several_files_and_keeps_source_parts_separate(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"items": [{"title": "Ghana election", "country": "GH"}]}), encoding="utf-8")
    b.write_text(json.dumps({"items": [{"title": "Strike shuts Suez Canal", "severity": 0.9}]}), encoding="utf-8")
    configure([{"file": str(a), "label": "A"}, {"file": str(b), "label": "B"}])
    result = layers.fetch("feeds")
    assert result["status"] == "ok" and result["count"] == 2
    assert sorted(i["severity"] for i in result["items"]) == [0.4, 0.9]
    assert [i["props"][0][1] for i in result["items"]] == ["A", "B"]  # each source its own cache part


def test_the_layer_is_not_disabled_once_a_source_exists():
    configure([{"file": "/does/not/exist.json"}])
    assert feeds.missing() is None
    assert next(r for r in layers.registry() if r["id"] == "feeds")["status"] == "idle"
