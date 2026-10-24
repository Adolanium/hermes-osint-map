"""Side panels: merged headlines, space weather, markets and country facts.

Each panel reads a handful of public sources through the same stale-while-error cache the layers
use. A panel answers as long as one of its sources has something to show; it raises
``UpstreamError`` only when there is nothing at all, and otherwise reports which sources failed in
``status``/``error`` (and per source where there are several).
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from typing import Any

from . import http
from .http import SnapshotCache, TTLCache, UpstreamError
from .parsers import iso_ms, number, strip_tags, text

_cache = SnapshotCache(size=32)

# --- News ------------------------------------------------------------------------------------------

FEEDS = (
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("DW", "https://rss.dw.com/xml/rss-en-world"),
    ("The Guardian", "https://www.theguardian.com/world/rss"),
    ("France 24", "https://www.france24.com/en/rss"),
)
NEWS_TTL = 600
MAX_PER_FEED = 100
MAX_SUMMARY = 300


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _child_text(node: ET.Element, *names: str) -> str | None:
    for child in node:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text
    return None


def _feed_time(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = parsedate_to_datetime(value)  # RFC 822, what RSS uses
        if parsed is not None:
            return int(parsed.timestamp() * 1000)
    except (TypeError, ValueError, IndexError):
        pass
    return iso_ms(value)  # Atom and Dublin Core dates


def _link(node: ET.Element) -> str | None:
    for child in node:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        if href and child.get("rel", "alternate") == "alternate":
            return href.strip()
        if (child.text or "").strip():
            return child.text.strip()
    guid = _child_text(node, "guid")
    return guid.strip() if guid and guid.strip().startswith("http") else None


def parse_feed(body: bytes | str, source: str) -> list[dict]:
    """RSS 2.0, RSS 1.0 (RDF) or Atom → headline dicts in plain text. Markup in titles and
    summaries (common in RSS descriptions) is stripped, entities decoded."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise UpstreamError(502, f"{source} answered a feed this plugin could not read.") from exc
    out = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        title = text(strip_tags(_child_text(node, "title") or ""), 200)
        url = _link(node)
        if not title or not url or not url.startswith(("https://", "http://")):
            continue
        summary = _child_text(node, "description", "summary", "content", "encoded")
        when = _feed_time(_child_text(node, "pubdate", "date", "published", "updated"))
        out.append({
            "id": "news:" + hashlib.sha1(url.encode()).hexdigest()[:12],
            "title": title,
            "url": url[:500],
            "source": source,
            "time": when,
            "summary": text(strip_tags(summary or ""), MAX_SUMMARY),
        })
        if len(out) >= MAX_PER_FEED:
            break
    return out


def merge_headlines(lists: list[list[dict]]) -> list[dict]:
    """Newest first, duplicates (same link or same title) folded."""
    seen_urls, seen_titles, out = set(), set(), []
    for entry in sorted((e for lst in lists for e in lst), key=lambda e: e["time"] or 0, reverse=True):
        url_key = entry["url"].split("?", 1)[0].rstrip("/")
        title_key = re.sub(r"\W+", " ", entry["title"].lower()).strip()
        if url_key in seen_urls or title_key in seen_titles:
            continue
        seen_urls.add(url_key)
        seen_titles.add(title_key)
        out.append(entry)
    return out


def _load_feed(source: str, url: str):
    return lambda: parse_feed(http.get(url, source=source, redirects=3, timeout=15.0, max_bytes=4 * 1024 * 1024).body, source)


def _clamp_limit(limit: Any, default: int, high: int) -> int:
    value = number(limit, 1, 10_000) if limit not in (None, "") else default
    return int(min(high, value)) if value is not None else default


_STOPWORDS = {"a", "an", "and", "the", "of", "in", "on", "at", "to", "for", "with", "is", "are", "or", "by", "from"}


def search_headlines(entries: list[dict], q: str | None) -> list[dict]:
    """Headlines matching ``q``: every word, in any order (title or summary). When no headline has
    them all, the ones with the most words matched, best first. Order is otherwise preserved."""
    words = [w for w in re.findall(r"[\w'-]+", (q or "").lower()[:100]) if w not in _STOPWORDS]
    if not words:
        return entries
    scored = []
    for entry in entries:
        hay = f"{entry['title']} {entry.get('summary') or ''}".lower()
        hits = sum(1 for w in words if re.search(rf"(?<![\w]){re.escape(w)}", hay))
        if hits:
            scored.append((hits, entry))
    full = [e for hits, e in scored if hits == len(words)]
    if full:
        return full
    return [e for _, e in sorted(scored, key=lambda pair: -pair[0])]


def news(q: str | None = None, limit: Any = None) -> dict:
    with ThreadPoolExecutor(max_workers=len(FEEDS), thread_name_prefix="osint-news") as pool:
        snaps = list(pool.map(lambda f: _cache.fetch(f"news:{f[0]}", NEWS_TTL, _load_feed(*f), source=f[0]), FEEDS))
    feeds = [{"source": name, "status": s["status"], "error": s["error"], "count": len(s["value"] or [])}
             for (name, _), s in zip(FEEDS, snaps)]
    with_data = [s for s in snaps if s["value"] is not None]
    if not with_data:
        raise UpstreamError(503, "None of the news feeds answered. " + " ".join(f["error"] or "" for f in feeds).strip())
    merged = merge_headlines([s["value"] for s in with_data])
    merged = search_headlines(merged, q)
    errors = [f"{f['source']}: {f['error']}" for f in feeds if f["error"]]
    return {
        "updated": min(s["updated"] for s in with_data),
        "status": "ok" if not errors else "stale",
        "error": " ".join(errors) or None,
        "items": merged[: _clamp_limit(limit, 50, 200)],
        "feeds": feeds,
    }


# --- Space weather ---------------------------------------------------------------------------------

SWPC = "https://services.swpc.noaa.gov"
KP_3H = SWPC + "/products/noaa-planetary-k-index.json"
KP_1M = SWPC + "/json/planetary_k_index_1m.json"
SCALES = SWPC + "/products/noaa-scales.json"
ALERTS = SWPC + "/products/alerts.json"
WEEK_MS = 7 * 24 * 3600 * 1000


def parse_kp(raw: Any) -> list[dict]:
    """The 3-hourly planetary Kp product, last 7 days. SWPC has served it both as a table (a
    header row, then rows) and as a list of objects; both are accepted."""
    rows: list[tuple[Any, Any]] = []
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        header = [str(h).lower() for h in raw[0]]
        if "time_tag" in header and "kp" in header:
            t, k = header.index("time_tag"), header.index("kp")
            rows = [(r[t], r[k]) for r in raw[1:] if isinstance(r, list) and len(r) > max(t, k)]
    elif isinstance(raw, list):
        rows = [(r.get("time_tag"), r.get("Kp", r.get("kp"))) for r in raw if isinstance(r, dict)]
    points = []
    for when, kp in rows:
        ms, value = iso_ms(when), number(kp, 0, 9)
        if ms is not None and value is not None:
            points.append({"time": ms, "kp": round(value, 2)})
    points.sort(key=lambda p: p["time"])
    if points:
        newest = points[-1]["time"]
        points = [p for p in points if p["time"] > newest - WEEK_MS]
    return points


def parse_kp_now(raw: Any) -> float | None:
    """The newest 1-minute estimated Kp."""
    for row in reversed(raw if isinstance(raw, list) else []):
        if isinstance(row, dict):
            value = number(row.get("estimated_kp", row.get("kp_index")), 0, 9)
            if value is not None:
                return round(value, 2)
    return None


def parse_scales(raw: Any) -> dict:
    """Current NOAA R/S/G scale levels (entry ``"0"`` of noaa-scales.json)."""
    current = raw.get("0") if isinstance(raw, dict) else None
    out: dict[str, int | None] = {"R": None, "S": None, "G": None}
    if isinstance(current, dict):
        for key in out:
            scale = current.get(key)
            value = number(scale.get("Scale"), 0, 5) if isinstance(scale, dict) else None
            out[key] = int(value) if value is not None else None
    return out


_SKIP_LINES = re.compile(r"^(space weather message code|serial number|issue time|extension to serial)", re.I)


def parse_alerts(raw: Any, limit: int = 8) -> list[dict]:
    """SWPC alerts/warnings/watches, newest first: message code and the message's headline line."""
    out = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or not isinstance(entry.get("message"), str):
            continue
        message = entry["message"]
        match = re.search(r"Space Weather Message Code:\s*([A-Z0-9]+)", message)
        code = match.group(1) if match else text(entry.get("product_id"), 12)
        headline = None
        for line in message.replace("\r", "\n").split("\n"):
            line = line.strip()
            if line and not _SKIP_LINES.match(line):
                headline = text(line, 160)
                break
        when = iso_ms(entry.get("issue_datetime"))
        if headline and when:
            out.append({"time": when, "code": code, "headline": headline})
    out.sort(key=lambda a: a["time"], reverse=True)
    return out[:limit]


def space_weather() -> dict:
    sources = (("kp", KP_3H, parse_kp, []), ("kp_now", KP_1M, parse_kp_now, None),
               ("scales", SCALES, parse_scales, {"R": None, "S": None, "G": None}), ("alerts", ALERTS, parse_alerts, []))

    def load(url: str, parse):
        return lambda: parse(http.get_json(url, source="NOAA SWPC", max_bytes=4 * 1024 * 1024))

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="osint-swpc") as pool:
        snaps = list(pool.map(lambda s: _cache.fetch(f"swpc:{s[0]}", 300, load(s[1], s[2]), source="NOAA SWPC"), sources))
    if all(s["value"] is None for s in snaps):
        raise UpstreamError(503, snaps[0]["error"] or "NOAA SWPC did not answer.")
    result: dict[str, Any] = {}
    for (name, _, _, empty), snap in zip(sources, snaps):
        result[name] = snap["value"] if snap["value"] is not None else empty
    if result["kp_now"] is None and result["kp"]:
        result["kp_now"] = result["kp"][-1]["kp"]
    errors = sorted({s["error"] for s in snaps if s["error"]})
    with_data = [s for s in snaps if s["updated"]]
    return {"updated": min(s["updated"] for s in with_data), "status": "ok" if not errors else "stale",
            "error": " ".join(errors) or None, **result}


# --- Markets ---------------------------------------------------------------------------------------

COINGECKO = "https://api.coingecko.com/api/v3/simple/price"
TWELVEDATA = "https://api.twelvedata.com/quote"
TWELVEDATA_ENV = "TWELVEDATA_API_KEY"
CRYPTO = (("bitcoin", "BTC", "Bitcoin"), ("ethereum", "ETH", "Ether"), ("solana", "SOL", "Solana"))
# group id, label, [(display symbol, label, Twelve Data symbol)]
EQUITY_GROUPS = (
    ("defense", "Defense", (("LMT", "Lockheed Martin", "LMT"), ("RTX", "RTX", "RTX"),
                            ("NOC", "Northrop Grumman", "NOC"), ("GD", "General Dynamics", "GD"),
                            ("BA", "Boeing", "BA"))),
    ("energy", "Energy", (("CL", "WTI crude", "WTI/USD"), ("BZ", "Brent crude", "XBR/USD"),
                          ("NG", "Natural gas", "NG/USD"))),
    ("materials", "Metals", (("XAU", "Gold", "XAU/USD"), ("XAG", "Silver", "XAG/USD"), ("HG", "Copper", "XCU/USD"))),
    ("indices", "Indices", (("SPX", "S&P 500", "SPX"),)),
)


def parse_coingecko(raw: Any) -> list[dict]:
    out = []
    for coin, symbol, label in CRYPTO:
        row = raw.get(coin) if isinstance(raw, dict) else None
        price = number(row.get("usd"), 0) if isinstance(row, dict) else None
        if price is None:
            continue
        change = number(row.get("usd_24h_change"), -100, 10_000)
        out.append({"symbol": symbol, "label": label, "price": price,
                    "change_pct": round(change, 2) if change is not None else None, "currency": "USD"})
    return out


def parse_twelvedata(raw: Any) -> tuple[list[dict], list[str]]:
    """A batched Twelve Data ``/quote`` answer → groups, plus a note per symbol it refused."""
    if isinstance(raw, dict) and raw.get("status") == "error":
        raise UpstreamError(502, f"Twelve Data refused the request: {text(raw.get('message'), 160) or 'no reason given'}")
    groups, notes = [], []
    for group_id, label, symbols in EQUITY_GROUPS:
        rows = []
        for symbol, name, td_symbol in symbols:
            quote = raw.get(td_symbol) if isinstance(raw, dict) else None
            if not isinstance(quote, dict) or quote.get("status") == "error":
                reason = text(quote.get("message"), 120) if isinstance(quote, dict) else "no quote"
                notes.append(f"{symbol}: {reason}")
                continue
            price = number(quote.get("close"), 0)
            if price is None:
                notes.append(f"{symbol}: no price in the quote")
                continue
            change = number(quote.get("percent_change"), -100, 10_000)
            rows.append({"symbol": symbol, "label": name, "price": price,
                         "change_pct": round(change, 2) if change is not None else None,
                         "currency": text(quote.get("currency"), 8) or "USD"})
        if rows:
            groups.append({"id": group_id, "label": label, "items": rows})
    return groups, notes


def twelvedata_key() -> str | None:
    """The optional Twelve Data key, read only through Hermes's secret scope (so each profile keeps
    its own key). Outside Hermes, or with no scope in this process, it behaves as if unset."""
    try:
        from agent.secret_scope import get_secret
        value = get_secret(TWELVEDATA_ENV)
    except Exception:
        value = None
    return value.strip() if isinstance(value, str) and value.strip() else None


def markets() -> dict:
    groups: list[dict] = []
    notes: list[str] = []
    params = {"ids": ",".join(c[0] for c in CRYPTO), "vs_currencies": "usd", "include_24hr_change": "true"}
    crypto = _cache.fetch("markets:crypto", 300,
                          lambda: parse_coingecko(http.get_json(COINGECKO, source="CoinGecko", params=params)),
                          source="CoinGecko")
    if crypto["value"]:
        groups.append({"id": "crypto", "label": "Digital assets", "items": crypto["value"]})
    if crypto["error"]:
        notes.append(crypto["error"] + (" Showing the last prices." if crypto["value"] else ""))
    updated = [crypto["updated"]] if crypto["updated"] else []
    key = twelvedata_key()
    if key is None:
        notes.append(f"Equities, energy and metals need a free Twelve Data key: set {TWELVEDATA_ENV} "
                     "in Hermes (Settings → Plugins → OSINT Map) and reopen the page.")
    else:
        symbols = ",".join(td for _, _, rows in EQUITY_GROUPS for _, _, td in rows)
        # The key travels in a header, never in the URL, so no error message or log line can carry it.
        load = lambda: parse_twelvedata(http.get_json(TWELVEDATA, source="Twelve Data", params={"symbol": symbols},
                                                      headers={"Authorization": f"apikey {key}"}))
        quotes = _cache.fetch("markets:twelvedata", 3600, load, source="Twelve Data", retry_after=300)
        if quotes["value"]:
            groups.extend(quotes["value"][0])
            notes.extend(quotes["value"][1])
            updated.append(quotes["updated"])
        if quotes["error"]:
            notes.append(quotes["error"])
    return {"updated": min(updated) if updated else None, "groups": groups, "notes": notes}


# --- Country facts ---------------------------------------------------------------------------------

WORLD_BANK = "https://api.worldbank.org/v2/country/{code}"
INDICATORS = (("population", "SP.POP.TOTL"), ("gdp_usd", "NY.GDP.MKTP.CD"), ("gdp_per_capita_usd", "NY.GDP.PCAP.CD"))
_country_cache = TTLCache(24 * 3600, size=300)
_CODE = re.compile(r"^[A-Za-z]{2}$")


def _wb_rows(raw: Any, code: str) -> list:
    if isinstance(raw, list) and raw and isinstance(raw[0], dict) and "message" in raw[0]:
        raise UpstreamError(404, f"The World Bank has no country with code {code}.")
    if not isinstance(raw, list) or len(raw) < 2 or not isinstance(raw[1], list):
        raise UpstreamError(404, f"The World Bank has no data for {code}.")
    return raw[1]


def parse_country(info: Any, indicators: Any, code: str) -> dict:
    rows = _wb_rows(info, code)
    if not rows or not isinstance(rows[0], dict):
        raise UpstreamError(404, f"The World Bank has no country with code {code}.")
    c = rows[0]

    def value(key: str) -> str | None:
        node = c.get(key)
        return text(node.get("value") if isinstance(node, dict) else node, 80)

    facts: dict[str, Any] = {"code": code, "name": text(c.get("name"), 80), "capital": text(c.get("capitalCity"), 80),
                             "region": value("region"), "income": value("incomeLevel"),
                             "lat": number(c.get("latitude"), -90, 90), "lon": number(c.get("longitude"), -180, 180)}
    years: dict[str, int | None] = {}
    by_id = {}
    for row in _wb_rows(indicators, code) if indicators is not None else []:
        if isinstance(row, dict) and isinstance(row.get("indicator"), dict):
            by_id[row["indicator"].get("id")] = row
    for name, indicator in INDICATORS:
        row = by_id.get(indicator) or {}
        amount = number(row.get("value"), 0)
        facts[name] = round(amount, 2) if amount is not None else None
        year = number(row.get("date"), 1900, 2200)
        years[name] = int(year) if year is not None and amount is not None else None
    known = [y for y in years.values() if y]
    facts["year"] = max(known) if known else None
    facts["years"] = years
    return facts


def country(code: str) -> dict:
    if not isinstance(code, str) or not _CODE.match(code.strip()):
        raise UpstreamError(400, "Country codes are two letters (ISO 3166 alpha-2), like JP or BR.")
    code = code.strip().upper()

    def load() -> dict:
        base = WORLD_BANK.format(code=code)
        info = http.get_json(base, source="the World Bank", params={"format": "json"})
        _wb_rows(info, code)  # fail fast on unknown codes before the second request
        ids = ";".join(i for _, i in INDICATORS)
        indicators = http.get_json(f"{base}/indicator/{ids}", source="the World Bank",
                                   params={"format": "json", "mrnev": 1, "source": 2})
        return parse_country(info, indicators, code)

    return _country_cache.get_or_load(code, load)
