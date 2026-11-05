"""Watches: an area plus rules, checked on a schedule by a Hermes cron job.

A watch has no process of its own. The page (or the agent, in any chat) creates a normal Hermes
scheduled job whose prompt calls ``osint_watch``. Each call compares what is in the area now with
what earlier calls already reported, through the plugin's own Hermes state (``ctx.state``):

- the first call records a baseline and reports nothing, so a new watch doesn't open with an alert
  storm about everything that was already there;
- later calls report only items that match the rules and were not seen before;
- the answer starts with ``NO_NEW_ITEMS`` when there is nothing to say, so the job can reply
  ``[SILENT]`` and Hermes delivers nothing.

State per watch is small and bounded (the last ``SEEN_CAP`` item ids, the last ``ALERT_CAP``
alerts), and an index key lists every watch so the page can show them without a cron round trip.
"""

from __future__ import annotations

import re
import time
from typing import Any, Protocol

from . import area as areas
from .http import UpstreamError

SEEN_CAP = 2000
ALERT_CAP = 20
INDEX_KEY = "watches"
NO_NEW = "NO_NEW_ITEMS"
WATCH_ID = re.compile(r"^[a-z0-9][a-z0-9-]{2,40}$")
DISASTER_LEVELS = {"green": 0, "orange": 1, "red": 2}
DEFAULT_LAYERS = ("quakes", "disasters", "natural", "weather", "aircraft")


class State(Protocol):
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...


def _now_ms() -> int:
    return int(time.time() * 1000)


def key(watch_id: str) -> str:
    return f"watch:{watch_id}"


def rules_from(args: dict) -> dict:
    """Thresholds a new item must pass. Missing values fall back to sensible alerting defaults."""
    try:
        magnitude = float(args.get("min_magnitude")) if args.get("min_magnitude") not in (None, "") else 4.5
    except (TypeError, ValueError):
        raise UpstreamError(400, "min_magnitude must be a number, like 5.") from None
    level = str(args.get("min_alert_level") or "orange").strip().lower()
    if level not in DISASTER_LEVELS:
        raise UpstreamError(400, "min_alert_level must be green, orange or red.")
    return {"min_magnitude": max(0.0, min(magnitude, 10.0)), "min_alert_level": level,
            "military_only": args.get("military_only") is not False}


def passes(entry: dict, rules: dict) -> bool:
    layer = entry.get("layer")
    if layer == "quakes":
        mag = areas.magnitude(entry)
        return mag is not None and mag >= rules["min_magnitude"]
    if layer == "disasters":
        level = areas.alert_level(entry)
        return DISASTER_LEVELS.get(level or "", -1) >= DISASTER_LEVELS[rules["min_alert_level"]]
    if layer == "aircraft":
        return entry.get("kind") == "military" or not rules["military_only"]
    return True


def _line(watch_area: areas.Area, entry: dict) -> str:
    bits = [entry["title"]]
    if entry.get("subtitle"):
        bits.append(f"({entry['subtitle']})")
    far = areas.distance_km(watch_area, entry)
    if far is not None:
        bits.append(f"{far:.0f} km from the watch centre")
    if entry.get("time"):
        bits.append("at " + time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(entry["time"] / 1000)))
    if entry.get("url"):
        bits.append(entry["url"])
    return "- " + " ".join(bits)


def check(state: State, watch_id: str, args: dict) -> dict:
    """Run one watch check. Returns ``{"text", "new", "baseline", "count"}``; ``text`` starts with
    ``NO_NEW_ITEMS`` when nothing new passed the rules."""
    if not WATCH_ID.match(watch_id or ""):
        raise UpstreamError(400, "watch_id must be 3-41 lower-case letters, digits or dashes, like taiwan-strait.")
    watch_area = areas.parse(args, default_layers=list(DEFAULT_LAYERS))
    if not (watch_area.country or watch_area.has_point):
        raise UpstreamError(400, "A watch needs an area: a country code, or lat and lon with a radius.")
    rules = rules_from(args)
    label = (str(args.get("label") or "").strip() or watch_area.describe())[:80]

    found, answers = areas.collect(watch_area)
    matching = [entry for entry in found if passes(entry, rules)]
    record = state.get(key(watch_id)) or {}
    seen = list(record.get("seen") or [])
    seen_set = set(seen)
    baseline = not record
    fresh = [] if baseline else [entry for entry in matching if entry["id"] not in seen_set]
    for entry in matching:
        if entry["id"] not in seen_set:
            seen.append(entry["id"])
            seen_set.add(entry["id"])
    now = _now_ms()
    record.update({
        "label": label,
        "area": {"lat": watch_area.lat, "lon": watch_area.lon, "radius_km": watch_area.radius_km,
                 "country": watch_area.country, "layers": list(watch_area.layer_ids)},
        "rules": rules,
        "seen": seen[-SEEN_CAP:],
        "checks": int(record.get("checks") or 0) + 1,
        "created": record.get("created") or now,
        "last_check": now,
        "matching_now": len(matching),
    })
    if fresh:
        alert = {"at": now, "count": len(fresh),
                 "items": [{"id": e["id"], "layer": e["layer"], "title": e["title"], "lat": e["lat"], "lon": e["lon"],
                            "time": e["time"], "url": e["url"]} for e in fresh[:10]]}
        record["alerts"] = ([alert] + list(record.get("alerts") or []))[:ALERT_CAP]
    state.set(key(watch_id), record)
    index = dict(state.get(INDEX_KEY) or {})
    index[watch_id] = {"label": label, "last_check": now}
    state.set(INDEX_KEY, index)

    failing = [f"{areas.layers.LAYERS[i].label}: {a['error']}" for i, a in answers.items() if a["status"] == "error"]
    status = ("" if not failing else " Some sources failed this time: " + "; ".join(failing) + ".")
    if baseline:
        text = (f"{NO_NEW} Watch {label!r} started: {len(matching)} items match its rules {watch_area.describe()} "
                f"right now. They are recorded as the baseline; later checks report only new ones.{status}")
    elif not fresh:
        text = f"{NO_NEW} Watch {label!r}: nothing new {watch_area.describe()} since the last check.{status}"
    else:
        text = "\n".join([f"Watch {label!r}: {len(fresh)} new {watch_area.describe()}:"]
                         + [_line(watch_area, e) for e in fresh[:10]]
                         + ([f"(and {len(fresh) - 10} more)"] if len(fresh) > 10 else [])
                         + ([status.strip()] if status else []))
    return {"text": text, "new": len(fresh), "baseline": baseline, "count": len(matching),
            "sources": sorted({areas.layers.LAYERS[i].source for i in answers})}


def listing(state: State) -> list[dict]:
    """Every watch the index knows, newest check first, for the page."""
    out = []
    for watch_id in (state.get(INDEX_KEY) or {}):
        record = state.get(key(watch_id))
        if not record:
            continue
        out.append({"id": watch_id, "label": record.get("label"), "area": record.get("area"), "rules": record.get("rules"),
                    "checks": record.get("checks"), "created": record.get("created"),
                    "last_check": record.get("last_check"), "matching_now": record.get("matching_now"),
                    "alerts": record.get("alerts") or []})
    out.sort(key=lambda w: w.get("last_check") or 0, reverse=True)
    return out


def forget(state: State, watch_id: str) -> bool:
    """Drop a watch's record (the page removes its cron job separately)."""
    index = dict(state.get(INDEX_KEY) or {})
    existed = index.pop(watch_id, None) is not None or bool(state.get(key(watch_id)))
    state.set(key(watch_id), None)
    state.set(INDEX_KEY, index)
    return existed


def job_prompt(watch_id: str, args: dict, label: str) -> str:
    """The cron prompt a watch's scheduled job runs. Kept here so the page and the agent agree."""
    parts = [f'watch_id="{watch_id}"', f'label="{label}"']
    for name in ("country", "lat", "lon", "radius_km", "min_magnitude", "min_alert_level"):
        if args.get(name) not in (None, ""):
            value = args[name]
            parts.append(f'{name}="{value}"' if isinstance(value, str) else f"{name}={value}")
    if args.get("layers"):
        parts.append("layers=" + ",".join(args["layers"]))
    return (f"OSINT Map watch. Call the osint_watch tool once with {', '.join(parts)}. "
            f"If its result starts with {NO_NEW}, reply with exactly [SILENT] and nothing else. "
            "Otherwise write a short alert of at most 80 words: what happened, where relative to the watched area, "
            "when, and the source, with a link if the result has one. Do not call any other tool.")
