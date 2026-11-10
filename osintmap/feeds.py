"""Operator feeds: the RSS/Atom sources and digest files an operator configures, drawn on the globe.

The plugin has no fixed feed list here on purpose. Every source is declared once in the plugin
setting ``OSINT_MAP_FEEDS`` (JSON, order preserved), so the map only ever contacts hosts the
operator named:

    [{"url": "https://example.com/world.xml", "label": "Example world"},
     {"file": "/Users/me/.hermes/state/digest.json", "label": "Morning digest", "country": "US"}]

A ``url`` source is RSS 2.0, RSS 1.0 (RDF) or Atom, read with the same parser the headlines panel
uses. A ``file`` source is any JSON object with an ``items`` list — the shape a scheduled job or a
mail rule can write without knowing anything about this plugin:

    {"version": 1, "updated": "2026-11-09T16:17:53Z",
     "items": [{"title": "…", "url": "https://…", "summary": "…", "country": "SA",
                "place": "Strait of Hormuz", "severity": 0.7, "time": "2026-11-09T16:17:53Z"}]}

Every item carries one optional field that says where it belongs: ``place`` (a chokepoint this
plugin already knows) or ``country`` (ISO alpha-2). When neither is given the item's own text is
read for a chokepoint name, then a country name. Country-only items are drawn at that country's
label point by the page, so they are approximate anchors, never reported locations. An item with no
anchor at all is dropped and counted — this plugin does not guess where a story happened.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path

from . import chokepoints, geo, http, panels, parsers
from .http import UpstreamError

ENV = "OSINT_MAP_FEEDS"
MAX_SOURCES = 12
MAX_ITEMS_PER_SOURCE = 200
MAX_FILE_BYTES = 2 * 1024 * 1024
DEFAULT_TTL = 600
MIN_TTL = 60
MAX_TTL = 24 * 3600
DEFAULT_SEVERITY = 0.4


_READER = None  # bound at register time to ctx.get_config


def use_settings(reader) -> None:
    """Bind the plugin's settings reader (``ctx.get_config``) when the plugin registers."""
    global _READER
    _READER = reader


def _raw_setting() -> object:
    """The configured sources as stored: the plugin setting when there is one, else the
    ``OSINT_MAP_FEEDS`` secret-scope/environment value so a bare process or test still works."""
    if _READER is not None:
        try:
            value = _READER("feeds", None)
        except Exception:  # a broken settings file must not take the layer down
            value = None
        if value not in (None, "", [], {}):
            return value
    try:  # inside the gateway a configured secret/environment value resolves here
        from agent.secret_scope import get_secret

        value = get_secret(ENV)
        if value:
            return value
    except Exception:
        pass
    return os.environ.get(ENV, "")


def specs() -> list[dict]:
    """Validated sources. A malformed setting yields no sources rather than raising: the layer then
    reports ``disabled`` with the reason, and the rest of the map keeps working."""
    value = _raw_setting()
    if isinstance(value, str):
        if not value.strip():
            return []
        try:
            value = json.loads(value)
        except ValueError:
            return []
    if isinstance(value, dict):  # tolerate {"sources": [...]}
        value = value.get("sources")
    if not isinstance(value, list):
        return []
    out = []
    for entry in value:
        if len(out) >= MAX_SOURCES:
            break
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        path = entry.get("file")
        if isinstance(url, str) and url.startswith(("https://", "http://")):
            source: dict[str, object] = {"kind": "rss", "url": url.strip(), "label": _label(entry, url)}
        elif isinstance(path, str) and path.strip():
            source = {"kind": "file", "file": os.path.expanduser(path.strip()), "label": _label(entry, path)}
        else:
            continue
        country = geo.normalise_iso2(entry.get("country"))
        if country:
            source["country"] = country
        source["ttl"] = _ttl(entry.get("ttl"))
        out.append(source)
    return out


def _label(entry: dict, fallback: str) -> str:
    label = parsers.text(entry.get("label"), 40)
    if label:
        return label
    if "://" in fallback:
        host = fallback.split("://", 1)[1].split("/", 1)[0]
        return host[4:] if host.startswith("www.") else host
    return Path(fallback).stem[:40] or "Feed"


def _ttl(value: object) -> int:
    """A per-source refresh interval in seconds, clamped into the sane band."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_TTL
    if not math.isfinite(number):
        return DEFAULT_TTL
    return int(min(max(number, MIN_TTL), MAX_TTL))


def missing() -> str | None:
    """Why the layer cannot draw, or None when it can. Drives the contract's ``disabled`` status."""
    if specs():
        return None
    raw = _raw_setting()
    if raw not in (None, "", [], {}):
        return (f"The feeds setting could not be read: give it a list of sources, each with a "
                f"\"url\" (RSS or Atom) or a \"file\" (a JSON digest).")
    return ("No operator feeds are configured. Add sources to this plugin's \"feeds\" setting to "
            "draw your own RSS/Atom feeds or digest files on the map.")


def key(spec: dict) -> str:
    """Stable cache key and item-id prefix for one source."""
    seed = spec.get("url") or spec.get("file") or spec.get("label") or ""
    return hashlib.sha1(f"{spec.get('kind')}:{seed}".encode()).hexdigest()[:12]


# --- Placement -------------------------------------------------------------------------------------


def _coords(lat: object, lon: object) -> tuple[float, float] | None:
    """An item's own coordinates, when it carries a usable pair."""
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return float(lat), float(lon)


def _anchor(text_value: str, spec: dict, place: object = None, country: object = None,
            lat: object = None, lon: object = None) -> tuple:
    """(lat, lon, country, anchor) for one item. ``anchor`` is the props sentence naming the rule, or
    None when the item is country-only and the page must use that country's label point."""
    code = geo.normalise_iso2(country) or geo.normalise_iso2(spec.get("country"))
    own = _coords(lat, lon)
    if own:  # an exact anchor the item itself carries: used verbatim, never adjusted
        return own[0], own[1], code or geo.country_in(text_value), "coordinates from the item"
    named = place if isinstance(place, str) and place.strip() else None
    point = chokepoints.find(named) if named else None
    if point:
        return point[2], point[3], code or geo.country_in(text_value), f"approx. - {point[1]}"
    point = chokepoints.find(text_value)  # a passage named in the text is more specific than a country
    if point:
        return point[2], point[3], code or geo.country_in(text_value), f"approx. - {point[1]}"
    if code:
        return None, None, code, None
    return None, None, geo.country_in(text_value), None


def _entry(kind: str, source_key: str, index: int, title: str, url: str | None, summary: str | None,
           severity: float, time_ms: int | None, lat: float | None, lon: float | None,
           country: str | None, anchor: str | None, label: str) -> dict:
    seed = url or f"{source_key}:{index}:{title}"
    rows = [("Source", label)]
    if anchor:  # placed by a chokepoint or by the item's own coordinates
        rows.append(("Anchor", anchor))
    elif country:  # placed by a country name: the page uses that country's label point
        rows.append(("Anchor", "approx. - country label point"))
    return parsers.item(
        "feeds", hashlib.sha1(seed.encode()).hexdigest()[:12], kind,
        lat, lon, title, subtitle=summary, severity=severity, time=time_ms,
        country=country, url=url, rows=rows,
    )


# --- Source kinds ----------------------------------------------------------------------------------


def from_rss(body: bytes | str, spec: dict) -> dict:
    """RSS/Atom body → map items (plus ``dropped``). Items with no anchor are dropped."""
    entries = panels.parse_feed(body, spec["label"])
    source_key = key(spec)
    found, dropped = [], 0
    for index, entry in enumerate(entries[:MAX_ITEMS_PER_SOURCE]):
        text_value = f"{entry['title']} {entry.get('summary') or ''}"
        lat, lon, country, anchor = _anchor(text_value, spec)
        if lat is None and not country:
            dropped += 1
            continue
        found.append(_entry("headline", source_key, index, entry["title"], entry.get("url"),
                            entry.get("summary"), DEFAULT_SEVERITY, entry.get("time"),
                            lat, lon, country, anchor, spec["label"]))
    return {"items": found, "dropped": dropped}


def from_digest(doc: object, spec: dict) -> dict:
    """A digest JSON document → map items (plus ``dropped``)."""
    if not isinstance(doc, dict) or not isinstance(doc.get("items"), list):
        raise UpstreamError(502, f"{spec['label']} is not a digest: expected a JSON object with an items list.")
    source_key = key(spec)
    found, dropped = [], 0
    for index, entry in enumerate(doc["items"][:MAX_ITEMS_PER_SOURCE]):
        if not isinstance(entry, dict):
            dropped += 1
            continue
        title = parsers.text(entry.get("title") or entry.get("headline"), 200)
        if not title:
            dropped += 1
            continue
        summary = parsers.text(entry.get("summary") or entry.get("subtitle"), 240)
        text_value = f"{title} {summary or ''}"
        lat, lon, country, anchor = _anchor(text_value, spec, entry.get("place"), entry.get("country"),
                                           entry.get("lat"), entry.get("lon"))
        if lat is None and not country:
            dropped += 1
            continue
        when = entry.get("time") or entry.get("sent_at")
        time_ms = when if isinstance(when, int) else parsers.iso_ms(when)
        severity = parsers.number(entry.get("severity"), 0, 1)
        found.append(_entry(parsers.text(entry.get("tag"), 20) or "digest", source_key, index, title,
                            parsers.text(entry.get("url"), 500), summary,
                            severity if severity is not None else DEFAULT_SEVERITY, time_ms,
                            lat, lon, country, anchor, spec["label"]))
    return {"items": found, "dropped": dropped}


# --- Loading ---------------------------------------------------------------------------------------


def load(spec: dict) -> dict:
    """One source, fetched now. Raises ``UpstreamError`` with a readable sentence on failure."""
    if spec["kind"] == "rss":
        body = http.get(spec["url"], source=spec["label"], redirects=3, timeout=15.0,
                        max_bytes=4 * 1024 * 1024).body
        return from_rss(body, spec)
    path = Path(spec["file"])
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise UpstreamError(502, f"{spec['label']} is larger than {MAX_FILE_BYTES // 1024} KB.")
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UpstreamError(502, f"{spec['label']} could not be read: {path} is missing or unreadable.") from exc
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise UpstreamError(502, f"{spec['label']} is not valid JSON.") from exc
    return from_digest(doc, spec)
