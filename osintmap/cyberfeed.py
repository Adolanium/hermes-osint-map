"""Cyber attacks layer, live feed: SSE parsing, event validation, and one shared upstream connection.

Check Point's public map streams Server-Sent Events from a single endpoint: ``attack`` events (one
detected attack, with source/destination geolocation) and ``counter`` events (global totals). The
endpoint is undocumented, so everything coming off the wire is treated as untrusted: malformed
events are counted and dropped, odd fields are nulled, and the buffer the Desktop polls is bounded.

``FeedHub`` keeps at most one upstream connection per gateway process, opens it only while a
Desktop page is polling, and closes it after ``idle_after`` seconds without a poll.
"""

from __future__ import annotations

import json
import math
import random
import re
import threading
import time
import uuid
from collections import deque
from contextlib import AbstractContextManager
from typing import Any, Callable, Iterable, Iterator

from .http import UpstreamError

ATTACK_TYPES = ("exploit", "malware", "phishing")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
MAX_NAME = 160
MAX_PLACE = 80
MAX_COUNT = 1_000_000
DUPLICATE_WINDOW_S = 2.0


# --- SSE -------------------------------------------------------------------------------------------


class SSEParser:
    """Incremental text/event-stream parser (WHATWG rules: fields in any order, ``:`` comments,
    multi-line ``data``, CR/LF/CRLF line ends, dispatch on a blank line). Feed it byte chunks split
    anywhere; it yields ``(event_type, data)`` pairs."""

    def __init__(self, max_event_bytes: int = 64 * 1024) -> None:
        self._buffer = b""
        self._event = ""
        self._data: list[str] = []
        self._size = 0
        self._oversized = False
        self.max_event_bytes = max_event_bytes
        self.retry_ms: int | None = None
        self.oversized = 0

    def feed(self, chunk: bytes) -> Iterator[tuple[str, str]]:
        self._buffer += chunk
        while True:
            cut = _line_end(self._buffer)
            if cut is None:
                if len(self._buffer) > self.max_event_bytes:  # a line that never ends: drop it
                    self._buffer = b""
                    self._oversized = True
                return
            end, skip = cut
            line = self._buffer[:end].decode("utf-8", errors="replace")
            self._buffer = self._buffer[end + skip :]
            event = self._line(line)
            if event is not None:
                yield event

    def _line(self, line: str) -> tuple[str, str] | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            return None
        field, sep, value = line.partition(":")
        if sep and value.startswith(" "):
            value = value[1:]
        if field == "event":
            self._event = value
        elif field == "data":
            self._size += len(value) + 1
            if self._size > self.max_event_bytes:
                self._oversized = True
            elif not self._oversized:
                self._data.append(value)
        elif field == "retry" and value.isdigit():
            self.retry_ms = int(value)
        return None

    def _dispatch(self) -> tuple[str, str] | None:
        event, data, oversized = self._event or "message", self._data, self._oversized
        self._event, self._data, self._size, self._oversized = "", [], 0, False
        if oversized:
            self.oversized += 1
            return None
        if not data:
            return None
        return event, "\n".join(data)


def _line_end(buffer: bytes) -> tuple[int, int] | None:
    lf = buffer.find(b"\n")
    cr = buffer.find(b"\r", 0, lf if lf != -1 else len(buffer))
    if cr == -1:
        return (lf, 1) if lf != -1 else None
    if cr + 1 == len(buffer):
        return None  # might be the CR of a CRLF split across chunks
    return cr, 2 if buffer[cr + 1] == 0x0A else 1


# --- Validation ------------------------------------------------------------------------------------


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split())
    if not cleaned or cleaned.lower() in {"null", "none", "unknown", "n/a"}:
        return None
    return cleaned[:limit]


def _number(value: Any, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    if not math.isfinite(number) or number < low or number > high:
        return None
    return round(number, 4)


def _country(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    return code if _COUNTRY.match(code) and code not in {"XX", "ZZ"} else None


def _place(raw: dict, prefix: str) -> dict:
    lat = _number(raw.get(f"{prefix}_la"), -90, 90)
    lon = _number(raw.get(f"{prefix}_lo"), -180, 180)
    if lat is None or lon is None or (lat == 0 and lon == 0):  # 0,0 is "no fix", not the Gulf of Guinea
        lat = lon = None
    # Check Point's client reads the source city from "s_io" (sic); accept both spellings.
    city = raw.get("d_ci") if prefix == "d" else raw.get("s_ci", raw.get("s_io"))
    return {
        "country": _country(raw.get(f"{prefix}_co")),
        "lat": lat,
        "lon": lon,
        "region": _text(raw.get(f"{prefix}_s"), MAX_PLACE),
        "city": _text(city, MAX_PLACE),
    }


def _source_time(value: Any) -> int | None:
    """Epoch milliseconds from the optional ``t`` field (seconds, milliseconds or ISO 8601)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        ms = value * 1000 if value < 1e11 else value
        return int(ms)
    if isinstance(value, str) and value.strip():
        from datetime import datetime

        try:
            return int(datetime.fromisoformat(value.strip().replace("Z", "+00:00")).timestamp() * 1000)
        except ValueError:
            return None
    return None


def normalize_attack(data: str) -> dict | None:
    """One ``attack`` payload → the plugin's event shape, or ``None`` when it is unusable.

    Unusable means: not a JSON object, or neither end has a country or coordinates (nothing to
    place). Missing names, counts and types degrade to safe defaults instead of failing."""
    try:
        raw = json.loads(data)
    except (ValueError, RecursionError):
        return None
    if not isinstance(raw, dict):
        return None
    src, dst = _place(raw, "s"), _place(raw, "d")
    if not (src["country"] or src["lat"] is not None) or not (dst["country"] or dst["lat"] is not None):
        return None
    kind = raw.get("a_t")
    kind = kind.strip().lower() if isinstance(kind, str) else ""
    count = _number(raw.get("a_c"), 1, MAX_COUNT)
    return {
        "type": kind if kind in ATTACK_TYPES else "other",
        "name": _text(raw.get("a_n"), MAX_NAME) or "Unnamed attack",
        "count": int(count) if count is not None else 1,
        "src": src,
        "dst": dst,
        "source_time": _source_time(raw.get("t")),
    }


def normalize_counter(data: str) -> dict | None:
    """One ``counter`` payload → ``{"today": int, "recent": [int, ...]}`` (oldest day first)."""
    try:
        raw = json.loads(data)
    except (ValueError, RecursionError):
        return None
    if not isinstance(raw, dict):
        return None
    today = _number(raw.get("today"), 0, 1e13)
    period = raw.get("recentPeriod")
    recent: list[int] = []
    if isinstance(period, list):
        for value in period[-62:]:
            number = _number(value, 0, 1e13)
            if number is None:
                recent = []
                break
            recent.append(int(number))
    if today is None and not recent:
        return None
    return {"today": int(today) if today is not None else None, "recent": recent}


def signature(event: dict) -> tuple:
    src, dst = event["src"], event["dst"]
    return (event["type"], event["name"], event["count"], src["country"], src["lat"], src["lon"],
            dst["country"], dst["lat"], dst["lon"])


# --- Buffer ----------------------------------------------------------------------------------------


class EventBuffer:
    """Bounded, sequence-numbered event log that pollers read with an ``after`` cursor.

    Identical payloads within ``DUPLICATE_WINDOW_S`` (the feed has no event ids, and does repeat
    itself in bursts) are kept, but marked ``repeat_of`` the first one so the UI can fold them
    into one row instead of drawing ten identical arcs."""

    def __init__(self, size: int = 2000, clock: Callable[[], float] = time.monotonic) -> None:
        self._events: deque[dict] = deque(maxlen=size)
        self._recent: dict[tuple, tuple[int, float]] = {}
        self._clock = clock
        self.seq = 0

    def add(self, event: dict, received_ms: int) -> dict:
        now = self._clock()
        self.seq += 1
        key = signature(event)
        first = self._recent.get(key)
        repeat_of = first[0] if first and now - first[1] <= DUPLICATE_WINDOW_S else None
        if repeat_of is None:
            self._recent[key] = (self.seq, now)
        if len(self._recent) > 512:
            self._recent = {k: v for k, v in self._recent.items() if now - v[1] <= DUPLICATE_WINDOW_S}
        stored = {**event, "seq": self.seq, "received": received_ms, "repeat_of": repeat_of}
        self._events.append(stored)
        return stored

    def after(self, cursor: int, limit: int) -> tuple[list[dict], bool]:
        """Events with ``seq > cursor`` (oldest first, at most ``limit``, newest kept when cut)
        and whether events between the cursor and the oldest returned one were lost."""
        if not self._events:
            return [], False
        oldest = self._events[0]["seq"]
        fresh = [e for e in self._events if e["seq"] > cursor] if cursor >= oldest else list(self._events)
        gap = cursor < oldest - 1 and cursor >= 0
        if len(fresh) > limit:
            fresh = fresh[-limit:]
            gap = True
        return fresh, gap


# --- Hub -------------------------------------------------------------------------------------------

StreamOpener = Callable[[], AbstractContextManager[Iterable[bytes]]]


def backoff_delay(failures: int, base: float = 2.0, cap: float = 60.0, rand: Callable[[], float] = random.random) -> float:
    """Exponential backoff with full jitter on the upper half: failures 1, 2, 3 → ~2, 4, 8 s, capped."""
    ceiling = min(cap, base * (2 ** max(0, failures - 1)))
    return ceiling / 2 + rand() * ceiling / 2


class FeedHub:
    """One upstream SSE connection shared by every Desktop poller in this process."""

    def __init__(
        self,
        open_stream: StreamOpener,
        *,
        idle_after: float = 45.0,
        buffer_size: int = 2000,
        max_per_second: int = 80,
        clock: Callable[[], float] = time.monotonic,
        wall_ms: Callable[[], int] = lambda: int(time.time() * 1000),
        backoff: Callable[[int], float] = backoff_delay,
    ) -> None:
        self._open = open_stream
        self._clock = clock
        self._wall_ms = wall_ms
        self._backoff = backoff
        self.idle_after = idle_after
        self.max_per_second = max_per_second
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._last_poll = 0.0
        self._buffer = EventBuffer(buffer_size, clock)
        self.stream_id = uuid.uuid4().hex[:12]
        self._state = "idle"
        self._error: str | None = None
        self._retry_at_ms: int | None = None
        self._connected_at_ms: int | None = None
        self._last_event_ms: int | None = None
        self._failures = 0
        self._unavailable = 0
        self._counter: dict | None = None
        self._stats = {"received": 0, "malformed": 0, "rate_limited": 0, "reconnects": 0}
        self._second = (0, 0)

    # Public API ---------------------------------------------------------------------------------

    def poll(self, after: int, limit: int = 500) -> dict:
        """Mark a client as active (opening the upstream if needed) and return what it hasn't seen."""
        with self._lock:
            self._last_poll = self._clock()
            self._stopping = False
            if self._thread is None or not self._thread.is_alive():
                self._state, self._error = "connecting", None
                self._thread = threading.Thread(target=self._run, name="osint-map-cyber", daemon=True)
                self._thread.start()
            events, gap = self._buffer.after(after, max(1, min(limit, 1000)))
            return {**self._status_locked(), "events": events, "gap": gap}

    def status(self) -> dict:
        with self._lock:
            return self._status_locked()

    def stop(self) -> None:
        """Close the upstream now instead of waiting for the idle timeout. The state turns "idle"
        when the worker exits; a poll that arrives first simply keeps the connection."""
        with self._lock:
            self._stopping = True
        self._wake.set()

    def join(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # Worker -------------------------------------------------------------------------------------

    def _status_locked(self) -> dict:
        return {
            "stream_id": self.stream_id,
            "state": self._state,
            "error": self._error,
            "retry_at": self._retry_at_ms,
            "connected_at": self._connected_at_ms,
            "last_event_at": self._last_event_ms,
            "seq": self._buffer.seq,
            "counter": self._counter,
            "stats": dict(self._stats),
        }

    def _idle(self) -> bool:
        return self._stopping or self._clock() - self._last_poll > self.idle_after

    def _run(self) -> None:
        while True:
            with self._lock:
                if self._idle():
                    self._state, self._connected_at_ms, self._retry_at_ms = "idle", None, None
                    self._thread = None
                    return
                self._state = "connecting" if self._failures == 0 else "reconnecting"
            parser = SSEParser()
            delay: float
            try:
                with self._open() as chunks:
                    with self._lock:
                        self._state, self._error, self._retry_at_ms = "live", None, None
                        self._connected_at_ms = self._wall_ms()
                    for chunk in chunks:
                        for kind, data in parser.feed(chunk):
                            self._handle(kind, data)
                        if self._idle():
                            break
                # The server closed the stream (or we went idle). Honour its retry hint.
                self._failures = 0
                self._unavailable = 0
                delay = max(1.0, (parser.retry_ms or 10_000) / 1000) * (0.8 + random.random() * 0.4)
                error = None if self._idle() else "The feed closed the connection."
            except UpstreamError as exc:
                self._failures += 1
                if exc.status in (401, 403, 404, 410):
                    self._unavailable += 1
                error = str(exc)
                delay = self._backoff(self._failures) if self._unavailable < 3 else 300.0
            except Exception as exc:  # network errors, timeouts (stall), anything else
                self._failures += 1
                error = _describe(exc)
                delay = self._backoff(self._failures)
            with self._lock:
                self._connected_at_ms = None
                self._stats["malformed"] += parser.oversized
                if self._idle():
                    continue
                self._stats["reconnects"] += 1
                self._error = error
                self._state = "unavailable" if self._unavailable >= 3 else "reconnecting"
                self._retry_at_ms = self._wall_ms() + int(delay * 1000)
            self._wake.clear()
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline and not self._idle():
                self._wake.wait(min(0.25, deadline - time.monotonic()))

    def _handle(self, kind: str, data: str) -> None:
        now_ms = self._wall_ms()
        if kind == "attack":
            event = normalize_attack(data)
            with self._lock:
                if event is None:
                    self._stats["malformed"] += 1
                    return
                second = int(self._clock())
                count = self._second[1] + 1 if self._second[0] == second else 1
                self._second = (second, count)
                if count > self.max_per_second:
                    self._stats["rate_limited"] += 1
                    return
                self._stats["received"] += 1
                self._last_event_ms = now_ms
                self._buffer.add(event, now_ms)
        elif kind == "counter":
            counter = normalize_counter(data)
            with self._lock:
                if counter is None:
                    self._stats["malformed"] += 1
                    return
                self._counter = {**counter, "received": now_ms}


def _describe(exc: Exception) -> str:
    name = type(exc).__name__
    if "Timeout" in name:
        return "The feed went quiet (no data or keep-alive for too long)."
    if "Connect" in name:
        return "Could not reach the feed."
    text = str(exc).strip()
    return f"{name}: {text}"[:200] if text else name
