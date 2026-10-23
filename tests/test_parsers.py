"""Layer parsers against recorded upstream payloads (captured 2026-10-23, trimmed)."""

import copy

import pytest

from osintmap import chokepoints, geo, parsers

ITEM_KEYS = {"id", "layer", "kind", "lat", "lon", "alt_km", "title", "subtitle", "severity", "time", "country",
             "url", "heading", "speed_kts", "props"}


def check_shape(entry, layer):
    assert ITEM_KEYS <= set(entry)
    assert entry["id"].startswith(layer + ":") and entry["layer"] == layer
    assert 0 <= entry["severity"] <= 1
    assert len(entry["props"]) <= 8 and all(isinstance(v, str) for _, v in entry["props"])
    assert "<" not in entry["title"]


# --- USGS ------------------------------------------------------------------------------------------


def test_usgs_items(fixture):
    raw = fixture("usgs_day.json")
    items = parsers.parse_usgs(raw)
    assert len(items) == 3
    first, feature = items[0], raw["features"][0]
    check_shape(first, "quakes")
    assert first["id"] == "quakes:" + feature["id"]
    assert first["lon"] == round(feature["geometry"]["coordinates"][0], 5)
    assert first["time"] == feature["properties"]["time"]
    assert first["title"] == feature["properties"]["title"]
    assert first["url"].startswith("https://earthquake.usgs.gov/")


def test_usgs_dedupes_across_feeds_and_skips_unplaceable(fixture):
    day = fixture("usgs_day.json")
    week = copy.deepcopy(day)
    week["features"][0]["properties"]["title"] = "from the week feed"
    week["features"].append({"id": "nogeo", "properties": {"mag": 5}, "geometry": None})
    items = parsers.parse_usgs(day, week)
    assert len(items) == 3 and items[0]["title"] != "from the week feed"


@pytest.mark.parametrize("mag,expected", [(2.5, 0.1), (7.0, 1.0), (8.5, 1.0), (4.75, 0.55), (None, 0.1)])
def test_quake_severity_scale(mag, expected):
    assert parsers.quake_severity(mag) == pytest.approx(expected, abs=0.001)


def test_usgs_country_from_place():
    feature = {"id": "x", "properties": {"mag": 5, "place": "12 km SW of Ridgecrest, CA", "time": 1},
               "geometry": {"type": "Point", "coordinates": [-117.7, 35.6, 8]}}
    other = {"id": "y", "properties": {"mag": 5, "place": "Kermadec Islands region", "time": 1},
             "geometry": {"type": "Point", "coordinates": [-178, -30, 8]}}
    japan = {"id": "z", "properties": {"mag": 5, "place": "32 km SE of Hachijo, Japan", "time": 1},
             "geometry": {"type": "Point", "coordinates": [139.2, 33.1, 10]}}
    items = {i["id"]: i for i in parsers.parse_usgs({"features": [feature, other, japan]})}
    assert items["quakes:x"]["country"] == "US"
    assert items["quakes:y"]["country"] is None
    assert items["quakes:z"]["country"] == "JP" and items["quakes:z"]["subtitle"] == "Depth 10 km"


# --- EONET -----------------------------------------------------------------------------------------


def test_eonet_uses_latest_geometry(fixture):
    raw = fixture("eonet.json")
    items = parsers.parse_eonet(raw)
    assert len(items) == len(raw["events"])
    storm = items[0]
    check_shape(storm, "natural")
    latest = max(raw["events"][0]["geometry"], key=lambda g: g["date"])
    assert (storm["lon"], storm["lat"]) == tuple(latest["coordinates"])
    assert storm["kind"] == "severeStorms"
    assert {i["kind"] for i in items} >= {"wildfires", "volcanoes"}


def test_eonet_polygon_becomes_centroid():
    raw = {"events": [{"id": "EONET_1", "title": "Fire", "categories": [{"id": "wildfires", "title": "Wildfires"}],
                       "sources": [], "link": "https://eonet.gsfc.nasa.gov/api/v3/events/EONET_1",
                       "geometry": [{"date": "2026-10-20T00:00:00Z", "type": "Polygon",
                                     "coordinates": [[[10, 10], [12, 10], [12, 12], [10, 12], [10, 10]]]}]}]}
    entry = parsers.parse_eonet(raw)[0]
    assert (entry["lat"], entry["lon"]) == (11.0, 11.0)
    assert entry["url"] == "https://eonet.gsfc.nasa.gov/api/v3/events/EONET_1"


# --- GDACS -----------------------------------------------------------------------------------------


def test_gdacs_items(fixture):
    raw = fixture("gdacs.json")
    items = parsers.parse_gdacs(raw)
    assert {i["kind"] for i in items} == {"TC", "EQ", "FL", "WF"}
    tc = next(i for i in items if i["kind"] == "TC")
    check_shape(tc, "disasters")
    assert tc["country"] == "MX" and tc["severity"] == 0.3
    assert tc["url"].startswith("https://www.gdacs.org/report.aspx")


@pytest.mark.parametrize("level,severity", [("Green", 0.3), ("Orange", 0.65), ("Red", 1.0)])
def test_gdacs_alert_levels(fixture, level, severity):
    raw = fixture("gdacs.json")
    raw["features"] = raw["features"][:1]
    raw["features"][0]["properties"]["alertlevel"] = level
    assert parsers.parse_gdacs(raw)[0]["severity"] == severity


# --- NWS -------------------------------------------------------------------------------------------


def test_nws_polygons_kept_zone_only_dropped(fixture):
    items, dropped = parsers.parse_nws(fixture("nws.json"))
    assert len(items) == 2 and dropped == 1
    check_shape(items[0], "weather")
    assert items[0]["country"] == "US" and items[0]["kind"] == "Flood Warning"
    assert items[0]["severity"] == 0.7


def test_centroid_handles_multipolygon_and_antimeridian():
    multi = {"type": "MultiPolygon", "coordinates": [[[[179, 0], [179, 2], [-179, 2], [-179, 0], [179, 0]]]]}
    lat, lon = geo.centroid(multi)
    assert lat == 1.0 and abs(abs(lon) - 180) < 0.01
    assert geo.centroid({"type": "Point", "coordinates": [5, 6]}) == (6.0, 5.0)
    assert geo.centroid(None) is None and geo.centroid({"type": "Polygon", "coordinates": []}) is None


# --- adsb.lol --------------------------------------------------------------------------------------


def test_adsb_military_feed(fixture):
    raw = fixture("adsb_mil.json")
    items = parsers.parse_adsb(raw, military=True)
    assert len(items) == 3 and all(i["kind"] == "military" for i in items)
    first, ac = items[0], raw["ac"][0]
    check_shape(first, "aircraft")
    assert first["title"] == ac["flight"].strip()
    assert first["alt_km"] == pytest.approx(ac["alt_baro"] * 0.0003048, abs=0.001)
    assert first["speed_kts"] == pytest.approx(ac["gs"], abs=0.1) and first["heading"] is not None


def test_adsb_point_feed_uses_db_flags_and_drops_ground_traffic(fixture):
    raw = fixture("adsb_point.json")
    raw["ac"].append({"hex": "abc123", "lat": 51.47, "lon": -0.45, "alt_baro": "ground", "flight": "TAXI1"})
    raw["ac"].append({"hex": "def456", "alt_baro": 3000})  # no position
    raw["ac"].append({"hex": "ae0001", "lat": 51.5, "lon": -0.4, "alt_baro": 9000, "dbFlags": 1, "r": "MIL1"})
    raw["ac"].append({"hex": "4ca123", "lat": 51.5, "lon": -0.4, "alt_baro": 9000, "squawk": "7700"})
    items = {i["id"]: i for i in parsers.parse_adsb(raw, military=False)}
    assert "aircraft:abc123" not in items and "aircraft:def456" not in items
    assert items["aircraft:ae0001"]["kind"] == "military" and items["aircraft:ae0001"]["title"] == "MIL1"
    assert items["aircraft:4ca123"]["severity"] == 1.0 and items["aircraft:4ca123"]["title"] == "4CA123"
    assert any(i["kind"] == "civil" for i in items.values())


# --- CelesTrak -------------------------------------------------------------------------------------


def test_celestrak_items_carry_omm_and_no_position(fixture):
    raw = fixture("celestrak_stations.json")
    items = parsers.parse_celestrak(raw, "stations")
    assert len(items) == 3
    iss = items[0]
    check_shape(iss, "satellites")
    assert iss["id"] == "satellites:25544" and iss["kind"] == "stations"
    assert iss["lat"] is None and iss["lon"] is None
    assert iss["omm"]["MEAN_MOTION"] == raw[0]["MEAN_MOTION"] and iss["omm"]["NORAD_CAT_ID"] == 25544
    assert parsers.parse_celestrak("No GP data found", "x") == []


# --- Text, countries, chokepoints ------------------------------------------------------------------


def test_strip_tags_and_text():
    html = '<p>Hello&nbsp;<b>world</b> &amp; <a href="x">friends</a></p><script>alert(1)</script><style>p{}</style>'
    assert parsers.text(parsers.strip_tags(html), 100) == "Hello world & friends"
    assert parsers.text("x" * 50, 10) == "x" * 9 + "…"
    assert parsers.text("  null ", 10) is None
    assert parsers.strip_tags("Albania&#039;s protests") == "Albania's protests"


@pytest.mark.parametrize("value,expected", [("2026-10-21T15:00:00", 1792594800000), ("2026-10-21T15:00:00Z", 1792594800000),
                                            ("2026-10-21T17:00:00+02:00", 1792594800000), ("nope", None), (None, None)])
def test_iso_ms(value, expected):
    assert parsers.iso_ms(value) == expected


def test_country_tables():
    assert geo.from_fips("AS") == "AU" and geo.from_fips("UP") == "UA" and geo.from_fips("GZ") == "PS"
    assert geo.from_iso3("MEX") == "MX" and geo.from_iso3("XXX") is None
    assert geo.from_place("Kharkiv, Kharkivs'ka Oblast', Ukraine") == "UA"
    assert geo.from_name("Alaska") == "US" and geo.from_name("Burma") == "MM" and geo.from_name("Narnia") is None
    assert geo.normalise_iso2("jp") == "JP" and geo.normalise_iso2("ZZ") is None


def test_props_cap_and_skip_empty():
    rows = [(f"r{i}", i) for i in range(12)] + [("empty", None)]
    assert len(parsers.props(rows)) == 8
    assert parsers.props([("a", None), ("b", "")]) == []


def test_chokepoints_static_list():
    items = chokepoints.items()
    assert len(items) == 15
    names = {i["title"] for i in items}
    assert {"Strait of Hormuz", "Suez Canal", "Panama Canal", "Strait of Malacca", "Bab-el-Mandeb"} <= names
    for entry in items:
        check_shape(entry, "chokepoints")
        assert entry["kind"] in ("strait", "canal", "cape")
