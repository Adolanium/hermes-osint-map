"""Live smoke test: call every dashboard route once against the real internet.

    .venv/Scripts/python.exe scripts/smoke.py

Prints one line per route: ok/FAIL, layer status, item counts and timing. Not part of the unit
tests (those never touch the network). Run it sparingly: it is a real request to every upstream.
"""

import importlib.util
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("osint_smoke_api", ROOT / "dashboard" / "plugin_api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


def describe(answer: dict) -> str:
    if not answer.get("ok"):
        return f"error {answer.get('status')}: {answer.get('error')}"
    bits = []
    if "status" in answer:
        bits.append(f"status={answer['status']}")
    for key in ("items", "layers", "groups", "kp", "alerts", "summary"):
        if isinstance(answer.get(key), list):
            bits.append(f"{key}={len(answer[key])}")
    if answer.get("kp_now") is not None:
        bits.append(f"kp_now={answer['kp_now']}")
    if answer.get("dropped"):
        bits.append(f"dropped={answer['dropped']}")
    if answer.get("name"):
        bits.append(f"name={answer['name']} pop={answer.get('population')} year={answer.get('year')}")
    if answer.get("error"):
        bits.append(f"error={answer['error']}")
    if answer.get("notes"):
        bits.append(f"notes={len(answer['notes'])}")
    return " ".join(bits)


CALLS = [
    ("layer quakes", lambda: api.layer("quakes")),
    ("layer natural", lambda: api.layer("natural")),
    ("layer disasters", lambda: api.layer("disasters")),
    ("layer weather", lambda: api.layer("weather")),
    ("layer aircraft (mil)", lambda: api.layer("aircraft")),
    ("layer aircraft (point)", lambda: api.layer("aircraft", lat="51.47", lon="-0.45", radius="60")),
    ("layer satellites", lambda: api.layer("satellites")),
    ("layer incidents", lambda: api.layer("incidents")),
    ("layer chokepoints", lambda: api.layer("chokepoints")),
    ("layers", api.layer_list),
    ("news", lambda: api.news()),
    ("news q=ukraine", lambda: api.news(q="ukraine", limit="5")),
    ("space-weather", api.space_weather),
    ("markets", api.markets),
    ("country JP", lambda: api.country("JP")),
    ("recon dns", lambda: api.recon_lookup("dns", "example.com")),
    ("recon rdap domain", lambda: api.recon_lookup("rdap", "github.com")),
    ("recon rdap ip", lambda: api.recon_lookup("rdap", "1.1.1.1")),
    ("recon tls", lambda: api.recon_lookup("tls", "github.com")),
    ("recon headers", lambda: api.recon_lookup("headers", "http://github.com")),
    ("recon subdomains", lambda: api.recon_lookup("subdomains", "hermes-agent.org")),
    ("recon cve", lambda: api.recon_lookup("cve", "CVE-2021-44228")),
    ("recon ip", lambda: api.recon_lookup("ip", "8.8.8.8")),
    ("recon github", lambda: api.recon_lookup("github", "torvalds")),
    ("recon mac", lambda: api.recon_lookup("mac", "00:1A:2B:3C:4D:5E")),
    ("guard headers localhost", lambda: api.recon_lookup("headers", "http://127.0.0.1/")),
]

failures = 0
for name, call in CALLS:
    start = time.monotonic()
    answer = call()
    took = time.monotonic() - start
    good = answer.get("ok") and answer.get("status") not in ("error",)
    if name.startswith("guard"):
        good = not answer.get("ok")
    failures += 0 if good else 1
    print(f"{'ok  ' if good else 'FAIL'} {name:<26} {took:6.2f}s  {describe(answer)}"[:300])
sys.exit(1 if failures else 0)
