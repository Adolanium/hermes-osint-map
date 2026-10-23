"""News incidents from GDELT 2.0 event exports.

GDELT publishes a zipped tab-separated export of coded news events every 15 minutes and names the
newest one in ``lastupdate.txt``. This module keeps a rolling window of the last ``WINDOW`` exports
(3 hours), parses only conflict-type events, and folds rows that share a place and a CAMEO root
code into one item with a ``count``, so a city with forty "fight" reports is one marker, not forty.

Each export is downloaded once: a refresh only fetches stamps that entered the window, and stamps
that left it are dropped, so memory stays bounded by the window. GDELT's doc/search API is rate
limited and not used.
"""

from __future__ import annotations

import io
import math
import re
import threading
import zipfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import geo
from .http import UpstreamError
from .parsers import clamp01, item, number, text

LASTUPDATE_URL = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
EXPORT_URL = "https://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip"
WINDOW = 12  # 15-minute exports → 3 hours
MAX_ITEMS = 5000
MAX_KEYS_PER_FILE = 5000
MAX_UNZIPPED = 64 * 1024 * 1024
COLUMNS = 61

# CAMEO root code → (kind, label). 13 and 14 are verbal conflict (QuadClass 3), 15-20 material
# conflict (QuadClass 4); 16 ("reduce relations") is diplomatic and left out.
ROOTS = {
    "13": ("threaten", "Threaten"),
    "14": ("protest", "Protest"),
    "15": ("force", "Force posture"),
    "17": ("coerce", "Coerce"),
    "18": ("assault", "Assault"),
    "19": ("fight", "Fight"),
    "20": ("mass-violence", "Mass violence"),
}
_STAMP = re.compile(r"(\d{14})\.export\.CSV\.zip\s*$")

# Column positions in the GDELT 2.0 event table (61 tab-separated fields).
C_ACTOR1, C_ACTOR2 = 6, 16
C_ROOT, C_QUAD, C_GOLDSTEIN, C_MENTIONS, C_SOURCES, C_ARTICLES, C_TONE = 28, 29, 30, 31, 32, 33, 34
C_GEO_TYPE, C_GEO_NAME, C_GEO_COUNTRY, C_LAT, C_LON = 51, 52, 53, 56, 57
C_DATEADDED, C_URL = 59, 60


def parse_lastupdate(body: str) -> str | None:
    """The 14-digit stamp of the newest export named in ``lastupdate.txt``."""
    for line in body.splitlines():
        match = _STAMP.search(line.strip())
        if match:
            return match.group(1)
    return None


def window_stamps(latest: str, size: int = WINDOW) -> list[str]:
    """``latest`` and the ``size - 1`` export stamps before it, newest first."""
    start = datetime.strptime(latest, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return [(start - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S") for i in range(size)]


def stamp_ms(stamp: str) -> int | None:
    try:
        return int(datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def unzip_export(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise UpstreamError(502, "A GDELT export had no CSV inside.")
            info = archive.getinfo(names[0])
            if info.file_size > MAX_UNZIPPED:
                raise UpstreamError(502, "A GDELT export was larger than this plugin accepts.")
            return archive.read(info).decode("utf-8", errors="replace")
    except zipfile.BadZipFile as exc:
        raise UpstreamError(502, "A GDELT export was not a valid zip file.") from exc


def parse_export(body: str) -> dict[str, dict]:
    """One export's rows → per (place, root) accumulators. Rows of other roots or quad classes,
    and rows without an action location, are skipped."""
    groups: dict[str, dict] = {}
    for line in body.splitlines():
        fields = line.split("\t")
        if len(fields) < COLUMNS:
            continue
        root = fields[C_ROOT].strip().zfill(2)
        if root not in ROOTS or fields[C_QUAD].strip() not in ("3", "4"):
            continue
        lat, lon = number(fields[C_LAT], -90, 90), number(fields[C_LON], -180, 180)
        if lat is None or lon is None or (lat == 0 and lon == 0):
            continue
        key = f"{root}:{lat:.4f},{lon:.4f}"
        mentions = int(number(fields[C_MENTIONS], 0, 1e7) or 0)
        group = groups.get(key)
        if group is None:
            if len(groups) >= MAX_KEYS_PER_FILE:
                continue
            group = groups[key] = {
                "root": root, "lat": lat, "lon": lon, "place": text(fields[C_GEO_NAME], 120),
                "fips": fields[C_GEO_COUNTRY].strip(), "geo_type": fields[C_GEO_TYPE].strip(),
                "events": 0, "mentions": 0, "sources": 0, "articles": 0, "goldstein_sum": 0.0,
                "goldstein_min": 10.0, "tone_sum": 0.0, "added": None, "url": None, "url_mentions": -1,
                "actors": [],
            }
        goldstein = number(fields[C_GOLDSTEIN], -10, 10) or 0.0
        group["events"] += 1
        group["mentions"] += mentions
        group["sources"] += int(number(fields[C_SOURCES], 0, 1e7) or 0)
        group["articles"] += int(number(fields[C_ARTICLES], 0, 1e7) or 0)
        group["goldstein_sum"] += goldstein
        group["goldstein_min"] = min(group["goldstein_min"], goldstein)
        group["tone_sum"] += number(fields[C_TONE], -100, 100) or 0.0
        added = stamp_ms(fields[C_DATEADDED].strip())
        if added and (group["added"] is None or added > group["added"]):
            group["added"] = added
        url = fields[C_URL].strip()
        if url.startswith(("http://", "https://")) and mentions > group["url_mentions"]:
            group["url"], group["url_mentions"] = url[:500], mentions
        for column in (C_ACTOR1, C_ACTOR2):
            actor = text(fields[column], 40)
            if actor and actor.title() not in group["actors"] and len(group["actors"]) < 4:
                group["actors"].append(actor.title())
    return groups


def merge(files: list[dict[str, dict]]) -> dict[str, dict]:
    """Sum per-file accumulators into one per (place, root) over the whole window."""
    total: dict[str, dict] = {}
    for groups in files:
        for key, group in groups.items():
            have = total.get(key)
            if have is None:
                total[key] = {**group, "actors": list(group["actors"])}
                continue
            for field in ("events", "mentions", "sources", "articles", "goldstein_sum", "tone_sum"):
                have[field] += group[field]
            have["goldstein_min"] = min(have["goldstein_min"], group["goldstein_min"])
            if group["added"] and (have["added"] is None or group["added"] > have["added"]):
                have["added"] = group["added"]
            if group["url"] and group["url_mentions"] > have["url_mentions"]:
                have["url"], have["url_mentions"] = group["url"], group["url_mentions"]
            for actor in group["actors"]:
                if actor not in have["actors"] and len(have["actors"]) < 4:
                    have["actors"].append(actor)
    return total


def short_place(full: str | None) -> str:
    """"Kharkiv, Kharkivs'ka Oblast', Ukraine" → "Kharkiv, Ukraine"."""
    if not full:
        return "Unknown place"
    parts = [p.strip() for p in full.split(",") if p.strip()]
    return f"{parts[0]}, {parts[-1]}" if len(parts) >= 3 else ", ".join(parts)


def incident_severity(mentions: int, goldstein_min: float) -> float:
    """More coverage and a more negative Goldstein score (−10 is the most destabilising) → brighter.
    Coverage saturates at about 100 mentions."""
    coverage = min(1.0, math.log10(1 + max(0, mentions)) / 2.0)
    impact = max(0.0, min(1.0, -goldstein_min / 10))
    return clamp01(0.1 + 0.9 * (0.55 * coverage + 0.45 * impact))


def to_items(groups: dict[str, dict], limit: int = MAX_ITEMS) -> list[dict]:
    out = []
    for key, g in groups.items():
        kind, label = ROOTS[g["root"]]
        n = g["events"] or 1
        country = geo.from_fips(g["fips"]) or geo.from_place(g["place"])
        out.append(item(
            "incidents", key, kind, g["lat"], g["lon"], f"{label} · {short_place(g['place'])}",
            subtitle=f"{g['events']} report{'s' if g['events'] != 1 else ''} · {g['mentions']} mentions",
            severity=incident_severity(g["mentions"], g["goldstein_min"]),
            time=g["added"],
            country=country,
            url=g["url"],
            rows=[("Type", label), ("Reports", g["events"]), ("Mentions", g["mentions"]), ("Sources", g["sources"]),
                  ("Goldstein (avg)", f"{g['goldstein_sum'] / n:.1f}"), ("Tone (avg)", f"{g['tone_sum'] / n:.1f}"),
                  ("Actors", ", ".join(g["actors"]) or None),
                  ("Place precision", "Country only" if g["geo_type"] == "1" else None)],
            count=g["events"],
        ))
    out.sort(key=lambda i: (i["severity"], i["time"] or 0), reverse=True)
    return out[:limit]


Fetch = Callable[[str], bytes]


class Window:
    """The rolling set of parsed exports. ``refresh`` is called through the layer's snapshot cache
    (at most every 15 minutes) and returns the merged items."""

    def __init__(self, fetch_text: Callable[[str], str], fetch_bytes: Fetch, size: int = WINDOW,
                 workers: int = 4) -> None:
        self._fetch_text, self._fetch_bytes = fetch_text, fetch_bytes
        self.size, self._workers = size, workers
        self._files: OrderedDict[str, dict[str, dict]] = OrderedDict()
        self._lock = threading.Lock()
        self.latest: str | None = None

    def _load(self, stamp: str) -> dict[str, dict] | None:
        try:
            return parse_export(unzip_export(self._fetch_bytes(EXPORT_URL.format(stamp=stamp))))
        except UpstreamError as exc:
            if exc.status == 404:
                return {}  # GDELT skips a slot now and then; remember it as empty
            return None  # try again on the next refresh

    def refresh(self) -> list[dict]:
        latest = parse_lastupdate(self._fetch_text(LASTUPDATE_URL))
        if latest is None:
            raise UpstreamError(502, "GDELT's lastupdate.txt did not name an export file.")
        wanted = window_stamps(latest, self.size)
        with self._lock:
            for stamp in [s for s in self._files if s not in wanted]:
                del self._files[stamp]
            missing = [s for s in wanted if s not in self._files]
        if missing:
            with ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="osint-gdelt") as pool:
                loaded = list(pool.map(self._load, missing))
            with self._lock:
                for stamp, groups in zip(missing, loaded):
                    if groups is not None:
                        self._files[stamp] = groups
        with self._lock:
            self.latest = latest
            if not any(self._files.values()) and latest not in self._files:
                raise UpstreamError(503, "Could not download any GDELT export in the last 3 hours.")
            files = [self._files[s] for s in wanted if s in self._files]
        return to_items(merge(files))

    def loaded(self) -> int:
        with self._lock:
            return len(self._files)
