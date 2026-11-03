"""GDELT export parsing, aggregation and the rolling 3-hour window."""

import io
import zipfile

import pytest

from osintmap import gdelt, parsers, tools
from osintmap.http import UpstreamError

LASTUPDATE = (
    "47277 7e603202643d72bbe0b9731640d22166 http://data.gdeltproject.org/gdeltv2/20261023044500.export.CSV.zip\n"
    "51551 8649ec20aa1cb2743854b5e9f74affdc http://data.gdeltproject.org/gdeltv2/20261023044500.mentions.CSV.zip\n"
    "2489657 86cea0d0f3d7ca84f275c12994f62dcd http://data.gdeltproject.org/gdeltv2/20261023044500.gkg.csv.zip\n"
)


def zipped(body: str, name: str = "x.export.CSV") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, body)
    return buffer.getvalue()


def row(root="19", quad="4", lat="50.0", lon="36.25", place="Kharkiv, Kharkivs'ka Oblast', Ukraine", fips="UP",
        mentions="10", goldstein="-10.0", added="20261023044500", url="https://news.example.com/a", actor="RUSSIA",
        geo="4"):
    fields = [""] * 61
    fields[6], fields[16] = actor, "UKRAINE"
    fields[26], fields[27], fields[28], fields[29] = root + "0", root + "0", root, quad
    fields[30], fields[31], fields[32], fields[33], fields[34] = goldstein, mentions, "3", mentions, "-5.5"
    fields[51], fields[52], fields[53], fields[56], fields[57] = geo, place, fips, lat, lon
    fields[59], fields[60] = added, url
    return "\t".join(fields)


def test_lastupdate_names_the_export():
    assert gdelt.parse_lastupdate(LASTUPDATE) == "20261023044500"
    assert gdelt.parse_lastupdate("garbage") is None


def test_window_is_twelve_quarter_hours_newest_first():
    stamps = gdelt.window_stamps("20261023004500")
    assert len(stamps) == 12 and stamps[0] == "20261023004500" and stamps[1] == "20261023003000"
    assert stamps[-1] == "20261022220000"  # crosses midnight


def test_recorded_export_keeps_only_conflict_roots(fixture):
    groups = gdelt.parse_export(fixture("gdelt_export.tsv"))
    roots = {g["root"] for g in groups.values()}
    assert roots == {"19"}  # the fixture's 04/01 (consult, make statement) rows are skipped
    canberra = next(g for g in groups.values() if g["place"].startswith("Canberra"))
    items = gdelt.to_items(groups)
    au = [i for i in items if i["country"] == "AU"]
    assert len(au) == 1  # FIPS AS is Australia, not American Samoa
    assert au[0]["title"] == "Fight · Bruce wolpe the eerie similarity of gabrielle giffords and helen haines cases"
    assert "Canberra, Australia" in au[0]["subtitle"]
    assert canberra["events"] == 1
    # a hash-style last slug falls back to the previous (readable) path segment
    newark = [i for i in items if (i["url"] or "").startswith("http://www.newarkpostonline.com")]
    assert {i["title"] for i in newark} == {
        "Fight · New castle county to expedite next reassessment by year after discovering errors"}


def test_rows_at_the_same_place_and_root_fold_into_one_item():
    body = "\n".join([
        row(mentions="10", url="https://news.example.com/small"),
        row(mentions="80", url="https://news.example.com/big", goldstein="-9.0", added="20261023050000"),
        row(root="14", quad="3", goldstein="-6.5"),  # same place, different root → its own item
        row(root="04", quad="1"),  # cooperation: skipped
        row(root="19", quad="2"),  # mismatched quad class: skipped
        row(lat="", lon=""),  # no action location: skipped
        "short\tline",
    ])
    items = gdelt.to_items(gdelt.parse_export(body))
    assert len(items) == 2
    fight = next(i for i in items if i["kind"] == "fight")
    assert fight["count"] == 2 and fight["title"] == "Fight · Kharkiv, Ukraine"
    assert fight["url"] == "https://news.example.com/big" and fight["country"] == "UA"
    assert fight["time"] == gdelt.stamp_ms("20261023050000")
    assert fight["id"] == "incidents:19:50.0000,36.2500"
    assert dict(fight["props"])["Mentions"] == "90"
    assert next(i for i in items if i["kind"] == "protest")["count"] == 1


def test_merge_sums_across_files():
    a = gdelt.parse_export(row(mentions="5"))
    b = gdelt.parse_export(row(mentions="7", url="https://news.example.com/b"))
    merged = gdelt.merge([a, b])
    assert len(merged) == 1
    only = next(iter(merged.values()))
    assert only["events"] == 2 and only["mentions"] == 12 and only["url"] == "https://news.example.com/b"
    assert a[next(iter(a))]["events"] == 1  # inputs are not mutated


def test_severity_grows_with_coverage_and_violence():
    assert gdelt.incident_severity(1, -1) < gdelt.incident_severity(100, -1) < gdelt.incident_severity(100, -10)
    assert gdelt.incident_severity(10_000, -10) == 1.0


class FakeGdelt:
    def __init__(self, latest):
        self.latest, self.calls, self.missing, self.broken = latest, [], set(), set()

    def text(self, url):
        return f"1 x http://data.gdeltproject.org/gdeltv2/{self.latest}.export.CSV.zip\n"

    def bytes(self, url):
        stamp = url.rsplit("/", 1)[-1][:14]
        self.calls.append(stamp)
        if stamp in self.missing:
            raise UpstreamError(404, "gone")
        if stamp in self.broken:
            raise UpstreamError(503, "down")
        slot = int(stamp[8:10]) * 4 + int(stamp[10:12]) // 15
        lat = str(slot % 80)  # a different place per quarter hour
        return zipped(row(lat=lat, added=stamp))


def test_window_downloads_each_export_once_and_rolls():
    fake = FakeGdelt("20261023044500")
    window = gdelt.Window(fake.text, fake.bytes, workers=1)
    first = window.refresh()
    assert len(fake.calls) == 12 and window.loaded() == 12 and len(first) == 12
    fake.calls.clear()
    assert len(window.refresh()) == 12 and fake.calls == []  # nothing new, nothing fetched
    fake.latest = "20261023050000"
    window.refresh()
    assert fake.calls == ["20261023050000"] and window.loaded() == 12  # oldest dropped


def test_window_tolerates_missing_and_failing_files():
    fake = FakeGdelt("20261023044500")
    fake.missing, fake.broken = {"20261023043000"}, {"20261023041500"}
    window = gdelt.Window(fake.text, fake.bytes, workers=1)
    assert len(window.refresh()) == 10
    fake.calls.clear()
    fake.broken.clear()
    window.refresh()
    assert fake.calls == ["20261023041500"]  # a 404 is remembered as empty; a 503 is retried


def test_window_raises_when_nothing_loads():
    fake = FakeGdelt("20261023044500")
    fake.broken = set(gdelt.window_stamps(fake.latest))
    with pytest.raises(UpstreamError, match="Could not download"):
        gdelt.Window(fake.text, fake.bytes, workers=1).refresh()


def test_bad_zip_is_an_upstream_error():
    with pytest.raises(UpstreamError, match="not a valid zip"):
        gdelt.unzip_export(b"not a zip")
    assert "Kharkiv" in gdelt.unzip_export(zipped(row()))


def test_short_place():
    assert gdelt.short_place("Kharkiv, Kharkivs'ka Oblast', Ukraine") == "Kharkiv, Ukraine"
    assert gdelt.short_place("Ohio, United States") == "Ohio, United States"
    assert gdelt.short_place(None) == "Unknown place"


def test_headline_from_url_reads_the_last_wordy_slug():
    assert gdelt.headline_from_url(
        "https://nypost.com/2026-11-02/us-news/4-buses-vandalized-with-swastikas-"
        "antisemitic-vitriol-outside-nyc-yeshiva-cops/"
    ) == "Buses vandalized with swastikas antisemitic vitriol outside nyc yeshiva cops"


def test_headline_from_url_falls_back_on_hash_slugs_and_query_urls():
    assert gdelt.headline_from_url("https://news.example.com/a") is None
    assert gdelt.headline_from_url(
        "https://www.newarkpostonline.com/article_d1c9a62b-4202-4103-b72f-8dd5fcbd61a8.html"
    ) is None  # "article", hex tokens and digit runs are not a headline
    assert gdelt.headline_from_url("not a url") is None
    assert gdelt.headline_from_url(None) is None


def test_one_article_at_one_place_collapses_across_roots():
    groups = gdelt.parse_export("\n".join([
        row(root="13", mentions="33", url="https://news.example.com/yeshiva-buses-swastikas"),
        row(root="17", mentions="46", url="https://news.example.com/yeshiva-buses-swastikas"),
    ]))
    items = gdelt.to_items(groups)
    assert len(items) == 1
    assert items[0]["id"] == "incidents:17:50.0000,36.2500"
    assert items[0]["kind"] == "coerce"  # 46 mentions > 33 → the more-covered coding survives
    assert [p for p in items[0]["props"] if p[0] == "Also coded"] == [["Also coded", "Threaten"]]


def test_equal_severity_collapse_keeps_the_higher_group_key_whatever_the_row_order():
    """Same URL, same place, equal severity: the survivor must not depend on the order the
    merged dict happens to be built in, or the item id flips between refreshes."""
    tied = dict(mentions="40", url="https://news.example.com/tied")
    forwards = gdelt.to_items(gdelt.parse_export("\n".join([
        row(root="13", **tied), row(root="17", **tied)])))
    backwards = gdelt.to_items(gdelt.parse_export("\n".join([
        row(root="17", **tied), row(root="13", **tied)])))
    assert len(forwards) == len(backwards) == 1
    assert forwards[0]["id"] == backwards[0]["id"] == "incidents:17:50.0000,36.2500"


def test_groups_without_urls_never_collapse():
    groups = gdelt.parse_export("\n".join([
        row(root="13", mentions="40", url=""), row(root="17", mentions="40", url="")]))
    items = gdelt.to_items(groups)
    assert len(items) == 2
    assert {i["kind"] for i in items} == {"threaten", "coerce"}


def test_country_centroid_cross_root_collapse_keeps_place_precision():
    """A country-centroid incident that also cross-root-collapses has actors too, so nine prop
    rows compete for ``MAX_PROPS`` slots. The clamp must drop the least load-bearing row, never
    the 'Place precision' flag that tools._item_line turns into '(country-level accuracy)'."""
    centroid = dict(geo="1", lat="49.0", lon="32.0", place="Ukraine",
                    url="https://news.example.com/ukrainian-units-rotate")
    groups = gdelt.parse_export("\n".join([
        row(root="13", mentions="33", **centroid), row(root="17", mentions="46", **centroid)]))
    items = gdelt.to_items(groups)
    assert len(items) == 1
    item = items[0]
    labels = [p[0] for p in item["props"]]
    # every row fired (collapse → 'Also coded', geo_type 1 → 'Place precision', default actors)
    assert "Also coded" in labels and len(labels) == parsers.MAX_PROPS  # 9 candidates, clamp in force
    assert ["Place precision", "Country only"] in item["props"]
    assert "(country-level accuracy)" in tools._item_line(item)
