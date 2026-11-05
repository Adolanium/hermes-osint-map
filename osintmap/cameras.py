"""Official open-data traffic cameras: where they are, and their latest still frame.

Only government open-data feeds with a published licence are used, never scraped webcam sites or
private cameras:

- Transport for London JamCams (api.tfl.gov.uk), "Powered by TfL Open Data".
- Singapore LTA traffic images (data.gov.sg), Singapore Open Data Licence v1.0.
- Caltrans CCTV (cwwp2.dot.ca.gov), California's state highways, published free of charge.
- Hong Kong Transport Department traffic snapshots (DATA.GOV.HK), under its terms of use.

Frames are fetched by camera *id*: the id is looked up in the camera list this module produced,
and the image URL must be on one of the fixed image hosts. A caller can never make the gateway
fetch an arbitrary URL.
"""

from __future__ import annotations

import base64
import csv
import io
import re
import threading
import xml.etree.ElementTree as ElementTree
from typing import Any, Callable
from urllib.parse import urlsplit

from . import http, parsers
from .http import TTLCache, UpstreamError

TFL = "https://api.tfl.gov.uk/Place/Type/JamCam"
SINGAPORE = "https://api.data.gov.sg/v1/transport/traffic-images"
CALTRANS_DISTRICTS = tuple(range(1, 13))
HONG_KONG = "https://static.data.gov.hk/td/traffic-snapshot-images/code/Traffic_Camera_Locations_En.xml"
IMAGE_HOSTS = {
    "s3-eu-west-1.amazonaws.com": "/jamcams.tfl.gov.uk/",  # TfL JamCams bucket only
    "images.data.gov.sg": "/",
    "cwwp2.dot.ca.gov": "/data/d",  # Caltrans district image folders only
    "tdcctv.data.one.gov.hk": "/",
}
SOURCE_NAMES = {"tfl": "TfL", "sg": "data.gov.sg", "ca": "Caltrans", "hk": "the Hong Kong Transport Department"}
MAX_CAMERAS = 10_000
MAX_FRAME_BYTES = 1_500_000
FRAME_TTL = 60.0
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")

ATTRIBUTION = {
    "tfl": "Powered by TfL Open Data. Contains OS data © Crown copyright and database rights.",
    "sg": "Contains information from Traffic Images accessed from data.gov.sg, made available under the "
          "Singapore Open Data Licence version 1.0.",
    "ca": "Caltrans CCTV, California Department of Transportation (cwwp2.dot.ca.gov).",
    "hk": "Traffic snapshot images: Transport Department, the Government of the Hong Kong SAR, via DATA.GOV.HK.",
}


def caltrans_url(district: int) -> str:
    return f"https://cwwp2.dot.ca.gov/data/d{district}/cctv/cctvStatusD{district:02d}.csv"

_DIRECTIONS = {"north", "south", "east", "west", "north east", "north west", "south east", "south west",
               "northeast", "northwest", "southeast", "southwest", "n", "s", "e", "w", "ne", "nw", "se", "sw"}

# Image URL for every camera id the layer has produced, so frames can be served by id.
_urls: dict[str, tuple[str, str]] = {}
_urls_lock = threading.Lock()
_frames = TTLCache(FRAME_TTL, size=128)


def _remember(found: list[dict]) -> None:
    with _urls_lock:
        for entry in found:
            url, source = entry.pop("_image"), entry.pop("_source")
            _urls[entry["id"]] = (url, source)
        if len(_urls) > MAX_CAMERAS:  # bounded: keep the newest entries only
            for key in list(_urls)[: len(_urls) - MAX_CAMERAS]:
                del _urls[key]


def _image_allowed(url: Any) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    prefix = IMAGE_HOSTS.get(parts.hostname or "")
    return parts.scheme == "https" and prefix is not None and parts.path.startswith(prefix)


def parse_tfl(raw: Any) -> list[dict]:
    """TfL ``Place/Type/JamCam`` → camera items (unavailable cameras skipped)."""
    out = []
    for place in raw if isinstance(raw, list) else []:
        if not isinstance(place, dict) or not isinstance(place.get("id"), str):
            continue
        lat, lon = parsers.number(place.get("lat"), -90, 90), parsers.number(place.get("lon"), -180, 180)
        props = {p.get("key"): p.get("value") for p in place.get("additionalProperties") or [] if isinstance(p, dict)}
        image = props.get("imageUrl")
        if lat is None or lon is None or not _image_allowed(image) or str(props.get("available")).lower() == "false":
            continue
        key = place["id"].removeprefix("JamCams_")
        view = parsers.text(props.get("view"), 40)
        # TfL's "view" is sometimes a compass direction ("West"), sometimes a place name.
        facing = view.lower() if view and view.lower() in _DIRECTIONS else None
        found = parsers.item(
            "cameras", f"tfl:{key}", "traffic", lat, lon, parsers.text(place.get("commonName"), 120) or "London camera",
            subtitle="Transport for London" + (f" · facing {facing}" if facing else ""),
            severity=0.2, country="GB", url="https://tfl.gov.uk/traffic/status/",
            rows=[("Operator", "Transport for London"), ("View", view), ("Camera", key)],
        )
        out.append({**found, "_image": image, "_source": "tfl"})
    return out


def parse_singapore(raw: Any) -> list[dict]:
    """data.gov.sg ``transport/traffic-images`` → camera items."""
    items = raw.get("items") if isinstance(raw, dict) else None
    cameras = items[0].get("cameras") if isinstance(items, list) and items and isinstance(items[0], dict) else None
    out = []
    for cam in cameras or []:
        if not isinstance(cam, dict):
            continue
        location = cam.get("location") if isinstance(cam.get("location"), dict) else {}
        lat = parsers.number(location.get("latitude"), -90, 90)
        lon = parsers.number(location.get("longitude"), -180, 180)
        key = parsers.text(cam.get("camera_id"), 20)
        image = cam.get("image")
        if lat is None or lon is None or not key or not _image_allowed(image):
            continue
        found = parsers.item(
            "cameras", f"sg:{key}", "traffic", lat, lon, f"Singapore traffic camera {key}",
            subtitle="Land Transport Authority, Singapore", severity=0.2, country="SG",
            time=parsers.iso_ms(cam.get("timestamp")), url="https://data.gov.sg",
            rows=[("Operator", "Land Transport Authority"), ("Camera", key)],
        )
        out.append({**found, "_image": image, "_source": "sg"})
    return out


_SLUG = re.compile(r"/cctv/image/([A-Za-z0-9_-]{1,80})/")


def parse_caltrans(text: str) -> list[dict]:
    """A Caltrans district's ``cctvStatusDnn.csv`` → camera items (out-of-service cameras skipped)."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        lat, lon = parsers.number(row.get("latitude"), -90, 90), parsers.number(row.get("longitude"), -180, 180)
        image = row.get("currentImageURL")
        slug = _SLUG.search(image or "")
        district = parsers.text(row.get("district"), 3)
        if lat is None or lon is None or not _image_allowed(image) or not slug or not district:
            continue
        if str(row.get("inService")).lower() != "true":
            continue
        place = parsers.text(row.get("nearbyPlace"), 60)
        direction = parsers.text(row.get("direction"), 20)
        facing = direction.lower() if direction and direction.lower() in _DIRECTIONS else None
        found = parsers.item(
            "cameras", f"ca:d{district}:{slug.group(1)}", "traffic", lat, lon,
            parsers.text(row.get("locationName"), 120) or "California highway camera",
            subtitle="Caltrans" + (f" · {place}" if place else "") + (f" · facing {facing}" if facing else ""),
            severity=0.2, country="US", url="https://quickmap.dot.ca.gov/",
            rows=[("Operator", f"Caltrans District {district}"), ("Route", parsers.text(row.get("route"), 20)),
                  ("Near", place), ("Facing", direction)],
        )
        out.append({**found, "_image": image, "_source": "ca"})
    return out


def parse_hong_kong(text: str) -> list[dict]:
    """The Transport Department's camera list (XML) → camera items."""
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as exc:
        raise UpstreamError(502, "The Hong Kong camera list is not valid XML.") from exc
    out = []
    for image in root.iter("image"):
        field = {child.tag: (child.text or "").strip() for child in image}
        lat, lon = parsers.number(field.get("latitude"), -90, 90), parsers.number(field.get("longitude"), -180, 180)
        key = field.get("key", "")
        url = field.get("url")
        if lat is None or lon is None or not re.fullmatch(r"[A-Z0-9]{2,12}", key) or not _image_allowed(url):
            continue
        title = re.sub(r"\s*\[[A-Z0-9]+\]\s*$", "", field.get("description", "")) or f"Hong Kong camera {key}"
        district = parsers.text(field.get("district"), 40)
        found = parsers.item(
            "cameras", f"hk:{key}", "traffic", lat, lon, parsers.text(title, 120),
            subtitle="Transport Department, Hong Kong" + (f" · {district}" if district else ""),
            severity=0.2, country="HK", url="https://www.td.gov.hk/en/transport_in_hong_kong/its/",
            rows=[("Operator", "Transport Department"), ("Region", parsers.text(field.get("region"), 40)),
                  ("District", district), ("Camera", key)],
        )
        out.append({**found, "_image": url, "_source": "hk"})
    return out


def loader(url: str, parse: Callable[[Any], list[dict]], source: str) -> Callable[[], dict]:
    def load() -> dict:
        found = parse(http.get_json(url, source=source, timeout=20.0))
        _remember(found)
        return {"items": found}
    return load


def text_loader(url: str, parse: Callable[[str], list[dict]], source: str, timeout: float = 45.0) -> Callable[[], dict]:
    """For the CSV and XML feeds. Caltrans's server is slow, so the timeout is generous."""
    def load() -> dict:
        found = parse(http.get(url, source=source, timeout=timeout).text())
        _remember(found)
        return {"items": found}
    return load


def frame(camera_id: str, fetch: Callable[..., Any] = http.get) -> dict:
    """``GET /camera/{id}``: the camera's latest still, as a data URL, cached for a minute.
    Only ids the camera layer has listed are served."""
    with _urls_lock:
        known = _urls.get(camera_id)
    if known is None:
        raise UpstreamError(404, "Unknown camera. Reload the cameras layer and try again.")
    url, source = known
    if not _image_allowed(url):  # defence in depth: the list only ever holds allowed URLs
        raise UpstreamError(403, "Refusing to fetch an image from that host.")

    def load() -> dict:
        response = fetch(url, source=SOURCE_NAMES.get(source, source), timeout=15.0,
                         max_bytes=MAX_FRAME_BYTES, headers={"Accept": ", ".join(IMAGE_TYPES)})
        # Judge by the bytes, not the label: data.gov.sg serves its JPEGs as octet-stream.
        kind = sniff_image(response.body)
        if kind is None:
            raise UpstreamError(502, "The camera did not return an image.")
        modified = response.headers.get("last-modified")
        return {
            "id": camera_id,
            "image": f"data:{kind};base64,{base64.b64encode(response.body).decode('ascii')}",
            "captured": _http_date_ms(modified),
            "fetched": http.now_ms(),
            "attribution": ATTRIBUTION[source],
        }

    return _frames.get_or_load(camera_id, load)


def sniff_image(body: bytes) -> str | None:
    """The image type from its file signature, or ``None`` when it is not a JPEG, PNG or WebP."""
    if body[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if body[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    return None


def _http_date_ms(value: str | None) -> int | None:
    if not value:
        return None
    from email.utils import parsedate_to_datetime

    try:
        return int(parsedate_to_datetime(value).timestamp() * 1000)
    except (TypeError, ValueError):
        return None
