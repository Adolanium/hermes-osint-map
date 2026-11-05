"""Agent tools. Each handler returns a JSON string with a plain-text ``text`` the model can quote
(source names included) and never raises."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from . import area as areas
from . import geo, layers, panels, recon, watch
from .http import UpstreamError, describe
from .parsers import number

TOOLSET = "osint_map"
EVENT_LAYERS = areas.EVENT_LAYERS
EMOJI = {"osint_situation": "🌍", "osint_events": "📍", "osint_country": "🏳️", "osint_recon": "🔎", "osint_news": "📰",
         "osint_watch": "🛰️"}

# Plugin-owned Hermes state (ctx.state), bound at register(). Only osint_watch needs it.
_state = None


def bind_state(state) -> None:
    global _state
    _state = state


def _schema(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required or []}}


SCHEMAS = {
    "osint_situation": _schema(
        "osint_situation",
        "A current world situation brief from public sources: item counts and the most severe items per map layer "
        "(earthquakes, natural events, disaster alerts, US severe weather, military aircraft, news incidents), "
        "the planetary Kp index and the top headlines. News incidents are machine-coded from news coverage (GDELT): "
        "they show where reporting is, not verified events. Takes no arguments; the first call can take ~20 s.",
        {},
    ),
    "osint_events": _schema(
        "osint_events",
        "List current map items, most severe first, filtered by layer, country and/or a point and radius. "
        "Items in the incidents layer are machine-coded from news coverage (GDELT), not verified events.",
        {
            "layer": {"type": "string", "enum": list(layers.LAYERS),
                      "description": "One layer; omit for every layer except satellites."},
            "country": {"type": "string", "description": "ISO 3166 alpha-2 code, like UA or JP."},
            "lat": {"type": "number", "description": "Latitude of the centre point (needs lon)."},
            "lon": {"type": "number", "description": "Longitude of the centre point (needs lat)."},
            "radius_km": {"type": "number", "description": "Radius around lat/lon in km (default 500, max 5000)."},
            "limit": {"type": "integer", "description": "Maximum items (default 20, max 100)."},
        },
    ),
    "osint_country": _schema(
        "osint_country",
        "Country brief: World Bank facts (capital, region, income, population, GDP), current map events located "
        "in the country, and matching headlines.",
        {"code": {"type": "string", "description": "ISO 3166 alpha-2 code, like BR."}},
        ["code"],
    ),
    "osint_recon": _schema(
        "osint_recon",
        "Passive recon lookup on a public target: dns (records), rdap (registration), tls (certificate), headers "
        "(HTTP security headers), subdomains (certificate transparency), cve (CVSS + CISA KEV), ip (geo/ASN), "
        "github (user/org profile), mac (vendor). Private and local addresses are refused.",
        {"kind": {"type": "string", "enum": list(recon.KINDS)},
         "target": {"type": "string", "description": "Domain, URL, IP, CVE id, GitHub name or MAC, matching kind."}},
        ["kind", "target"],
    ),
    "osint_news": _schema(
        "osint_news",
        "Search recent world headlines: BBC World, Al Jazeera, DW, The Guardian and France 24, plus the "
        "GDELT 2.0 article-search API when a query is given (wider local coverage). GDELT results are recent "
        "article listings, not editorially curated.",
        {"query": {"type": "string", "description": "Words to find in titles and summaries; empty for the latest."},
         "limit": {"type": "integer", "description": "Maximum headlines (default 10, max 50)."}},
    ),
    "osint_watch": _schema(
        "osint_watch",
        "Check a watched area for anything new since the last check: earthquakes at or above min_magnitude, "
        "disaster alerts at or above min_alert_level, natural events, US severe weather and military aircraft by "
        "default. The first call for a watch_id records a baseline and reports nothing. The result starts with "
        f"{watch.NO_NEW} when there is nothing new. Built for scheduled jobs: to set up a recurring watch, create a "
        "cron job whose prompt calls this tool with the same arguments and replies [SILENT] when the result starts "
        f"with {watch.NO_NEW}. Name that job 'OSINT watch · <label>' and start its prompt with "
        "'OSINT Map watch (watch_id=\"<id>\").' so the OSINT Map page lists it.",
        {
            "watch_id": {"type": "string", "description": "Stable id for this watch: 3-41 lower-case letters, digits or dashes."},
            "label": {"type": "string", "description": "Human name for the area, like Taiwan Strait."},
            "country": {"type": "string", "description": "ISO 3166 alpha-2 code, like TW (or use lat/lon)."},
            "lat": {"type": "number", "description": "Latitude of the centre point (needs lon)."},
            "lon": {"type": "number", "description": "Longitude of the centre point (needs lat)."},
            "radius_km": {"type": "number", "description": "Radius around lat/lon in km (default 500, max 5000)."},
            "layers": {"type": "array", "items": {"type": "string", "enum": list(layers.LAYERS)},
                       "description": "Layers to watch; default quakes, disasters, natural, weather, aircraft."},
            "min_magnitude": {"type": "number", "description": "Smallest earthquake magnitude to report (default 4.5)."},
            "min_alert_level": {"type": "string", "enum": ["green", "orange", "red"],
                                "description": "Lowest GDACS disaster alert level to report (default orange)."},
        },
        ["watch_id"],
    ),
}


# --- Formatting ------------------------------------------------------------------------------------


def _when(ms: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ms / 1000)) if ms else "time unknown"


def _item_line(entry: dict) -> str:
    bits = [entry["title"]]
    if entry.get("subtitle"):
        bits.append(f"({entry['subtitle']})")
    if entry.get("country"):
        bits.append(f"[{entry['country']}]")
    if entry.get("time"):
        bits.append(f"at {_when(entry['time'])}")
    if entry.get("lat") is not None:
        note = " (country-level accuracy)" if any(
            len(p) > 1 and p[0] == "Place precision" for p in entry.get("props", [])) else ""
        bits.append(f"{entry['lat']:.2f},{entry['lon']:.2f}{note}")
    if entry.get("url"):
        bits.append(entry["url"])
    return "- " + " ".join(bits)


def _ok(text: str, **data: Any) -> str:
    return json.dumps({"ok": True, "text": text, **data}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _guarded(handler: Callable[[dict], str]) -> Callable[..., str]:
    def run(args: dict | None = None, **_: Any) -> str:
        try:
            return handler(args or {})
        except UpstreamError as exc:
            return _err(str(exc))
        except Exception as exc:  # a tool must answer, whatever went wrong
            return _err(describe(exc, "OSINT Map"))
    run.__name__ = handler.__name__
    run.__doc__ = handler.__doc__
    return run


def _layer_status(layer_id: str, answer: dict) -> str:
    layer = layers.LAYERS[layer_id]
    state = "" if answer["status"] == "ok" else f" ({answer['status']}: {answer['error']})"
    return f"{layer.label} ({layer.source}): {answer['count']} items{state}"


# --- Handlers --------------------------------------------------------------------------------------


def situation(args: dict) -> str:
    answers = layers.fetch_many()
    lines = [f"OSINT situation at {_when(int(time.time() * 1000))}", ""]
    for layer_id, answer in answers.items():
        lines.append(_layer_status(layer_id, answer))
        if layer_id in ("satellites", "chokepoints", "cameras"):
            continue
        for entry in sorted(answer["items"], key=lambda i: i["severity"], reverse=True)[:3]:
            lines.append("  " + _item_line(entry))
    try:
        sw = panels.space_weather()
        scales = sw["scales"]
        lines += ["", f"Space weather (NOAA SWPC): Kp now {sw['kp_now']}, scales R{scales['R']} S{scales['S']} G{scales['G']}."]
    except UpstreamError as exc:
        lines += ["", f"Space weather unavailable: {exc}"]
    try:
        heads = panels.news(limit=5)["items"]
        lines += ["", "Top headlines:"] + [f"- {h['title']} ({h['source']}, {_when(h['time'])}) {h['url']}" for h in heads]
    except UpstreamError as exc:
        lines += ["", f"Headlines unavailable: {exc}"]
    sources = sorted({layers.LAYERS[i].source for i in answers} | {"NOAA SWPC", "BBC/Al Jazeera/DW/Guardian/France 24 RSS"})
    return _ok("\n".join(lines), sources=sources)


def events(args: dict) -> str:
    area = areas.parse(args)
    limit = int(min(100, number(args.get("limit"), 1, 1000) or 20))
    found, answers = areas.collect(area)
    where = area.describe() if (area.country or area.has_point) else ""
    head = f"{len(found)} items {where}".strip() + (f", showing {limit}" if len(found) > limit else "") + ":"
    lines = [head] + [_item_line(e) for e in found[:limit]]
    lines += ["", "Layer status: " + "; ".join(_layer_status(i, a) for i, a in answers.items())]
    if not found:
        lines.insert(1, "- nothing matches right now")
    hint = areas.card_hint(area)
    if hint:
        lines += ["", hint]
    return _ok("\n".join(lines), count=len(found), sources=sorted({layers.LAYERS[i].source for i in answers}))


def country(args: dict) -> str:
    code = geo.normalise_iso2(str(args.get("code") or ""))
    if code is None:
        return _err("code must be an ISO 3166 alpha-2 code, like BR.")
    lines, sources = [], []
    name = geo.name(code) or code
    try:
        facts = panels.country(code)
        name = facts["name"] or name
        sources.append("World Bank")
        lines.append(f"{name} ({code}) — World Bank")
        for label, key, fmt in (("Capital", "capital", "{}"), ("Region", "region", "{}"), ("Income group", "income", "{}"),
                                ("Population", "population", "{:,.0f}"), ("GDP (current US$)", "gdp_usd", "${:,.0f}"),
                                ("GDP per capita", "gdp_per_capita_usd", "${:,.0f}")):
            if facts.get(key) is not None:
                year = (facts.get("years") or {}).get(key)
                lines.append(f"- {label}: {fmt.format(facts[key])}" + (f" ({year})" if year else ""))
    except UpstreamError as exc:
        lines.append(f"{name} ({code}): World Bank facts unavailable ({exc})")
    answers = layers.fetch_many(EVENT_LAYERS)
    matched = sorted((e for a in answers.values() for e in a["items"] if e["country"] == code),
                     key=lambda i: i["severity"], reverse=True)
    lines += ["", f"Current map events located in {name}: {len(matched)}"] + [_item_line(e) for e in matched[:15]]
    sources += sorted({layers.LAYERS[i].source for i in answers})
    try:
        heads = panels.news(q=name, limit=8)["items"]
        lines += ["", f"Headlines mentioning {name}: {len(heads)}"]
        lines += [f"- {h['title']} ({h['source']}, {_when(h['time'])}) {h['url']}" for h in heads]
        sources.append("RSS/GDELT headlines")
    except UpstreamError as exc:
        lines += ["", f"Headlines unavailable: {exc}"]
    lines += ["", areas.card_hint(areas.Area(country=code), name)]
    return _ok("\n".join(lines), sources=sources)


def recon_tool(args: dict) -> str:
    answer = recon.run(str(args.get("kind") or ""), str(args.get("target") or ""))
    lines = [f"{answer['kind']} lookup for {answer['target']}:"] + [f"- {k}: {v}" for k, v in answer["summary"]]
    if answer["kind"] == "subdomains":
        lines += ["- Names: " + ", ".join(n["name"] for n in answer["result"]["names"][:40])]
    if answer["kind"] == "cve" and answer["result"].get("description"):
        lines += ["- Description: " + answer["result"]["description"]]
    lines += ["", "Sources: " + (", ".join(answer["sources"]) or "local check only")]
    return _ok("\n".join(lines), sources=answer["sources"])


def news_tool(args: dict) -> str:
    query = str(args.get("query") or "").strip()
    limit = int(min(50, number(args.get("limit"), 1, 1000) or 10))
    answer = panels.news(q=query, limit=limit)
    head = f"{len(answer['items'])} headlines" + (f" matching {query!r}" if query else "") + ":"
    lines = [head] + [f"- {h['title']} ({h['source']}, {_when(h['time'])}) {h['url']}"
                      + (f"\n  {h['summary']}" if h["summary"] else "") for h in answer["items"]]
    if answer.get("error"):
        lines += ["", f"Some feeds failed: {answer['error']}"]
    return _ok("\n".join(lines), sources=sorted({f["source"] for f in answer["feeds"] if f["count"]}))


def watch_tool(args: dict) -> str:
    if _state is None:
        return _err("Watches need Hermes plugin state, which this Hermes did not provide. Update Hermes Agent.")
    answer = watch.check(_state, str(args.get("watch_id") or "").strip(), args)
    return _ok(answer["text"], new=answer["new"], baseline=answer["baseline"], count=answer["count"],
               sources=answer["sources"])


HANDLERS: dict[str, Callable[..., str]] = {
    "osint_situation": _guarded(situation),
    "osint_events": _guarded(events),
    "osint_country": _guarded(country),
    "osint_recon": _guarded(recon_tool),
    "osint_news": _guarded(news_tool),
    "osint_watch": _guarded(watch_tool),
}
