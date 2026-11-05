"""Hermes steers the OSINT Map: ``osint_show`` puts a scene on the globe while Hermes answers.

A scene is a camera move plus marks: where to look (a country, or a point and radius), up to a
dozen labelled pins, an optional ring, layers to switch on or off, and a vision mode. The tool
validates it and stores it in the plugin's Hermes state under ``show``, the same store watches
use, so the Desktop page (which polls ``/show``) picks it up whichever process wrote it.

Nothing about a scene is trusted: numbers are range-checked, labels are trimmed plain text, and
only known layer ids and vision modes are kept.
"""

from __future__ import annotations

import re
import time
from typing import Any, Protocol

from .http import UpstreamError

KEY = "show"
MAX_PINS = 12
LABEL = 48
CAPTION = 140
VISION = ("normal", "nvg", "thermal", "crt")
LAYER_IDS = ("quakes", "disasters", "natural", "weather", "incidents", "aircraft", "satellites", "cameras",
             "chokepoints", "cyber")
_COUNTRY = re.compile(r"^[A-Z]{2}$")


class State(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


def _num(value: Any, low: float, high: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise UpstreamError(400, f"{name} must be a number.") from None
    if not low <= number <= high:
        raise UpstreamError(400, f"{name} must be between {low:g} and {high:g}.")
    return round(number, 4)


def _text(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _point(raw: Any, what: str) -> dict:
    if not isinstance(raw, dict):
        raise UpstreamError(400, f"{what} needs lat and lon.")
    return {"lat": _num(raw.get("lat"), -90, 90, f"{what} lat"), "lon": _num(raw.get("lon"), -180, 180, f"{what} lon")}


def _layers(raw: Any) -> list[str]:
    if raw in (None, ""):
        return []
    items = raw.split(",") if isinstance(raw, str) else raw if isinstance(raw, list) else []
    return [x for x in (str(i).strip().lower() for i in items) if x in LAYER_IDS][: len(LAYER_IDS)]


def scene(args: dict) -> dict:
    """Validate tool arguments into a scene. Raises ``UpstreamError(400)`` with a sentence."""
    out: dict[str, Any] = {"caption": _text(args.get("caption"), CAPTION)}
    if not out["caption"]:
        raise UpstreamError(400, "caption is required: one short line saying what the map shows.")
    country = str(args.get("country") or "").strip().upper()
    if country:
        if not _COUNTRY.match(country):
            raise UpstreamError(400, "country must be an ISO 3166 alpha-2 code, like YE.")
        out["focus"] = {"country": country}
    elif args.get("lat") not in (None, "") or args.get("lon") not in (None, ""):
        focus = _point(args, "focus")
        focus["radius_km"] = _num(args.get("radius_km", 800), 20, 6000, "radius_km")
        out["focus"] = focus
    pins = []
    for raw in (args.get("pins") or [])[:MAX_PINS]:
        pin = _point(raw, "Each pin")
        pin["label"] = _text(raw.get("label"), LABEL) or "Pin"
        pins.append(pin)
    out["pins"] = pins
    if isinstance(args.get("ring"), dict):
        ring = _point(args["ring"], "ring")
        ring["radius_km"] = _num(args["ring"].get("radius_km", 300), 10, 5000, "ring radius_km")
        ring["label"] = _text(args["ring"].get("label"), LABEL)
        out["ring"] = ring
    out["layers_on"] = _layers(args.get("layers_on"))
    out["layers_off"] = [x for x in _layers(args.get("layers_off")) if x not in out["layers_on"]]
    vision = str(args.get("vision") or "").strip().lower()
    if vision:
        if vision not in VISION:
            raise UpstreamError(400, "vision must be normal, nvg, thermal or crt.")
        out["vision"] = vision
    if "focus" not in out and not pins and "ring" not in out:
        raise UpstreamError(400, "Give the map somewhere to look: a country, a lat/lon focus, pins or a ring.")
    return out


def show(state: State, args: dict) -> dict:
    """Store a new scene and return ``{"seq", "scene"}``."""
    current = state.get(KEY) or {}
    entry = {"seq": int(current.get("seq") or 0) + 1, "at": int(time.time() * 1000), "scene": scene(args)}
    state.set(KEY, entry)
    return entry


def latest(state: State, after: int = 0) -> dict:
    """``GET /show``: the newest scene if it is newer than ``after``."""
    current = state.get(KEY) or {}
    seq = int(current.get("seq") or 0)
    return {"seq": seq, "at": current.get("at"), "scene": current.get("scene") if seq > after else None}


def describe(entry: dict) -> str:
    sc = entry["scene"]
    bits = [f"On the OSINT Map now: {sc['caption']}."]
    focus = sc.get("focus")
    if focus and "country" in focus:
        bits.append(f"The globe flew to {focus['country']}.")
    elif focus:
        bits.append(f"The globe flew to {focus['lat']:.2f}, {focus['lon']:.2f} ({focus['radius_km']:.0f} km across).")
    if sc["pins"]:
        bits.append(f"{len(sc['pins'])} pin(s): " + "; ".join(p["label"] for p in sc["pins"]) + ".")
    if sc.get("ring"):
        bits.append(f"A {sc['ring']['radius_km']:.0f} km ring is drawn.")
    bits.append("The user sees this on the OSINT Map page in Hermes Desktop (sidebar: OSINT Map). Don't repeat the "
                "coordinates in your answer; refer to what is on the map.")
    return " ".join(bits)
