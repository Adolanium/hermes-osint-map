"""Cyber attacks layer: HTTP access to the endpoints behind threatmap.checkpoint.com.

None of these endpoints are documented or offered as a public API; they are what Check Point's own
map page calls. Requests carry an honest User-Agent, country and top-stat lookups are cached, and
the attack stream is opened only by ``FeedHub`` while a Desktop page is watching.
"""

from __future__ import annotations

import math
import re
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import httpx

from .http import USER_AGENT, UpstreamError

API_BASE = "https://threatmap-api.checkpoint.com/ThreatMap/api/"
FEED_URL = API_BASE + "feed"
STALL_SECONDS = 45.0
_COUNTRY = re.compile(r"^[A-Z]{2}$")


@contextmanager
def open_feed() -> Iterator[Iterator[bytes]]:
    """Open the SSE stream. A read that waits longer than ``STALL_SECONDS`` (the server sends
    keep-alive comments every few seconds) raises ``httpx.ReadTimeout``, which the hub treats as a
    stalled stream and reconnects."""
    timeout = httpx.Timeout(connect=10.0, read=STALL_SECONDS, write=10.0, pool=10.0)
    headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache", "User-Agent": USER_AGENT}
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=False) as client:
        with client.stream("GET", FEED_URL) as response:
            if response.status_code != 200:
                raise UpstreamError(response.status_code, f"The feed answered HTTP {response.status_code}.")
            kind = response.headers.get("content-type", "")
            if "text/event-stream" not in kind:
                raise UpstreamError(502, f"The feed answered {kind or 'no content type'}, not an event stream.")
            yield response.iter_bytes()


class TTLCache:
    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic, size: int = 64) -> None:
        self.ttl, self._clock, self._size = ttl, clock, size
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_load(self, key: str, load: Callable[[], Any]) -> Any:
        with self._lock:
            hit = self._items.get(key)
            if hit and self._clock() - hit[0] < self.ttl:
                return hit[1]
        value = load()
        with self._lock:
            if len(self._items) >= self._size:
                self._items.pop(min(self._items, key=lambda k: self._items[k][0]))
            self._items[key] = (self._clock(), value)
        return value


def _get_json(url: str) -> Any:
    try:
        response = httpx.get(url, timeout=15.0, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise UpstreamError(503, f"Could not reach Check Point: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise UpstreamError(response.status_code, f"Check Point answered HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamError(502, "Check Point answered something that is not JSON.") from exc


def _series(values: Any, limit: int = 62) -> list[float] | None:
    if not isinstance(values, list) or not values or len(values) > limit:
        return None
    out = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        out.append(float(value))
    return out


def _dates(values: Any, length: int) -> list[str] | None:
    if not isinstance(values, list) or len(values) != length:
        return None
    if not all(isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v) for v in values):
        return None
    return list(values)


def parse_country(raw: Any) -> dict:
    """Check Point's per-country payload → chronological series. Both series are newest-first on
    the wire. ``trend`` is the country's daily attack trend; ``malware_types`` is the share (%) of
    the country's organizations affected per malware family."""
    result: dict = {"trend": None, "malware_types": None}
    if not isinstance(raw, dict):
        return result
    trend = raw.get("trend")
    if isinstance(trend, dict):
        attacks = _series(trend.get("attacks"))
        dates = _dates(trend.get("dates"), len(attacks)) if attacks else None
        if attacks and dates:
            result["trend"] = {"dates": dates[::-1], "values": attacks[::-1]}
    types = raw.get("malware_types")
    if isinstance(types, dict) and isinstance(types.get("attacksByType"), dict):
        by_type = {}
        for name, values in list(types["attacksByType"].items())[:12]:
            series = _series(values)
            if isinstance(name, str) and series:
                by_type[name[:40]] = series[::-1]
        lengths = {len(v) for v in by_type.values()}
        dates = _dates(types.get("dates"), lengths.pop()) if len(lengths) == 1 else None
        if by_type and dates:
            result["malware_types"] = {"dates": dates[::-1], "series": by_type}
    return result


def parse_top(raw: Any) -> dict:
    """``topStats`` → ranked lists of ``{"id", "fraction"}`` (fraction of the leader, 0..1)."""
    out: dict = {}
    for key in ("countries", "industries", "malwareTypes"):
        rows = []
        for item in (raw.get(key) if isinstance(raw, dict) else None) or []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            fraction = item.get("fraction")
            if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 <= fraction <= 1:
                continue
            rows.append({"id": item["id"][:40], "fraction": round(float(fraction), 4)})
        out[key] = rows[:10]
    return out


_country_cache = TTLCache(30 * 60)
_top_cache = TTLCache(15 * 60)


def country(code: str) -> dict:
    code = code.upper()
    if not _COUNTRY.match(code):
        raise UpstreamError(400, "Country codes are two letters, like US.")
    return _country_cache.get_or_load(code, lambda: {"code": code, **parse_country(_get_json(f"{API_BASE}countries/{code}"))})


def top_stats() -> dict:
    return _top_cache.get_or_load("top", lambda: parse_top(_get_json(API_BASE + "topStats")))
