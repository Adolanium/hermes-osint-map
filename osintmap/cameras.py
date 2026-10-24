"""Official open-data traffic cameras: where they are, and their latest still frame.

Only government open-data feeds with a published licence are used, never scraped webcam sites or
private cameras:

- Transport for London JamCams (api.tfl.gov.uk), "Powered by TfL Open Data".
- Singapore LTA traffic images (data.gov.sg), Singapore Open Data Licence v1.0.

Frames are fetched by camera *id*: the id is looked up in the camera list this module produced,
and the image URL must be on one of the two fixed image hosts. A caller can never make the gateway
fetch an arbitrary URL.
"""

from __future__ import annotations

import base64
import threading
from typing import Any, Callable
from urllib.parse import urlsplit

from . import http, parsers
from .http import TTLCache, UpstreamError

TFL = "https://api.tfl.gov.uk/Place/Type/JamCam"
SINGAPORE = "https://api.data.gov.sg/v1/transport/traffic-images"
IMAGE_HOSTS = {
    "s3-eu-west-1.amazonaws.com": "/jamcams.tfl.gov.uk/",  # TfL JamCams bucket only
    "images.data.gov.sg": "/",
}
MAX_FRAME_BYTES = 1_500_000
FRAME_TTL = 60.0
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")

ATTRIBUTION = {
    "tfl": "Powered by TfL Open Data. Contains OS data © Crown copyright and database rights.",
    "sg": "Contains information from Traffic Images accessed from data.gov.sg, made available under the "
          "Singapore Open Data Licence version 1.0.",
}

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
        if len(_urls) > 4000:  # bounded: keep the newest entries only
            for key in list(_urls)[: len(_urls) - 4000]:
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


def loader(url: str, parse: Callable[[Any], list[dict]], source: str) -> Callable[[], dict]:
    def load() -> dict:
        found = parse(http.get_json(url, source=source, timeout=20.0))
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
        response = fetch(url, source=source.upper() if source == "sg" else "TfL", timeout=15.0,
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
