"""Traffic cameras: parsing the two official feeds, and serving frames by id only."""

import pytest

from osintmap import cameras
from osintmap.http import UpstreamError

TFL = [
    {"id": "JamCams_00002.00865", "commonName": "A406 Billet Upass E", "lat": 51.60067, "lon": -0.01594,
     "additionalProperties": [
         {"key": "available", "value": "true"},
         {"key": "imageUrl", "value": "https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/00002.00865.jpg"},
         {"key": "view", "value": "West"}]},
    {"id": "JamCams_off", "commonName": "Out of service", "lat": 51.5, "lon": -0.1,
     "additionalProperties": [{"key": "available", "value": "false"},
                              {"key": "imageUrl", "value": "https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/x.jpg"}]},
    {"id": "JamCams_elsewhere", "commonName": "Wrong bucket", "lat": 51.5, "lon": -0.1,
     "additionalProperties": [{"key": "imageUrl", "value": "https://s3-eu-west-1.amazonaws.com/other-bucket/x.jpg"}]},
    {"id": "JamCams_nopos", "commonName": "No position", "lat": None, "lon": None, "additionalProperties": []},
    "junk",
]
SINGAPORE = {"items": [{"timestamp": "2026-10-24T13:09:50+08:00", "cameras": [
    {"camera_id": "1001", "timestamp": "2026-10-24T13:09:00+08:00", "image": "https://images.data.gov.sg/api/traffic-images/a.jpg",
     "location": {"latitude": 1.2955, "longitude": 103.8715}},
    {"camera_id": "1002", "image": "http://images.data.gov.sg/api/traffic-images/b.jpg",  # not https
     "location": {"latitude": 1.3, "longitude": 103.8}},
    {"camera_id": "1003", "image": "https://evil.example/c.jpg", "location": {"latitude": 1.3, "longitude": 103.8}},
]}]}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(cameras, "_urls", {})
    monkeypatch.setattr(cameras, "_frames", cameras.TTLCache(60))


def test_tfl_keeps_only_available_cameras_on_the_jamcams_bucket():
    found = cameras.parse_tfl(TFL)
    assert [c["id"] for c in found] == ["cameras:tfl:00002.00865"]
    cam = found[0]
    assert cam["country"] == "GB" and cam["lat"] == 51.60067
    assert "facing west" in cam["subtitle"]
    assert cam["_image"].endswith("00002.00865.jpg")
    assert cameras.parse_tfl({"not": "a list"}) == []


def test_singapore_keeps_only_https_images_on_data_gov_sg():
    found = cameras.parse_singapore(SINGAPORE)
    assert [c["id"] for c in found] == ["cameras:sg:1001"]
    assert found[0]["country"] == "SG" and found[0]["time"] is not None
    assert cameras.parse_singapore({}) == [] and cameras.parse_singapore(None) == []


def test_loader_strips_image_urls_from_items_and_remembers_them(monkeypatch):
    monkeypatch.setattr(cameras.http, "get_json", lambda url, source, timeout: TFL)
    answer = cameras.loader(cameras.TFL, cameras.parse_tfl, "TfL")()
    assert all("_image" not in c and "_source" not in c for c in answer["items"])
    assert cameras._urls["cameras:tfl:00002.00865"][1] == "tfl"


JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0])


class FakeResponse:
    def __init__(self, body=JPEG + b"jpeg", kind="image/jpeg"):
        self.body = body
        self.headers = {"content-type": kind, "last-modified": "Sat, 24 Oct 2026 05:03:02 GMT"}


def test_frames_are_served_by_known_id_only():
    with pytest.raises(UpstreamError) as err:
        cameras.frame("cameras:tfl:never-listed", fetch=lambda *a, **k: FakeResponse())
    assert err.value.status == 404
    with pytest.raises(UpstreamError):
        cameras.frame("https://evil.example/x.jpg", fetch=lambda *a, **k: FakeResponse())


def test_frame_is_a_data_url_with_capture_time_and_attribution_and_is_cached(monkeypatch):
    monkeypatch.setattr(cameras.http, "get_json", lambda url, source, timeout: TFL)
    cameras.loader(cameras.TFL, cameras.parse_tfl, "TfL")()
    calls = []

    def fetch(url, **kwargs):
        calls.append((url, kwargs["max_bytes"]))
        return FakeResponse()

    first = cameras.frame("cameras:tfl:00002.00865", fetch=fetch)
    second = cameras.frame("cameras:tfl:00002.00865", fetch=fetch)
    assert first["image"].startswith("data:image/jpeg;base64,")
    assert first["captured"] == 1792818182000
    assert "TfL Open Data" in first["attribution"]
    assert second is first and len(calls) == 1
    assert calls[0] == ("https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/00002.00865.jpg", cameras.MAX_FRAME_BYTES)


def test_non_image_answers_are_refused_and_not_cached(monkeypatch):
    monkeypatch.setattr(cameras.http, "get_json", lambda url, source, timeout: TFL)
    cameras.loader(cameras.TFL, cameras.parse_tfl, "TfL")()
    with pytest.raises(UpstreamError):
        cameras.frame("cameras:tfl:00002.00865", fetch=lambda *a, **k: FakeResponse(b"<html>", "text/html"))
    good = cameras.frame("cameras:tfl:00002.00865", fetch=lambda *a, **k: FakeResponse())
    assert good["image"].startswith("data:image/jpeg")


@pytest.mark.parametrize("url,ok", [
    ("https://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/1.jpg", True),
    ("https://images.data.gov.sg/api/traffic-images/a.jpg", True),
    ("http://s3-eu-west-1.amazonaws.com/jamcams.tfl.gov.uk/1.jpg", False),
    ("https://s3-eu-west-1.amazonaws.com/someone-else/1.jpg", False),
    ("https://images.data.gov.sg.evil.example/a.jpg", False),
    ("https://127.0.0.1/jamcams.tfl.gov.uk/1.jpg", False),
    (None, False),
])
def test_image_host_allowlist(url, ok):
    assert cameras._image_allowed(url) is ok


def test_images_are_recognised_by_signature_not_label():
    assert cameras.sniff_image(JPEG + b"rest") == "image/jpeg"
    assert cameras.sniff_image(b"\x89PNG\r\n\x1a\nrest") == "image/png"
    assert cameras.sniff_image(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert cameras.sniff_image(b"<html>") is None and cameras.sniff_image(b"") is None


def test_an_octet_stream_jpeg_is_accepted(monkeypatch):
    monkeypatch.setattr(cameras.http, "get_json", lambda url, source, timeout: SINGAPORE)
    cameras.loader(cameras.SINGAPORE, cameras.parse_singapore, "data.gov.sg")()
    found = cameras.frame("cameras:sg:1001", fetch=lambda *a, **k: FakeResponse(JPEG + b"x", "application/octet-stream"))
    assert found["image"].startswith("data:image/jpeg;base64,")
    assert "Singapore Open Data Licence" in found["attribution"]


def test_view_names_are_not_mistaken_for_directions():
    place = {**TFL[0], "additionalProperties": [p if p["key"] != "view" else {"key": "view", "value": "Piccadilly Circus"}
                                                 for p in TFL[0]["additionalProperties"]]}
    cam = cameras.parse_tfl([place])[0]
    assert cam["subtitle"] == "Transport for London"
    assert ("View", "Piccadilly Circus") in [tuple(r) for r in cam["props"]]
