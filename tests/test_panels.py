"""Headlines, space weather, markets and World Bank country facts."""

from datetime import datetime, timezone

import pytest

from osintmap import http as net
from osintmap import panels
from osintmap.http import SnapshotCache, TTLCache, UpstreamError


def ms(*parts):
    return int(datetime(*parts, tzinfo=timezone.utc).timestamp() * 1000)

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>World</title>
<item><title>Albania&#039;s student protests grow</title>
<link>https://www.dw.com/en/a-1?maca=rss</link>
<description><![CDATA[<p>Police try to <b>suppress</b> protests &amp; marches.</p><img src="x.jpg"/>]]></description>
<pubDate>Thu, 22 Oct 2026 19:18:00 GMT</pubDate></item>
<item><title>Second story</title><link>https://www.dw.com/en/b-2</link><dc:date>2026-10-22T20:00:00Z</dc:date></item>
<item><title></title><link>https://www.dw.com/en/empty</link></item>
<item><title>No link at all</title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>T</title>
<entry><title type="html">Quake &lt;em&gt;strikes&lt;/em&gt; coast</title>
<link rel="alternate" href="https://example.org/quake"/><updated>2026-10-23T01:00:00Z</updated>
<summary>Short summary</summary></entry></feed>"""

RDF = b"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><title>x</title></channel>
<item><title>RDF item</title><link>https://example.org/rdf</link><dc:date>2026-10-23T02:00:00+00:00</dc:date></item>
</rdf:RDF>"""


# --- News ------------------------------------------------------------------------------------------


def test_rss_items_are_plain_text():
    items = panels.parse_feed(RSS, "DW")
    assert [i["title"] for i in items] == ["Albania's student protests grow", "Second story"]
    first = items[0]
    assert first["summary"] == "Police try to suppress protests & marches."
    assert first["source"] == "DW" and first["url"] == "https://www.dw.com/en/a-1?maca=rss"
    assert first["time"] == ms(2026, 10, 22, 19, 18) and items[1]["time"] == ms(2026, 10, 22, 20, 0)
    assert first["id"].startswith("news:")


def test_atom_and_rdf_feeds():
    atom = panels.parse_feed(ATOM, "X")[0]
    assert atom["title"] == "Quake strikes coast" and atom["url"] == "https://example.org/quake"
    assert atom["summary"] == "Short summary" and atom["time"]
    assert panels.parse_feed(RDF, "Y")[0]["url"] == "https://example.org/rdf"


def test_unreadable_feed_is_an_upstream_error():
    with pytest.raises(UpstreamError, match="could not read"):
        panels.parse_feed(b"<html><body>oops", "BBC World")


def test_merge_newest_first_and_folds_duplicates():
    a = [{"title": "Same Story!", "url": "https://a.example/1?x=1", "time": 5, "summary": None},
         {"title": "Old", "url": "https://a.example/2", "time": 1, "summary": None}]
    b = [{"title": "same story", "url": "https://b.example/9", "time": 4, "summary": None},
         {"title": "Other", "url": "https://a.example/1", "time": 3, "summary": None},
         {"title": "Undated", "url": "https://b.example/u", "time": None, "summary": None}]
    merged = panels.merge_headlines([a, b])
    assert [m["title"] for m in merged] == ["Same Story!", "Old", "Undated"]


def test_news_filters_partial_failures_and_reports_them(monkeypatch):
    monkeypatch.setattr(panels, "_cache", SnapshotCache())

    def fake_get(url, **kwargs):
        if "dw.com" in url:
            return net.Response(200, {}, RSS, url)
        raise UpstreamError(503, f"{kwargs['source']} is having trouble (HTTP 503).")

    monkeypatch.setattr(panels.http, "get", fake_get)
    answer = panels.news(q="ALBANIA", limit="5")
    assert [i["title"] for i in answer["items"]] == ["Albania's student protests grow"]
    assert answer["status"] == "stale" and "BBC World" in answer["error"]
    assert {f["source"]: f["status"] for f in answer["feeds"]}["DW"] == "ok"


def test_news_with_no_feed_at_all_raises(monkeypatch):
    monkeypatch.setattr(panels, "_cache", SnapshotCache())
    monkeypatch.setattr(panels.http, "get", lambda url, **k: (_ for _ in ()).throw(UpstreamError(503, "down")))
    with pytest.raises(UpstreamError, match="None of the news feeds"):
        panels.news()


# --- Space weather ---------------------------------------------------------------------------------


def test_kp_list_of_objects(fixture):
    points = panels.parse_kp(fixture("swpc_kp.json"))
    assert len(points) == 12 and all(0 <= p["kp"] <= 9 for p in points)
    assert points == sorted(points, key=lambda p: p["time"])


def test_kp_legacy_table_and_week_window():
    table = [["time_tag", "Kp", "a_running", "station_count"],
             ["2026-10-01 00:00:00.000", "2.00", "7", "8"],
             ["2026-10-16 00:00:00.000", "3.33", "18", "8"],
             ["2026-10-16 03:00:00.000", "bad", "18", "8"]]
    assert panels.parse_kp(table) == [{"time": ms(2026, 10, 16), "kp": 3.33}]


def test_kp_now_scales_and_alerts(fixture):
    assert panels.parse_kp_now(fixture("swpc_kp1m.json")) == 1.33
    assert panels.parse_scales(fixture("swpc_scales.json")) == {"R": 0, "S": 0, "G": 0}
    alerts = panels.parse_alerts(fixture("swpc_alerts.json"))
    assert len(alerts) == 3 and alerts[0]["code"] == "WARK04"
    assert alerts[0]["headline"].startswith("EXTENDED WARNING: Geomagnetic K-index")
    assert alerts == sorted(alerts, key=lambda a: a["time"], reverse=True)


def test_space_weather_serves_what_it_has(monkeypatch, fixture):
    monkeypatch.setattr(panels, "_cache", SnapshotCache())
    data = {panels.KP_3H: fixture("swpc_kp.json"), panels.SCALES: fixture("swpc_scales.json")}

    def fake(url, **kwargs):
        if url in data:
            return data[url]
        raise UpstreamError(503, "NOAA SWPC is having trouble (HTTP 503).")

    monkeypatch.setattr(panels.http, "get_json", fake)
    answer = panels.space_weather()
    assert answer["status"] == "stale" and answer["alerts"] == [] and answer["scales"]["G"] == 0
    assert answer["kp_now"] == answer["kp"][-1]["kp"]  # falls back to the newest 3-hour value


# --- Markets ---------------------------------------------------------------------------------------


def test_coingecko():
    raw = {"bitcoin": {"usd": 101000.5, "usd_24h_change": -1.2345}, "ethereum": {"usd": 3900},
           "solana": {"usd": "bad"}}
    rows = panels.parse_coingecko(raw)
    assert rows[0] == {"symbol": "BTC", "label": "Bitcoin", "price": 101000.5, "change_pct": -1.23, "currency": "USD"}
    assert [r["symbol"] for r in rows] == ["BTC", "ETH"]


def test_twelvedata_batch_with_per_symbol_errors():
    raw = {"LMT": {"close": "470.10", "percent_change": "0.52", "currency": "USD"},
           "XAU/USD": {"close": "2650.1", "percent_change": "-0.3"},
           "SPX": {"status": "error", "message": "symbol not available on your plan"}}
    groups, notes = panels.parse_twelvedata(raw)
    assert [g["id"] for g in groups] == ["defense", "materials"]
    assert groups[0]["items"][0]["price"] == 470.10
    assert any(n.startswith("SPX: symbol not available") for n in notes)
    with pytest.raises(UpstreamError, match="refused"):
        panels.parse_twelvedata({"status": "error", "code": 401, "message": "bad key"})


def test_markets_without_key_explains_how_to_enable(monkeypatch):
    monkeypatch.setattr(panels, "_cache", SnapshotCache())
    monkeypatch.setattr(panels, "twelvedata_key", lambda: None)
    monkeypatch.setattr(panels.http, "get_json", lambda url, **k: {"bitcoin": {"usd": 1, "usd_24h_change": 2}})
    answer = panels.markets()
    assert answer["groups"][0]["id"] == "crypto"
    assert any("TWELVEDATA_API_KEY" in n for n in answer["notes"])


def test_markets_key_goes_in_a_header_not_the_url(monkeypatch):
    monkeypatch.setattr(panels, "_cache", SnapshotCache())
    monkeypatch.setattr(panels, "twelvedata_key", lambda: "SECRET123")
    seen = []

    def fake(url, **kwargs):
        seen.append((url, kwargs))
        if "twelvedata" in url:
            raise UpstreamError(503, "Twelve Data is having trouble (HTTP 503).")
        return {}

    monkeypatch.setattr(panels.http, "get_json", fake)
    answer = panels.markets()
    url, kwargs = next(s for s in seen if "twelvedata" in s[0])
    assert "SECRET123" not in url and "SECRET123" not in str(kwargs.get("params"))
    assert kwargs["headers"]["Authorization"] == "apikey SECRET123"
    assert not any("SECRET123" in n for n in answer["notes"])


# --- World Bank ------------------------------------------------------------------------------------


def test_country_parse(fixture):
    facts = panels.parse_country(fixture("wb_country.json"), fixture("wb_indicators.json"), "JP")
    assert facts["name"] == "Japan" and facts["capital"] == "Tokyo" and facts["region"] == "East Asia & Pacific"
    assert facts["income"] == "High income" and facts["lat"] == 35.67 and facts["lon"] == 139.77
    assert facts["population"] == 123366734 and facts["gdp_usd"] > 1e12 and facts["year"] == 2025


def test_country_unknown_and_invalid_codes(monkeypatch):
    monkeypatch.setattr(panels, "_country_cache", TTLCache(60))
    invalid = [{"message": [{"id": "120", "key": "Invalid value", "value": "The provided parameter value is not valid"}]}]
    monkeypatch.setattr(panels.http, "get_json", lambda url, **k: invalid)
    with pytest.raises(UpstreamError) as err:
        panels.country("QQ")
    assert err.value.status == 404
    for bad in ("J", "JPN", "1A", "", "j p"):
        with pytest.raises(UpstreamError) as err:
            panels.country(bad)
        assert err.value.status == 400
