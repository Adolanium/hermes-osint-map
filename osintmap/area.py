"""One filter for "what is on the map here": a country, or a point and a radius, over some layers.

The agent tools (osint_events, osint_watch), the chat map card (``GET /area``) and watches all
read the same function, so a card in a chat, an alert and the tool text never disagree about
which items are in an area.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import geo, layers
from .http import UpstreamError
from .parsers import number

EVENT_LAYERS = [layer_id for layer_id in layers.LAYERS if layer_id != "satellites"]
DEFAULT_RADIUS_KM = 500.0
MAX_RADIUS_KM = 5000.0
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Area:
    lat: float | None = None
    lon: float | None = None
    radius_km: float = DEFAULT_RADIUS_KM
    country: str | None = None
    layer_ids: tuple[str, ...] = tuple(EVENT_LAYERS)

    @property
    def has_point(self) -> bool:
        return self.lat is not None and self.lon is not None

    def describe(self) -> str:
        where = []
        if self.country:
            where.append(f"in {geo.name(self.country) or self.country}")
        if self.has_point:
            where.append(f"within {self.radius_km:.0f} km of {self.lat:.2f},{self.lon:.2f}")
        return " ".join(where) or "worldwide"

    def contains(self, entry: dict) -> bool:
        if self.country and entry.get("country") != self.country:
            return False
        if self.has_point:
            if entry.get("lat") is None or entry.get("lon") is None:
                return False
            if geo.haversine_km(self.lat, self.lon, entry["lat"], entry["lon"]) > self.radius_km:
                return False
        return True


def parse(args: dict, *, default_layers: list[str] | None = None) -> Area:
    """Validate tool or route arguments into an Area. Raises ``UpstreamError(400)`` with a sentence."""
    country = None
    if args.get("country"):
        country = geo.normalise_iso2(str(args["country"]))
        if country is None:
            raise UpstreamError(400, "country must be an ISO 3166 alpha-2 code, like UA.")
    lat, lon = number(args.get("lat"), -90, 90), number(args.get("lon"), -180, 180)
    if (args.get("lat") not in (None, "") or args.get("lon") not in (None, "")) and (lat is None or lon is None):
        raise UpstreamError(400, "Give both lat (-90..90) and lon (-180..180), or neither.")
    radius = number(args.get("radius_km"), 1, 20000) or DEFAULT_RADIUS_KM
    raw_layers = args.get("layers")
    if args.get("layer"):
        raw_layers = [args["layer"]]
    if isinstance(raw_layers, str):
        raw_layers = [part.strip() for part in raw_layers.split(",") if part.strip()]
    if raw_layers:
        unknown = [str(one) for one in raw_layers if one not in layers.LAYERS]
        if unknown:
            raise UpstreamError(400, f"Unknown layer {unknown[0][:40]!r}; use one of {', '.join(layers.LAYERS)}.")
        layer_ids = tuple(dict.fromkeys(str(one) for one in raw_layers))
    else:
        layer_ids = tuple(default_layers or EVENT_LAYERS)
    return Area(lat=lat, lon=lon, radius_km=min(radius, MAX_RADIUS_KM), country=country, layer_ids=layer_ids)


def collect(area: Area) -> tuple[list[dict], dict[str, dict]]:
    """Items in the area, most severe first, and each layer's answer (for status lines)."""
    answers: dict[str, dict] = {}
    for one in area.layer_ids:
        query: dict[str, Any] = {}
        if one == "aircraft" and area.has_point:
            query = {"lat": area.lat, "lon": area.lon, "radius": min(250, area.radius_km / 1.852)}
        answers[one] = layers.fetch(one, query)
    found = [entry for answer in answers.values() for entry in answer["items"] if area.contains(entry)]
    found.sort(key=lambda i: (i["severity"], i["time"] or 0), reverse=True)
    return found, answers


def prop(entry: dict, name: str) -> str | None:
    for row in entry.get("props") or []:
        if len(row) > 1 and row[0] == name:
            return str(row[1])
    return None


def magnitude(entry: dict) -> float | None:
    """Earthquake magnitude from the item's facts ("5.6 mww" → 5.6)."""
    value = prop(entry, "Magnitude")
    match = _NUMBER.search(value or "")
    return float(match.group()) if match else None


def alert_level(entry: dict) -> str | None:
    """GDACS alert colour, lower case (green, orange, red)."""
    value = prop(entry, "Alert level")
    return value.strip().lower() if value else None


def distance_km(area: Area, entry: dict) -> float | None:
    if not area.has_point or entry.get("lat") is None or entry.get("lon") is None:
        return None
    return geo.haversine_km(area.lat, area.lon, entry["lat"], entry["lon"])


def card_directive(area: Area, title: str | None = None) -> str:
    """The transcript directive Hermes Desktop draws as a live map card."""
    attrs = []
    if area.country:
        attrs.append(f'country="{area.country}"')
    if area.has_point:
        attrs += [f'lat="{area.lat:.3f}"', f'lon="{area.lon:.3f}"', f'radius="{area.radius_km:.0f}"']
    if tuple(area.layer_ids) != tuple(EVENT_LAYERS):
        attrs.append(f'layers="{",".join(area.layer_ids)}"')
    if title:
        clean = re.sub(r'["{}\n\r]', "", title).strip()[:60]
        if clean:
            attrs.append(f'title="{clean}"')
    return "::osint-map{" + " ".join(attrs) + "}"


def card_hint(area: Area, title: str | None = None) -> str:
    """One line for tool text: how to show this area as a map card in a Hermes Desktop chat."""
    if not (area.country or area.has_point):
        return ""
    return ("Map card: when you answer in Hermes Desktop, you can show this area on a live map by putting this "
            "line alone in its own paragraph of your reply (never in messaging platforms or the CLI): "
            + card_directive(area, title))
