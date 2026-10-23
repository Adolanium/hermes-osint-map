"""Shared HTTP access, caches, errors and the SSRF guard.

Every upstream in this plugin is a public, keyless (or optionally keyed) endpoint run by someone
else, so requests carry an honest User-Agent, bodies are size-capped while they stream, and every
failure becomes an ``UpstreamError`` with a sentence a person can read. Nothing here logs request
URLs or headers: the one optional key (Twelve Data) travels in a header and never in a message.

Two caches cover the two ways the plugin reads upstreams:

- ``SnapshotCache`` serves layers and panels. A failed refresh keeps the last good value and reports
  it as ``stale``; the page shows the age instead of going blank.
- ``TTLCache`` serves one-off lookups (recon, country facts). Failures are not cached.

The SSRF guard is for the two recon kinds that connect to hosts a user typed (``tls`` and
``headers``): resolve first, refuse anything that is not a public unicast address, then connect to
the address that was checked, so a second DNS answer cannot swap in a private one.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import threading
import time
from collections import OrderedDict
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "hermes-osint-map/0.1 (+https://github.com/Adolanium/hermes-osint-map)"
DEFAULT_TIMEOUT = 15.0
MAX_BODY = 8 * 1024 * 1024


class UpstreamError(Exception):
    """An upstream (or the caller's input) made the request fail. ``status`` is an HTTP-style code
    for the page: 400 bad input, 403 refused by the guard, 404 not found, 429 rate limited,
    502 bad answer, 503 unreachable, 504 timed out."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def now_ms() -> int:
    return int(time.time() * 1000)


def describe(exc: BaseException, source: str = "the source") -> str:
    """A plain sentence for an unexpected exception. Never includes the request URL."""
    if isinstance(exc, UpstreamError):
        return str(exc)
    name = type(exc).__name__
    if "Timeout" in name or isinstance(exc, TimeoutError):
        return f"{source} took too long to answer."
    if "Connect" in name or isinstance(exc, (ConnectionError, socket.gaierror)):
        return f"Could not reach {source}."
    return f"{source} failed unexpectedly ({name})."


# --- Caches ----------------------------------------------------------------------------------------


class TTLCache:
    """Thread-safe, bounded cache of successful loads. A load that raises is not stored.

    Concurrent callers of the same key wait for one load instead of each hitting the upstream."""

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic, size: int = 128) -> None:
        self.ttl, self._clock, self._size = ttl, clock, size
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                if len(self._key_locks) > self._size * 4:  # forget locks nobody holds
                    self._key_locks = {k: v for k, v in self._key_locks.items() if v.locked()}
                lock = self._key_locks[key] = threading.Lock()
            return lock

    def _hit(self, key: str) -> tuple[bool, Any]:
        with self._lock:
            hit = self._items.get(key)
            if hit and self._clock() - hit[0] < self.ttl:
                self._items.move_to_end(key)
                return True, hit[1]
        return False, None

    def get_or_load(self, key: str, load: Callable[[], Any]) -> Any:
        found, value = self._hit(key)
        if found:
            return value
        with self._key_lock(key):
            found, value = self._hit(key)  # someone else loaded it while we waited
            if found:
                return value
            value = load()
            with self._lock:
                self._items[key] = (self._clock(), value)
                self._items.move_to_end(key)
                while len(self._items) > self._size:
                    self._items.popitem(last=False)
            return value


class SnapshotCache:
    """Stale-while-error cache for polled sources.

    ``fetch`` returns ``{"value", "updated", "status", "error"}`` and never raises: ``ok`` is a
    fresh value, ``stale`` is the last good value after a failed refresh, ``error`` means there has
    never been a good value (``value`` is then ``None``). After a failure the source is left alone
    for ``retry_after`` seconds (default ``min(ttl, 60)``) so a polling page cannot hammer an
    upstream that is already struggling."""

    def __init__(self, size: int = 256, clock: Callable[[], float] = time.monotonic,
                 wall_ms: Callable[[], int] = now_ms) -> None:
        self._clock, self._wall_ms, self._size = clock, wall_ms, size
        self._entries: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

    def _key_lock(self, key: str) -> threading.Lock:
        with self._lock:
            lock = self._key_locks.get(key)
            if lock is None:
                if len(self._key_locks) > self._size * 4:
                    self._key_locks = {k: v for k, v in self._key_locks.items() if v.locked()}
                lock = self._key_locks[key] = threading.Lock()
            return lock

    @staticmethod
    def _view(entry: dict | None) -> dict:
        if entry is None:
            return {"value": None, "updated": None, "status": "idle", "error": None}
        has_value = entry["loaded_at"] is not None
        if entry["error"] is None:
            status = "ok"
        else:
            status = "stale" if has_value else "error"
        return {"value": entry["value"], "updated": entry["updated"], "status": status, "error": entry["error"]}

    def peek(self, key: str) -> dict:
        with self._lock:
            return self._view(self._entries.get(key))

    def fetch(self, key: str, ttl: float, load: Callable[[], Any], *, source: str = "the source",
              retry_after: float | None = None) -> dict:
        retry_after = min(ttl, 60.0) if retry_after is None else retry_after
        with self._key_lock(key):
            with self._lock:
                entry = self._entries.get(key)
                now = self._clock()
                if entry is not None:
                    fresh = entry["loaded_at"] is not None and now - entry["loaded_at"] < ttl
                    resting = entry["failed_at"] is not None and now - entry["failed_at"] < retry_after
                    if (fresh and entry["error"] is None) or resting:
                        self._entries.move_to_end(key)
                        return self._view(entry)
            try:
                value, error = load(), None
            except Exception as exc:  # every failure becomes a sentence; the old value survives
                value, error = None, describe(exc, source)
            with self._lock:
                entry = self._entries.get(key) or {"value": None, "loaded_at": None, "updated": None,
                                                   "error": None, "failed_at": None}
                if error is None:
                    entry.update(value=value, loaded_at=self._clock(), updated=self._wall_ms(),
                                 error=None, failed_at=None)
                else:
                    entry.update(error=error, failed_at=self._clock())
                self._entries[key] = entry
                self._entries.move_to_end(key)
                while len(self._entries) > self._size:
                    self._entries.popitem(last=False)
                return self._view(entry)


# --- HTTP ------------------------------------------------------------------------------------------

_client_lock = threading.Lock()
_client: httpx.Client | None = None


def client() -> httpx.Client:
    """One pooled client per process; httpx.Client is safe to share between threads."""
    global _client
    with _client_lock:
        if _client is None:
            _client = httpx.Client(
                headers={"User-Agent": USER_AGENT},
                timeout=httpx.Timeout(DEFAULT_TIMEOUT, connect=10.0),
                follow_redirects=False,
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            )
        return _client


class Response:
    """The parts of an HTTP answer this plugin uses (the body is already read and capped)."""

    def __init__(self, status: int, headers: httpx.Headers, body: bytes, url: str) -> None:
        self.status, self.headers, self.body, self.url = status, headers, body, url

    def text(self) -> str:
        charset = "utf-8"
        match = re.search(r"charset=([\w-]+)", self.headers.get("content-type", ""), re.I)
        if match:
            charset = match.group(1)
        try:
            return self.body.decode(charset, errors="replace")
        except LookupError:
            return self.body.decode("utf-8", errors="replace")

    def json(self, source: str) -> Any:
        try:
            return json.loads(self.body)
        except (ValueError, RecursionError) as exc:
            raise UpstreamError(502, f"{source} answered something that is not JSON.") from exc


def _status_error(status: int, source: str) -> UpstreamError:
    if status == 404:
        return UpstreamError(404, f"{source} has no record of that.")
    if status == 429:
        return UpstreamError(429, f"{source} is rate limiting requests; try again in a minute.")
    if status in (401, 403):
        return UpstreamError(status, f"{source} refused the request (HTTP {status}).")
    if status >= 500:
        return UpstreamError(503, f"{source} is having trouble (HTTP {status}).")
    return UpstreamError(502, f"{source} answered HTTP {status}.")


def get(url: str, *, source: str, params: dict | None = None, headers: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT, max_bytes: int = MAX_BODY, redirects: int = 0,
        guard_redirects: bool = False, ok_statuses: tuple[int, ...] = (200,)) -> Response:
    """GET ``url`` and return the capped body. ``redirects`` hops are followed by hand; with
    ``guard_redirects`` each new host must resolve to public addresses (for upstreams like
    rdap.org that send us on to hosts they choose)."""
    current, hops = url, 0
    while True:
        try:
            with client().stream("GET", current, params=params if hops == 0 else None,
                                 headers=headers, timeout=timeout) as response:
                status = response.status_code
                if status in (301, 302, 303, 307, 308) and "location" in response.headers:
                    if hops >= redirects:
                        raise UpstreamError(502, f"{source} redirected too many times.")
                    target = urljoin(str(response.url), response.headers["location"])
                    parts = urlsplit(target)
                    if parts.scheme not in ("http", "https") or not parts.hostname:
                        raise UpstreamError(502, f"{source} redirected somewhere unusable.")
                    if guard_redirects:
                        resolve_public(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
                    current, hops = target, hops + 1
                    continue
                if status not in ok_statuses:
                    raise _status_error(status, source)
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    raise UpstreamError(502, f"{source} answered more data than this plugin accepts.")
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise UpstreamError(502, f"{source} answered more data than this plugin accepts.")
                    chunks.append(chunk)
                return Response(status, response.headers, b"".join(chunks), str(response.url))
        except UpstreamError:
            raise
        except httpx.TimeoutException as exc:
            raise UpstreamError(504, f"{source} took too long to answer.") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(503, f"Could not reach {source} ({type(exc).__name__}).") from exc


def get_json(url: str, *, source: str, **kwargs: Any) -> Any:
    headers = {"Accept": "application/json", **(kwargs.pop("headers", None) or {})}
    return get(url, source=source, headers=headers, **kwargs).json(source)


# --- Target validation and the SSRF guard ---------------------------------------------------------

_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_TLD = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_BENCHMARK = ipaddress.ip_network("198.18.0.0/15")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_NAT64_LOCAL = ipaddress.ip_network("64:ff9b:1::/48")


def parse_domain(text: str) -> str | None:
    """A registrable-looking DNS name in ASCII (IDNA) form, or ``None``. Rejects IP literals,
    single labels (``localhost``), ports, paths and anything with odd characters."""
    if not isinstance(text, str):
        return None
    name = text.strip().lower().rstrip(".")
    if not name or len(name) > 253 or any(c in name for c in "/:@ \t\\?#[]%"):
        return None
    try:
        name = name.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = name.split(".")
    if len(labels) < 2 or not all(_LABEL.match(label) for label in labels) or not _TLD.match(labels[-1]):
        return None
    return name


def parse_ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not isinstance(text, str):
        return None
    try:
        return ipaddress.ip_address(text.strip().strip("[]"))
    except ValueError:
        return None


def blocked_reason(address: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why an address must not be connected to from the gateway, or ``None`` when it is public.

    IPv6 forms that embed an IPv4 address (mapped ``::ffff:a.b.c.d``, 6to4, Teredo, NAT64) are
    judged by the address they carry, so ``::ffff:127.0.0.1`` is loopback, not "some IPv6"."""
    try:
        ip = ipaddress.ip_address(address) if isinstance(address, str) else address
    except ValueError:
        return "not an IP address"
    if ip.version == 6:
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip.teredo:
            embedded = ip.teredo[1]
        if embedded is None and (ip in _NAT64 or ip in _NAT64_LOCAL):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            inner = blocked_reason(embedded)
            return f"{inner} (inside an IPv6 address)" if inner else None
        if ip.is_site_local:
            return "a site-local address"
    if ip.is_loopback:
        return "a loopback address"
    if ip.is_link_local:
        return "a link-local address"
    if ip.is_multicast:
        return "a multicast address"
    if ip.is_unspecified:
        return "an unspecified address"
    if ip.version == 4 and ip in _CGNAT:
        return "a carrier-grade NAT (shared) address"
    if ip.version == 4 and ip in _BENCHMARK:
        return "a benchmarking address"
    if ip.is_reserved:
        return "a reserved address"
    if ip.is_private:
        return "a private address"
    if not ip.is_global:
        return "not a public address"
    return None


Resolver = Callable[[str, int], list[str]]


def _system_resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        address = info[4][0].split("%", 1)[0]
        if address not in seen:
            seen.append(address)
    return seen


def resolve_public(host: str, port: int, resolver: Resolver | None = None) -> list[str]:
    """Resolve ``host`` and return its addresses, refusing if *any* of them is not public (a name
    that answers both a public and a private address is treated as an attempt, not a mix)."""
    literal = parse_ip(host)
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = (resolver or _system_resolve)(host, port)
        except (socket.gaierror, UnicodeError, OSError) as exc:
            raise UpstreamError(404, f"{host} does not resolve in DNS.") from exc
    if not addresses:
        raise UpstreamError(404, f"{host} does not resolve in DNS.")
    for address in addresses:
        reason = blocked_reason(address)
        if reason:
            raise UpstreamError(403, f"Refusing to connect: {host} points to {reason} ({address}).")
    return addresses


def open_guarded(host: str, port: int, timeout: float, resolver: Resolver | None = None) -> tuple[socket.socket, str]:
    """A TCP connection to the first reachable checked address of ``host``. Returns the socket and
    the address it connected to."""
    last: Exception | None = None
    for address in resolve_public(host, port, resolver)[:4]:
        try:
            return socket.create_connection((address, port), timeout=timeout), address
        except OSError as exc:
            last = exc
    if isinstance(last, TimeoutError) or isinstance(last, socket.timeout):
        raise UpstreamError(504, f"{host}:{port} did not answer in time.")
    raise UpstreamError(503, f"Could not connect to {host}:{port}.")
