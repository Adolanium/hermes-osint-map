"""Pure parse/normalise functions: upstream payload → the page's ``Item`` shape.

Nothing here does I/O, so each parser is tested against recorded fixtures. Every upstream is
treated as untrusted: a record that cannot be placed or named is dropped, odd fields become
``None``, strings are collapsed to plain text and cut to a length the page can show.
"""

from __future__ import annotations

import html
import math
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

from . import geo

MAX_TITLE = 160
MAX_SUBTITLE = 120
MAX_PROP = 120
MAX_PROPS = 8


# --- Text and numbers ------------------------------------------------------------------------------


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def strip_tags(value: Any) -> str:
    """HTML fragment (or entity-escaped text) → plain text, scripts and styles dropped."""
    if not isinstance(value, str) or not value:
        return ""
    if "<" not in value and "&" not in value:
        return value
    parser = _TextOnly()
    try:
        parser.feed(value)
        parser.close()
    except Exception:  # malformed markup: fall back to a blunt cut
        return html.unescape(re.sub(r"<[^>]*>", " ", value))
    return "".join(parser.parts)


def text(value: Any, limit: int) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned or cleaned.lower() in {"null", "none", "n/a", "unknown"}:
        return None
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def number(value: Any, low: float = -math.inf, high: float = math.inf) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    if not math.isfinite(result) or result < low or result > high:
        return None
    return result


def clamp01(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def iso_ms(value: Any) -> int | None:
    """ISO 8601 (``Z``, offsets, or naive meaning UTC) → epoch ms."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _fmt(value: float, digits: int = 1) -> str:
    return f"{value:,.{digits}f}".rstrip("0").rstrip(".") if digits else f"{value:,.0f}"


def props(rows: list[tuple[str, Any]]) -> list[list[str]]:
    """Display facts: rows with no value are skipped; at most ``MAX_PROPS`` rows of strings."""
    out = []
    for label, value in rows:
        shown = text(value, MAX_PROP)
        if shown is not None:
            out.append([label, shown])
        if len(out) == MAX_PROPS:
            break
    return out


def item(layer: str, key: Any, kind: str, lat: float | None, lon: float | None, title: str, *,
         subtitle: str | None = None, severity: float = 0.3, time: int | None = None,
         country: str | None = None, url: str | None = None, alt_km: float | None = None,
         heading: float | None = None, speed_kts: float | None = None,
         rows: list[tuple[str, Any]] | None = None, **extra: Any) -> dict:
    return {
        "id": f"{layer}:{key}",
        "layer": layer,
        "kind": kind,
        "lat": None if lat is None else round(lat, 5),
        "lon": None if lon is None else round(lon, 5),
        "alt_km": None if alt_km is None else round(alt_km, 3),
        "title": text(title, MAX_TITLE) or "Untitled",
        "subtitle": text(subtitle, MAX_SUBTITLE),
        "severity": clamp01(severity),
        "time": time,
        "country": geo.normalise_iso2(country),
        "url": url if isinstance(url, str) and url.startswith(("https://", "http://")) else None,
        "heading": None if heading is None else round(heading, 1),
        "speed_kts": None if speed_kts is None else round(speed_kts, 1),
        "props": props(rows or []),
        **extra,
    }


def _lat_lon(lat: Any, lon: Any) -> tuple[float, float] | None:
    la, lo = number(lat, -90, 90), number(lon, -180, 180)
    if la is None or lo is None or (la == 0 and lo == 0):  # 0,0 is "no fix" in most feeds
        return None
    return la, lo


# --- USGS earthquakes ------------------------------------------------------------------------------


def quake_severity(mag: float | None) -> float:
    """M2.5 → 0.1, rising linearly to 1.0 at M7 and above."""
    if mag is None:
        return 0.1
    return clamp01(0.1 + (mag - 2.5) / 4.5 * 0.9)


def parse_usgs(*feeds: Any) -> list[dict]:
    """USGS summary GeoJSON feeds → earthquake items, deduplicated by event id (the first feed
    wins, so pass the most detailed feed first)."""
    out: dict[str, dict] = {}
    for raw in feeds:
        for feature in (raw.get("features") if isinstance(raw, dict) else None) or []:
            if not isinstance(feature, dict) or not isinstance(feature.get("id"), str):
                continue
            key = feature["id"]
            if key in out:
                continue
            p = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
            coords = (feature.get("geometry") or {}).get("coordinates") if isinstance(feature.get("geometry"), dict) else None
            if not isinstance(coords, list) or len(coords) < 2:
                continue
            spot = _lat_lon(coords[1], coords[0])
            if spot is None:
                continue
            depth = number(coords[2], -20, 1000) if len(coords) > 2 else None
            mag = number(p.get("mag"), -2, 10)
            place = text(p.get("place"), 120)
            when = number(p.get("time"), 0)
            alert = text(p.get("alert"), 10)
            out[key] = item(
                "quakes", key, "earthquake", *spot,
                text(p.get("title"), MAX_TITLE) or f"M {mag if mag is not None else '?'} - {place or 'unknown place'}",
                subtitle=f"Depth {_fmt(depth)} km" if depth is not None else None,
                severity=quake_severity(mag),
                time=int(when) if when is not None else None,
                country=geo.from_place(place),
                url=p.get("url"),
                rows=[("Magnitude", f"{mag:g} {p.get('magType') or ''}".strip() if mag is not None else None),
                      ("Depth", f"{_fmt(depth)} km" if depth is not None else None),
                      ("Place", place),
                      ("PAGER alert", alert.capitalize() if alert else None),
                      ("Tsunami flag", "Yes" if p.get("tsunami") == 1 else None),
                      ("Felt reports", p.get("felt") if p.get("felt") else None),
                      ("Status", p.get("status"))],
            )
    return list(out.values())


# --- NASA EONET ------------------------------------------------------------------------------------

_EONET_BASE = {"volcanoes": 0.6, "severeStorms": 0.5, "wildfires": 0.4, "floods": 0.5, "landslides": 0.5,
               "earthquakes": 0.5, "drought": 0.4, "dustHaze": 0.25, "seaLakeIce": 0.15, "snow": 0.2,
               "tempExtremes": 0.35, "waterColor": 0.15, "manmade": 0.35}


def parse_eonet(raw: Any) -> list[dict]:
    """Open EONET events → one item at each event's latest geometry (polygons → centroid)."""
    out = []
    for event in (raw.get("events") if isinstance(raw, dict) else None) or []:
        if not isinstance(event, dict) or not isinstance(event.get("id"), str):
            continue
        geometries = [g for g in event.get("geometry") or [] if isinstance(g, dict)]
        if not geometries:
            continue
        latest = max(geometries, key=lambda g: iso_ms(g.get("date")) or 0)
        spot = geo.centroid({"type": latest.get("type"), "coordinates": latest.get("coordinates")})
        if spot is None:
            continue
        categories = [c for c in event.get("categories") or [] if isinstance(c, dict) and isinstance(c.get("id"), str)]
        kind = categories[0]["id"] if categories else "other"
        label = text(categories[0].get("title"), 40) if categories else None
        magnitude = number(latest.get("magnitudeValue"), 0)
        unit = text(latest.get("magnitudeUnit"), 20)
        severity = _EONET_BASE.get(kind, 0.3)
        if kind == "severeStorms" and magnitude is not None and unit == "kts":
            severity = 0.3 + (magnitude - 35) / (137 - 35) * 0.7  # tropical storm → category 5
        sources = [s for s in event.get("sources") or [] if isinstance(s, dict)]
        url = next((s.get("url") for s in sources if isinstance(s.get("url"), str)), None) or event.get("link")
        out.append(item(
            "natural", event["id"], kind, *spot, text(event.get("title"), MAX_TITLE) or label or "Natural event",
            subtitle=label,
            severity=severity,
            time=iso_ms(latest.get("date")),
            url=url,
            rows=[("Category", label),
                  ("Magnitude", f"{_fmt(magnitude)} {unit or ''}".strip() if magnitude is not None else None),
                  ("Observations", len(geometries) if len(geometries) > 1 else None),
                  ("Source", ", ".join(text(s.get("id"), 20) or "" for s in sources[:3]).strip(", ") or None)],
        ))
    return out


# --- GDACS -----------------------------------------------------------------------------------------

_GDACS_LEVEL = {"green": 0.3, "orange": 0.65, "red": 1.0}
_GDACS_KIND = {"EQ": "Earthquake", "TC": "Tropical cyclone", "FL": "Flood", "VO": "Volcano",
               "DR": "Drought", "WF": "Wildfire", "TS": "Tsunami"}


def parse_gdacs(raw: Any) -> list[dict]:
    out = []
    for feature in (raw.get("features") if isinstance(raw, dict) else None) or []:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            continue
        p = feature["properties"]
        kind = text(p.get("eventtype"), 4)
        event_id = p.get("eventid")
        if kind not in _GDACS_KIND or not isinstance(event_id, (int, str)):
            continue
        spot = geo.centroid(feature.get("geometry"))
        if spot is None:
            continue
        level = (text(p.get("alertlevel"), 10) or "").lower()
        severity_data = p.get("severitydata") if isinstance(p.get("severitydata"), dict) else {}
        affected = [c for c in p.get("affectedcountries") or [] if isinstance(c, dict)]
        country = geo.from_iso3(p.get("iso3")) or (geo.normalise_iso2(affected[0].get("iso2")) if affected else None)
        links = p.get("url") if isinstance(p.get("url"), dict) else {}
        out.append(item(
            "disasters", f"{kind}{event_id}", kind, *spot,
            text(p.get("name"), MAX_TITLE) or f"{_GDACS_KIND[kind]} {event_id}",
            subtitle=text(severity_data.get("severitytext"), MAX_SUBTITLE),
            severity=_GDACS_LEVEL.get(level, 0.3),
            time=iso_ms(p.get("datemodified")) or iso_ms(p.get("fromdate")),
            country=country,
            url=links.get("report"),
            rows=[("Type", _GDACS_KIND[kind]),
                  ("Alert level", level.capitalize() or None),
                  ("Severity", severity_data.get("severitytext")),
                  ("Country", p.get("country")),
                  ("From", (p.get("fromdate") or "")[:10] or None),
                  ("To", (p.get("todate") or "")[:10] or None)],
        ))
    return out


# --- NWS alerts ------------------------------------------------------------------------------------

_NWS_SEVERITY = {"Extreme": 1.0, "Severe": 0.7, "Moderate": 0.45, "Minor": 0.25}


def parse_nws(raw: Any) -> tuple[list[dict], int]:
    """Active NWS alerts → items at the alert polygon's centroid. Alerts that only name forecast
    zones have no geometry; they are dropped and counted rather than guessed at."""
    out, dropped = [], 0
    for feature in (raw.get("features") if isinstance(raw, dict) else None) or []:
        if not isinstance(feature, dict) or not isinstance(feature.get("properties"), dict):
            continue
        p = feature["properties"]
        key = p.get("id") or feature.get("id")
        if not isinstance(key, str):
            continue
        spot = geo.centroid(feature.get("geometry"))
        if spot is None:
            dropped += 1
            continue
        severity = text(p.get("severity"), 20)
        event = text(p.get("event"), 60) or "Weather alert"
        out.append(item(
            "weather", key.rsplit(":", 1)[-1] if key.startswith("urn:") else key, event, *spot,
            f"{event} · {text(p.get('areaDesc'), 80) or 'United States'}",
            subtitle=text(p.get("senderName"), MAX_SUBTITLE),
            severity=_NWS_SEVERITY.get(severity or "", 0.4),
            time=iso_ms(p.get("onset")) or iso_ms(p.get("effective")) or iso_ms(p.get("sent")),
            country="US",
            url=p.get("@id") if isinstance(p.get("@id"), str) else None,
            rows=[("Severity", severity), ("Certainty", p.get("certainty")), ("Urgency", p.get("urgency")),
                  ("Area", p.get("areaDesc")), ("Expires", p.get("expires")), ("Headline", p.get("headline"))],
        ))
    return out, dropped


# --- adsb.lol --------------------------------------------------------------------------------------

_EMERGENCY_SQUAWKS = {"7500": "Unlawful interference", "7600": "Radio failure", "7700": "General emergency"}
FT_TO_KM = 0.0003048


def parse_adsb(raw: Any, *, military: bool) -> list[dict]:
    """adsb.lol ``v2`` aircraft list → items. Aircraft on the ground or without a position fix are
    dropped. ``military`` marks every aircraft from the military feed; elsewhere the database flag
    (``dbFlags`` bit 0) decides."""
    if not isinstance(raw, dict):
        return []
    now = number(raw.get("now"), 0)
    out = []
    for ac in raw.get("ac") or []:
        if not isinstance(ac, dict) or not isinstance(ac.get("hex"), str):
            continue
        spot = _lat_lon(ac.get("lat"), ac.get("lon"))
        alt = ac.get("alt_baro")
        if spot is None or alt == "ground":
            continue
        alt_ft = number(alt, -2000, 80000)
        flags = ac.get("dbFlags") if isinstance(ac.get("dbFlags"), int) else 0
        is_mil = military or bool(flags & 1)
        hex_id = ac["hex"].strip().lower()[:8]
        callsign = text(ac.get("flight"), 12)
        reg = text(ac.get("r"), 16)
        type_code = text(ac.get("t"), 12)
        squawk = text(ac.get("squawk"), 4)
        speed = number(ac.get("gs"), 0, 2500)
        heading = number(ac.get("track"), 0, 360)
        if heading is None:
            heading = number(ac.get("true_heading"), 0, 360)
        seen = number(ac.get("seen_pos"), 0, 3600) or 0.0
        emergency = _EMERGENCY_SQUAWKS.get(squawk or "")
        out.append(item(
            "aircraft", hex_id, "military" if is_mil else "civil", *spot,
            callsign or reg or hex_id.upper(),
            subtitle=" · ".join(x for x in (type_code, f"{_fmt(alt_ft, 0)} ft" if alt_ft is not None else None) if x) or None,
            severity=1.0 if emergency else (0.7 if is_mil else 0.35),
            time=int(now - seen * 1000) if now else None,
            alt_km=alt_ft * FT_TO_KM if alt_ft is not None else None,
            heading=heading, speed_kts=speed,
            rows=[("Type", type_code), ("Registration", reg), ("Squawk", f"{squawk} ({emergency})" if emergency else squawk),
                  ("Altitude", f"{_fmt(alt_ft, 0)} ft" if alt_ft is not None else None),
                  ("Speed", f"{_fmt(speed, 0)} kt" if speed is not None else None),
                  ("ICAO hex", hex_id.upper())],
        ))
    return out


# --- CelesTrak -------------------------------------------------------------------------------------

_OMM_KEYS = ("OBJECT_NAME", "OBJECT_ID", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION", "RA_OF_ASC_NODE",
             "ARG_OF_PERICENTER", "MEAN_ANOMALY", "EPHEMERIS_TYPE", "CLASSIFICATION_TYPE", "NORAD_CAT_ID",
             "ELEMENT_SET_NO", "REV_AT_EPOCH", "BSTAR", "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT")


def parse_celestrak(raw: Any, group: str) -> list[dict]:
    """CelesTrak OMM JSON → satellite items without a position: the page propagates ``omm`` with
    SGP4, which is why lat/lon are ``None`` here."""
    out = []
    for omm in raw if isinstance(raw, list) else []:
        if not isinstance(omm, dict):
            continue
        norad = omm.get("NORAD_CAT_ID")
        motion = number(omm.get("MEAN_MOTION"), 0.01, 20)
        if not isinstance(norad, int) or motion is None or not isinstance(omm.get("EPOCH"), str):
            continue
        clean = {k: omm[k] for k in _OMM_KEYS if k in omm}
        incl = number(omm.get("INCLINATION"), 0, 180)
        period = 1440 / motion
        out.append(item(
            "satellites", norad, group, None, None, text(omm.get("OBJECT_NAME"), MAX_TITLE) or f"NORAD {norad}",
            subtitle=f"{group} · {_fmt(period)} min orbit",
            severity=0.3,
            time=iso_ms(omm["EPOCH"]),
            url=f"https://celestrak.org/satcat/table-satcat.php?CATNR={norad}",
            rows=[("NORAD id", norad), ("International designator", omm.get("OBJECT_ID")), ("Group", group),
                  ("Inclination", f"{_fmt(incl, 2)}°" if incl is not None else None),
                  ("Period", f"{_fmt(period)} min"), ("Elements epoch", omm["EPOCH"][:19])],
            omm=clean,
        ))
    return out
