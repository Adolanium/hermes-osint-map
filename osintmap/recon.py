"""Recon lookups: DNS, RDAP, TLS, HTTP headers, certificate-log subdomains, CVEs, IPs, GitHub, MACs.

Targets are validated before anything leaves the gateway, and each kind talks to one fixed
upstream, except ``tls`` and ``headers``: those connect to the host the user named, so they go
through the SSRF guard in ``http`` (public addresses only, a short list of ports, every redirect
hop checked again, and the socket opened to the address that was checked).

Every answer has the same shape: ``kind``, ``target``, ``summary`` (label/value rows for the page),
``result`` (trimmed source data) and ``sources``.
"""

from __future__ import annotations

import hashlib
import http.client
import re
import socket
import ssl
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from . import http as net
from .http import TTLCache, UpstreamError, parse_domain, parse_ip
from .parsers import iso_ms, number, text

KINDS = ("dns", "rdap", "tls", "headers", "subdomains", "cve", "ip", "github", "mac")
SOCKET_TIMEOUT = 8.0
TLS_PORTS = (443, 8443)
HEADER_PORTS = (443, 8443, 80, 8080)
MAX_REDIRECTS = 5
MAX_SANS = 50
MAX_SUBDOMAINS = 300

_caches = {
    "cve": TTLCache(3600, size=128), "github": TTLCache(3600, size=64), "mac": TTLCache(24 * 3600, size=256),
}
_default_cache = TTLCache(600, size=256)


def _bad(message: str) -> UpstreamError:
    return UpstreamError(400, message)


def _require_domain(target: str) -> str:
    domain = parse_domain(target)
    if domain is None:
        raise _bad("That is not a domain name. Use a name like example.com (no scheme, path or port).")
    return domain


def _public_ip(target: str) -> str:
    ip = parse_ip(target)
    if ip is None:
        raise _bad("That is not an IP address. Use IPv4 like 8.8.8.8 or IPv6 like 2001:4860:4860::8888.")
    reason = net.blocked_reason(ip)
    if reason:
        raise _bad(f"{ip} is {reason}; lookups only cover public addresses.")
    return str(ip)


# --- DNS -------------------------------------------------------------------------------------------

DNS_TYPES = {"A": 1, "AAAA": 28, "MX": 15, "NS": 2, "TXT": 16, "CNAME": 5, "CAA": 257}
DOH = "https://dns.google/resolve"


def parse_doh(raw: Any, rtype: str) -> tuple[int | None, list[str]]:
    """A dns.google JSON answer → (DNS status, answers of the asked type). CNAME hops that come
    along with an A query are left out of the A list."""
    if not isinstance(raw, dict):
        return None, []
    status = raw.get("Status") if isinstance(raw.get("Status"), int) else None
    out = []
    for answer in raw.get("Answer") or []:
        if isinstance(answer, dict) and answer.get("type") == DNS_TYPES[rtype]:
            value = text(answer.get("data"), 300)
            if value and value not in out:
                out.append(value.strip('"') if rtype == "TXT" else value)
    return status, out[:50]


def dns(target: str) -> dict:
    domain = _require_domain(target)

    def one(rtype: str) -> tuple[str, int | None, list[str]]:
        raw = net.get_json(DOH, source="Google Public DNS", params={"name": domain, "type": rtype},
                           headers={"Accept": "application/dns-json"}, timeout=10.0)
        return (rtype, *parse_doh(raw, rtype))

    with ThreadPoolExecutor(max_workers=len(DNS_TYPES), thread_name_prefix="osint-dns") as pool:
        answers = list(pool.map(one, DNS_TYPES))
    if all(status == 3 for _, status, _ in answers):
        raise UpstreamError(404, f"{domain} does not exist in DNS (NXDOMAIN).")
    records = {rtype: values for rtype, _, values in answers}
    summary = [[rtype, ", ".join(values[:5]) + (f" (+{len(values) - 5} more)" if len(values) > 5 else "")]
               for rtype, values in records.items() if values]
    return {"target": domain, "summary": summary or [["Records", "None of the common record types are set."]],
            "result": {"records": records}, "sources": ["Google Public DNS (dns.google)"]}


# --- RDAP ------------------------------------------------------------------------------------------

RDAP = "https://rdap.org/{kind}/{target}"


def _vcard_name(entity: dict) -> str | None:
    card = entity.get("vcardArray")
    if isinstance(card, list) and len(card) > 1 and isinstance(card[1], list):
        for field in card[1]:
            if isinstance(field, list) and len(field) >= 4 and field[0] in ("fn", "org") and isinstance(field[3], str):
                return text(field[3], 120)
    return None


def _entities(node: dict, depth: int = 0) -> list[tuple[list[str], str]]:
    out = []
    for entity in node.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        roles = [r for r in entity.get("roles") or [] if isinstance(r, str)]
        name = _vcard_name(entity) or text(entity.get("handle"), 80)
        if name:
            out.append((roles, name))
        if depth < 3:
            out.extend(_entities(entity, depth + 1))
    return out


def parse_rdap(raw: Any) -> dict:
    """An RDAP domain or IP network object → the facts people look for first."""
    if not isinstance(raw, dict):
        raise UpstreamError(502, "RDAP answered something that is not an RDAP record.")
    events = {}
    for event in raw.get("events") or []:
        if isinstance(event, dict) and isinstance(event.get("eventAction"), str):
            events[event["eventAction"]] = event.get("eventDate")
    entities = _entities(raw)

    def role(*names: str) -> str | None:
        return next((name for roles, name in entities if any(r in roles for r in names)), None)

    result: dict[str, Any] = {
        "handle": text(raw.get("handle"), 80),
        "status": [s for s in raw.get("status") or [] if isinstance(s, str)][:10],
        "registered": events.get("registration"),
        "expires": events.get("expiration"),
        "changed": events.get("last changed"),
        "registrar": role("registrar"),
        "registrant": role("registrant"),
    }
    if raw.get("objectClassName") == "ip network" or "startAddress" in raw:
        cidrs = []
        for block in raw.get("cidr0_cidrs") or []:
            if isinstance(block, dict) and block.get("length") is not None:
                prefix = block.get("v4prefix") or block.get("v6prefix")
                if prefix:
                    cidrs.append(f"{prefix}/{block['length']}")
        result.update(kind="ip", name=text(raw.get("name"), 80), start=raw.get("startAddress"), end=raw.get("endAddress"),
                      cidr=cidrs[:8], country=text(raw.get("country"), 4), type=text(raw.get("type"), 60),
                      org=role("registrant", "administrative", "abuse"))
    else:
        result.update(kind="domain", name=text(raw.get("ldhName"), 253),
                      nameservers=[text(ns.get("ldhName"), 253).lower() for ns in raw.get("nameservers") or []
                                   if isinstance(ns, dict) and text(ns.get("ldhName"), 253)][:13])
    return result


def _rdap_lookup(kind: str, target: str) -> dict:
    raw = net.get_json(RDAP.format(kind=kind, target=target), source="RDAP", redirects=5, guard_redirects=True,
                       headers={"Accept": "application/rdap+json, application/json"}, timeout=15.0)
    return parse_rdap(raw)


def _rdap_summary(r: dict) -> list[list[str]]:
    rows: list[tuple[str, Any]] = [("Name", r.get("name"))]
    if r.get("kind") == "ip":
        rows += [("Network", ", ".join(r.get("cidr") or []) or (f"{r.get('start')} – {r.get('end')}" if r.get("start") else None)),
                 ("Organisation", r.get("org")), ("Country", r.get("country")), ("Type", r.get("type"))]
    else:
        rows += [("Registrar", r.get("registrar")), ("Nameservers", ", ".join(r.get("nameservers") or []) or None)]
    rows += [("Registered", (r.get("registered") or "")[:10] or None), ("Expires", (r.get("expires") or "")[:10] or None),
             ("Status", ", ".join(r.get("status") or []) or None)]
    return [[label, str(value)] for label, value in rows if value]


def rdap(target: str) -> dict:
    ip = parse_ip(target)
    if ip is not None:
        address = _public_ip(target)
        result = _rdap_lookup("ip", address)
        return {"target": address, "summary": _rdap_summary(result), "result": result, "sources": ["RDAP via rdap.org"]}
    domain = _require_domain(target)
    result = _rdap_lookup("domain", domain)
    return {"target": domain, "summary": _rdap_summary(result), "result": result, "sources": ["RDAP via rdap.org"]}


# --- TLS -------------------------------------------------------------------------------------------


def split_host_port(target: str, allowed: tuple[int, ...], default: int) -> tuple[str, int]:
    raw = (target or "").strip()
    host, port = raw, default
    match = re.match(r"^(.+):(\d{1,5})$", raw)
    if match:
        host, port = match.group(1), int(match.group(2))
    domain = parse_domain(host)
    if domain is None:
        raise _bad("Give a domain name, optionally with a port, like example.com or example.com:8443.")
    if port not in allowed:
        raise _bad(f"Port {port} is not allowed here; use one of {', '.join(map(str, allowed))}.")
    return domain, port


def _name_map(pairs: Any) -> dict[str, str]:
    out = {}
    for rdn in pairs or ():
        for entry in rdn:
            if isinstance(entry, tuple) and len(entry) == 2:
                out[entry[0]] = entry[1]
    return out


def parse_peer_cert(cert: dict, now: float | None = None) -> dict:
    """``SSLSocket.getpeercert()`` → subject, issuer, SANs (capped) and validity."""
    subject, issuer = _name_map(cert.get("subject")), _name_map(cert.get("issuer"))
    sans = [value for kind, value in cert.get("subjectAltName") or () if kind in ("DNS", "IP Address")]
    out: dict[str, Any] = {
        "subject": subject.get("commonName") or subject.get("organizationName"),
        "issuer": ", ".join(x for x in (issuer.get("organizationName"), issuer.get("commonName")) if x) or None,
        "sans": sans[:MAX_SANS], "san_count": len(sans), "serial": cert.get("serialNumber"),
        "not_before": None, "not_after": None, "days_left": None,
    }
    for field, key in (("notBefore", "not_before"), ("notAfter", "not_after")):
        if isinstance(cert.get(field), str):
            try:
                out[key] = int(ssl.cert_time_to_seconds(cert[field]) * 1000)
            except ValueError:
                pass
    if out["not_after"]:
        out["days_left"] = int((out["not_after"] / 1000 - (now or time.time())) // 86400)
    return out


def _handshake(host: str, port: int, verify: bool) -> tuple[ssl.SSLSocket, str]:
    sock, address = net.open_guarded(host, port, SOCKET_TIMEOUT)
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        sock.settimeout(SOCKET_TIMEOUT)
        return context.wrap_socket(sock, server_hostname=host), address
    except BaseException:
        sock.close()
        raise


def tls(target: str) -> dict:
    host, port = split_host_port(target, TLS_PORTS, 443)
    verify_error = None
    try:
        try:
            conn, address = _handshake(host, port, verify=True)
        except ssl.SSLCertVerificationError as exc:
            verify_error = text(exc.verify_message or str(exc), 200)
            conn, address = _handshake(host, port, verify=False)
    except UpstreamError:
        raise
    except (socket.timeout, TimeoutError) as exc:
        raise UpstreamError(504, f"{host}:{port} did not finish a TLS handshake in time.") from exc
    except ssl.SSLError as exc:
        raise UpstreamError(502, f"{host}:{port} did not complete a TLS handshake ({text(exc.reason or str(exc), 120)}).") from exc
    except OSError as exc:
        raise UpstreamError(503, f"Could not talk TLS to {host}:{port}.") from exc
    with conn:
        der = conn.getpeercert(binary_form=True) or b""
        cert = conn.getpeercert() if verify_error is None else {}
        protocol, cipher = conn.version(), (conn.cipher() or (None,))[0]
    details = parse_peer_cert(cert) if cert else {}
    result = {"host": host, "port": port, "address": address, "trusted": verify_error is None,
              "verify_error": verify_error, "protocol": protocol, "cipher": cipher,
              "sha256": hashlib.sha256(der).hexdigest() if der else None, **details}
    summary_rows: list[tuple[str, Any]] = [
        ("Trusted", "Yes" if verify_error is None else f"No: {verify_error}"),
        ("Subject", details.get("subject")), ("Issuer", details.get("issuer")),
        ("Valid until", time.strftime("%Y-%m-%d", time.gmtime(details["not_after"] / 1000)) if details.get("not_after") else None),
        ("Days left", details.get("days_left")),
        ("Names", f"{details['san_count']} (" + ", ".join(details["sans"][:4]) + (", …)" if details["san_count"] > 4 else ")")
         if details.get("san_count") else None),
        ("Protocol", protocol), ("Cipher", cipher), ("Address", address),
    ]
    return {"target": f"{host}:{port}" if port != 443 else host,
            "summary": [[k, str(v)] for k, v in summary_rows if v is not None],
            "result": result, "sources": [f"Direct TLS handshake with {host}:{port}"]}


# --- HTTP headers ----------------------------------------------------------------------------------

SECURITY_HEADERS = (
    ("strict-transport-security", "HSTS"), ("content-security-policy", "CSP"), ("x-frame-options", "X-Frame-Options"),
    ("x-content-type-options", "X-Content-Type-Options"), ("referrer-policy", "Referrer-Policy"),
    ("permissions-policy", "Permissions-Policy"),
)


def parse_url_target(target: str) -> tuple[str, str, int, str]:
    """``URL or domain`` → (scheme, host, port, path+query). Credentials, odd schemes and ports
    outside ``HEADER_PORTS`` are refused."""
    raw = (target or "").strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.I):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise _bad("That URL could not be read.") from exc
    if parts.scheme.lower() not in ("http", "https"):
        raise _bad("Only http:// and https:// URLs can be checked.")
    if parts.username or parts.password:
        raise _bad("URLs with credentials in them are not checked.")
    host = parts.hostname or ""
    domain = parse_domain(host)
    if domain is None:
        ip = parse_ip(host)
        if ip is None:
            raise _bad("Give a URL like https://example.com/ or a domain like example.com.")
        domain = str(ip)
    scheme = parts.scheme.lower()
    port = port or (443 if scheme == "https" else 80)
    if port not in HEADER_PORTS:
        raise _bad(f"Port {port} is not allowed here; use one of {', '.join(map(str, HEADER_PORTS))}.")
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return scheme, domain, port, path


Opener = Callable[[str, int, float], tuple[socket.socket, str]]


def _request_head(scheme: str, host: str, port: int, path: str, opener: Opener) -> tuple[int, list[tuple[str, str]], str, str | None]:
    """One GET through a guarded socket; the body is never read. Returns status, headers, the
    connected address and a note when the certificate was not trusted."""
    sock, address = opener(host, port, SOCKET_TIMEOUT)
    note = None
    try:
        if scheme == "https":
            try:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
            except ssl.SSLCertVerificationError as exc:
                sock.close()
                note = f"certificate not trusted ({text(exc.verify_message or 'verification failed', 120)})"
                sock, address = opener(host, port, SOCKET_TIMEOUT)
                context = ssl.create_default_context()
                context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
                sock = context.wrap_socket(sock, server_hostname=host)
        conn = http.client.HTTPConnection(host, port, timeout=SOCKET_TIMEOUT)
        conn.sock = sock
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        default = 443 if scheme == "https" else 80
        shown = f"[{host}]" if ":" in host else host
        conn.putheader("Host", shown if port == default else f"{shown}:{port}")
        conn.putheader("User-Agent", net.USER_AGENT)
        conn.putheader("Accept", "text/html,application/xhtml+xml,*/*;q=0.5")
        conn.putheader("Connection", "close")
        conn.endheaders()
        response = conn.getresponse()
        status, headers = response.status, response.getheaders()
        response.close()
        conn.close()
        return status, headers, address, note
    finally:
        sock.close()


def analyse_headers(headers: list[tuple[str, str]], https: bool) -> dict:
    lower: dict[str, str] = {}
    for name, value in headers:
        lower.setdefault(name.lower(), value)
    present, missing = {}, []
    for key, label in SECURITY_HEADERS:
        if key in lower:
            present[label] = text(lower[key], 200) or ""
        elif key != "strict-transport-security" or https:
            missing.append(label)
    return {"server": text(lower.get("server"), 120), "powered_by": text(lower.get("x-powered-by"), 120),
            "present": present, "missing": missing,
            "headers": {name: text(value, 200) or "" for name, value in list(lower.items())[:60]}}


def headers(target: str, opener: Opener | None = None) -> dict:
    opener = opener or net.open_guarded
    scheme, host, port, path = parse_url_target(target)
    chain: list[dict] = []
    while True:
        default = 443 if scheme == "https" else 80
        shown = f"[{host}]" if ":" in host else host
        url = f"{scheme}://{shown}{'' if port == default else f':{port}'}{path}"
        try:
            status, response_headers, address, note = _request_head(scheme, host, port, path, opener)
        except UpstreamError:
            raise
        except (socket.timeout, TimeoutError) as exc:
            raise UpstreamError(504, f"{host}:{port} did not answer in time.") from exc
        except (ssl.SSLError, http.client.HTTPException, OSError) as exc:
            raise UpstreamError(502, f"{host}:{port} did not answer HTTP properly ({type(exc).__name__}).") from exc
        chain.append({"url": url[:300], "status": status, "address": address, "note": note})
        location = next((v for k, v in response_headers if k.lower() == "location"), None)
        if status in (301, 302, 303, 307, 308) and location:
            if len(chain) > MAX_REDIRECTS:
                raise UpstreamError(502, f"{target} redirected more than {MAX_REDIRECTS} times.")
            # Every hop is validated like the first one, so a redirect cannot reach a private host.
            scheme, host, port, path = parse_url_target(urljoin(url, location))
            continue
        break
    analysis = analyse_headers(response_headers, scheme == "https")
    summary = [["Status chain", " → ".join(str(hop["status"]) for hop in chain)], ["Final URL", chain[-1]["url"]]]
    if analysis["server"]:
        summary.append(["Server", analysis["server"]])
    summary.append(["Security headers", ", ".join(analysis["present"]) or "none"])
    if analysis["missing"]:
        summary.append(["Missing", ", ".join(analysis["missing"])])
    if any(hop["note"] for hop in chain):
        summary.append(["Warning", next(hop["note"] for hop in chain if hop["note"])])
    return {"target": chain[0]["url"], "summary": summary, "result": {"chain": chain, **analysis},
            "sources": [f"Direct HTTP request to {host}"]}


# --- Subdomains from certificate transparency ------------------------------------------------------

CRTSH = "https://crt.sh/"


def parse_crtsh(raw: Any, domain: str, limit: int = MAX_SUBDOMAINS) -> tuple[list[dict], int]:
    """crt.sh JSON → unique names under ``domain`` with first/last certificate dates (newest
    activity first), and the total number of unique names before the cap."""
    names: dict[str, dict] = {}
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        start = iso_ms(row.get("not_before"))
        for value in str(row.get("name_value") or "").lower().split("\n"):
            name = value.strip().removeprefix("*.")
            if not name or not (name == domain or name.endswith("." + domain)) or len(name) > 253:
                continue
            seen = names.setdefault(name, {"name": name, "first_seen": start, "last_seen": start, "certs": 0})
            seen["certs"] += 1
            if start and (seen["first_seen"] is None or start < seen["first_seen"]):
                seen["first_seen"] = start
            if start and (seen["last_seen"] is None or start > seen["last_seen"]):
                seen["last_seen"] = start
    ordered = sorted(names.values(), key=lambda n: (n["last_seen"] or 0, n["name"]), reverse=True)
    return ordered[:limit], len(ordered)


def subdomains(target: str) -> dict:
    domain = _require_domain(target)
    # crt.sh often answers 502 or stalls on wildcard queries; its plain identity search (which also
    # matches subdomains, less exhaustively) is the fallback before giving up.
    raw, error = None, None
    for query, timeout in ((f"%.{domain}", 12.0), (domain, 10.0)):
        try:
            raw = net.get_json(CRTSH, source="crt.sh", params={"q": query, "output": "json"},
                               timeout=timeout, max_bytes=16 * 1024 * 1024)
            break
        except UpstreamError as exc:
            if exc.status not in (502, 503, 504):
                raise
            error = exc
    if raw is None:
        raise error or UpstreamError(503, "crt.sh did not answer.")
    names, total = parse_crtsh(raw, domain)
    summary = [["Names found", f"{total}" + (f" (showing {len(names)})" if total > len(names) else "")]]
    if names:
        summary.append(["Most recent", ", ".join(n["name"] for n in names[:5])])
    return {"target": domain, "summary": summary, "result": {"names": names, "total": total},
            "sources": ["Certificate Transparency logs via crt.sh"]}


# --- CVE -------------------------------------------------------------------------------------------

CIRCL = "https://cve.circl.lu/api/cve/{id}"
KEV = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
_CVE = re.compile(r"^CVE-\d{4}-\d{4,7}$")
_kev_cache = TTLCache(6 * 3600, size=1)


def parse_kev(raw: Any) -> dict[str, dict]:
    out = {}
    for row in (raw.get("vulnerabilities") if isinstance(raw, dict) else None) or []:
        if isinstance(row, dict) and isinstance(row.get("cveID"), str):
            out[row["cveID"].upper()] = {
                "added": row.get("dateAdded"), "due": row.get("dueDate"),
                "ransomware": row.get("knownRansomwareCampaignUse"), "name": text(row.get("vulnerabilityName"), 160),
                "action": text(row.get("requiredAction"), 300),
            }
    return out


def _metrics(container: dict) -> list[dict]:
    out = []
    for metric in container.get("metrics") or []:
        if not isinstance(metric, dict):
            continue
        for key, version in (("cvssV4_0", "4.0"), ("cvssV3_1", "3.1"), ("cvssV3_0", "3.0"), ("cvssV2_0", "2.0")):
            data = metric.get(key)
            if isinstance(data, dict) and number(data.get("baseScore"), 0, 10) is not None:
                out.append({"version": version, "score": float(data["baseScore"]),
                            "severity": text(data.get("baseSeverity"), 12), "vector": text(data.get("vectorString"), 200)})
    return out


def parse_cve(raw: Any, cve_id: str) -> dict:
    """A CVE JSON 5 record → title, description, best CVSS (newest version, then highest score),
    CWEs and dates. CVSS from CISA's ADP container counts when the CNA gave none."""
    if not isinstance(raw, dict) or not isinstance(raw.get("containers"), dict):
        raise UpstreamError(404, f"No record was found for {cve_id}.")
    meta = raw.get("cveMetadata") if isinstance(raw.get("cveMetadata"), dict) else {}
    cna = raw["containers"].get("cna") if isinstance(raw["containers"].get("cna"), dict) else {}
    adp = [a for a in raw["containers"].get("adp") or [] if isinstance(a, dict)]
    description = next((d.get("value") for d in cna.get("descriptions") or []
                        if isinstance(d, dict) and str(d.get("lang", "")).startswith("en")), None)
    metrics = _metrics(cna) or [m for a in adp for m in _metrics(a)]
    metrics.sort(key=lambda m: (m["version"], m["score"]), reverse=True)
    cwes = []
    for problem in [*(cna.get("problemTypes") or []), *(p for a in adp for p in a.get("problemTypes") or [])]:
        for d in problem.get("descriptions") or [] if isinstance(problem, dict) else []:
            label = d.get("cweId") or d.get("description") if isinstance(d, dict) else None
            if isinstance(label, str) and label not in cwes:
                cwes.append(text(label, 80))
    return {
        "id": cve_id, "state": meta.get("state"), "title": text(cna.get("title"), 200) or text(description, 160),
        "description": text(description, 1200), "cvss": metrics[0] if metrics else None,
        "cwe": cwes[:5], "published": meta.get("datePublished"), "updated": meta.get("dateUpdated"),
        "assigner": meta.get("assignerShortName"),
    }


def cve(target: str) -> dict:
    cve_id = (target or "").strip().upper()
    if not _CVE.match(cve_id):
        raise _bad("CVE ids look like CVE-2024-3094.")
    record = parse_cve(net.get_json(CIRCL.format(id=cve_id), source="CIRCL Vulnerability-Lookup"), cve_id)
    sources = ["CIRCL Vulnerability-Lookup (cve.circl.lu)"]
    try:
        kev = _kev_cache.get_or_load("kev", lambda: parse_kev(net.get_json(KEV, source="CISA KEV", max_bytes=16 * 1024 * 1024,
                                                                          timeout=20.0)))
        record["kev"] = kev.get(cve_id)
        record["kev_checked"] = True
        sources.append("CISA Known Exploited Vulnerabilities catalog")
    except UpstreamError:
        record["kev"], record["kev_checked"] = None, False
    cvss = record["cvss"]
    rows: list[tuple[str, Any]] = [
        ("Title", record["title"]),
        ("CVSS", f"{cvss['score']:.1f} {cvss['severity'] or ''} (v{cvss['version']})".replace("  ", " ") if cvss else None),
        ("CWE", ", ".join(record["cwe"]) or None), ("Published", (record["published"] or "")[:10] or None),
        ("Known exploited (CISA KEV)", ("Yes, due " + str(record["kev"]["due"])) if record["kev"]
         else ("No" if record["kev_checked"] else "Could not check")),
        ("State", record["state"]),
    ]
    return {"target": cve_id, "summary": [[k, str(v)] for k, v in rows if v], "result": record, "sources": sources}


# --- IP --------------------------------------------------------------------------------------------

IPWHO = "https://ipwho.is/{ip}"


def parse_ipwho(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise UpstreamError(502, "ipwho.is answered something unexpected.")
    if raw.get("success") is False:
        raise UpstreamError(404, f"ipwho.is: {text(raw.get('message'), 120) or 'no data for that address'}.")
    conn = raw.get("connection") if isinstance(raw.get("connection"), dict) else {}
    zone = raw.get("timezone") if isinstance(raw.get("timezone"), dict) else {}
    return {"country": text(raw.get("country"), 80), "country_code": text(raw.get("country_code"), 4),
            "region": text(raw.get("region"), 80), "city": text(raw.get("city"), 80),
            "lat": number(raw.get("latitude"), -90, 90), "lon": number(raw.get("longitude"), -180, 180),
            "asn": conn.get("asn") if isinstance(conn.get("asn"), int) else None, "org": text(conn.get("org"), 120),
            "isp": text(conn.get("isp"), 120), "domain": text(conn.get("domain"), 120), "timezone": text(zone.get("id"), 60)}


def ip(target: str) -> dict:
    address = _public_ip(target)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="osint-ip") as pool:
        geo_future = pool.submit(lambda: parse_ipwho(net.get_json(IPWHO.format(ip=address), source="ipwho.is")))
        rdap_future = pool.submit(_rdap_lookup, "ip", address)
        results, errors, sources = {}, [], []
        for name, future, source in (("geo", geo_future, "ipwho.is"), ("rdap", rdap_future, "RDAP via rdap.org")):
            try:
                results[name] = future.result()
                sources.append(source)
            except UpstreamError as exc:
                results[name] = None
                errors.append(str(exc))
    if not sources:
        raise UpstreamError(503, " ".join(errors))
    geo_info, net_info = results["geo"] or {}, results["rdap"] or {}
    place = ", ".join(x for x in (geo_info.get("city"), geo_info.get("region"), geo_info.get("country")) if x)
    rows: list[tuple[str, Any]] = [
        ("Location", place or None),
        ("ASN", f"AS{geo_info['asn']}" if geo_info.get("asn") else None),
        ("Organisation", geo_info.get("org") or net_info.get("org")), ("ISP", geo_info.get("isp")),
        ("Network", ", ".join(net_info.get("cidr") or []) or net_info.get("name")),
        ("Registry country", net_info.get("country")),
    ]
    summary = [[k, str(v)] for k, v in rows if v]
    if errors:
        summary.append(["Partial", " ".join(errors)])
    return {"target": address, "summary": summary, "result": {"geo": results["geo"], "network": results["rdap"]},
            "sources": sources}


# --- GitHub ----------------------------------------------------------------------------------------

GITHUB = "https://api.github.com/users/{user}"
_GITHUB_USER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def parse_github(user: Any, repos: Any) -> dict:
    if not isinstance(user, dict) or not isinstance(user.get("login"), str):
        raise UpstreamError(502, "GitHub answered something unexpected.")
    top = sorted((r for r in repos if isinstance(r, dict)) if isinstance(repos, list) else [],
                 key=lambda r: r.get("stargazers_count") or 0, reverse=True)[:10]
    return {
        "login": user["login"], "name": text(user.get("name"), 120), "type": user.get("type"),
        "company": text(user.get("company"), 120), "blog": text(user.get("blog"), 200),
        "location": text(user.get("location"), 120), "bio": text(user.get("bio"), 300),
        "public_repos": user.get("public_repos"), "followers": user.get("followers"),
        "created": user.get("created_at"), "url": user.get("html_url"),
        "repos": [{"name": r.get("name"), "stars": r.get("stargazers_count"), "forks": r.get("forks_count"),
                   "language": r.get("language"), "description": text(r.get("description"), 200),
                   "url": r.get("html_url"), "pushed": r.get("pushed_at"), "fork": bool(r.get("fork"))} for r in top],
    }


def github(target: str) -> dict:
    name = (target or "").strip().lstrip("@")
    if not _GITHUB_USER.match(name):
        raise _bad("GitHub names are 1-39 letters, digits or single hyphens, like torvalds.")
    try:
        user = net.get_json(GITHUB.format(user=name), source="GitHub", headers=_GITHUB_HEADERS)
        repos = net.get_json(GITHUB.format(user=name) + "/repos", source="GitHub", headers=_GITHUB_HEADERS,
                             params={"per_page": 100, "sort": "pushed"})
    except UpstreamError as exc:
        if exc.status == 403:
            raise UpstreamError(429, "GitHub's anonymous rate limit (60 requests an hour) is used up; try later.") from exc
        if exc.status == 404:
            raise UpstreamError(404, f"GitHub has no user or organisation called {name}.") from exc
        raise
    result = parse_github(user, repos)
    rows: list[tuple[str, Any]] = [
        ("Name", result["name"]), ("Type", result["type"]), ("Company", result["company"]),
        ("Location", result["location"]), ("Public repos", result["public_repos"]), ("Followers", result["followers"]),
        ("Joined", (result["created"] or "")[:10] or None),
        ("Top repos", ", ".join(f"{r['name']} ({r['stars']}★)" for r in result["repos"][:3]) or None),
    ]
    return {"target": result["login"], "summary": [[k, str(v)] for k, v in rows if v not in (None, "")],
            "result": result, "sources": ["GitHub REST API"]}


# --- MAC vendor ------------------------------------------------------------------------------------

MACVENDORS = "https://api.macvendors.com/{oui}"
_mac_lock = threading.Lock()
_mac_last = [0.0]
MAC_INTERVAL = 1.1  # the free API allows about one request a second


def parse_mac(target: str) -> tuple[str, int]:
    """A MAC address or OUI in any common notation → ("AA:BB:CC", first octet)."""
    digits = re.sub(r"[\s:.\-]", "", (target or "").strip())
    if not re.fullmatch(r"[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{6})?", digits):
        raise _bad("Give a MAC address like 00:1A:2B:3C:4D:5E or its first half (OUI) like 00-1A-2B.")
    oui = digits[:6].upper()
    return ":".join(oui[i:i + 2] for i in range(0, 6, 2)), int(oui[:2], 16)


def mac(target: str) -> dict:
    oui, first = parse_mac(target)
    notes = []
    if first & 0x01:
        notes.append("multicast/group address")
    if first & 0x02:
        # Locally administered addresses (phones' randomised Wi-Fi MACs) have no registered vendor.
        return {"target": oui, "summary": [["Vendor", "None: this is a locally administered (often randomised) address"]],
                "result": {"oui": oui, "vendor": None, "local": True, "notes": notes}, "sources": []}
    with _mac_lock:  # space out calls across threads so a burst does not trip the rate limit
        wait = MAC_INTERVAL - (time.monotonic() - _mac_last[0])
        if wait > 0:
            time.sleep(wait)
        _mac_last[0] = time.monotonic()
        try:
            vendor = text(net.get(MACVENDORS.format(oui=oui), source="macvendors.com", timeout=10.0).text(), 160)
        except UpstreamError as exc:
            if exc.status != 404:
                raise
            vendor = None
    summary = [["OUI", oui], ["Vendor", vendor or "Not registered (no vendor found)"]]
    if notes:
        summary.append(["Notes", ", ".join(notes)])
    return {"target": oui, "summary": summary, "result": {"oui": oui, "vendor": vendor, "local": False, "notes": notes},
            "sources": ["macvendors.com"]}


# --- Entry point -----------------------------------------------------------------------------------

_RUNNERS: dict[str, Callable[[str], dict]] = {
    "dns": dns, "rdap": rdap, "tls": tls, "headers": headers, "subdomains": subdomains,
    "cve": cve, "ip": ip, "github": github, "mac": mac,
}


def run(kind: str, target: str | None) -> dict:
    """``GET /recon/{kind}?q=``. Successful answers are cached briefly per (kind, target)."""
    if kind not in _RUNNERS:
        raise UpstreamError(404, f"Unknown recon kind {str(kind)[:20]!r}; use one of {', '.join(KINDS)}.")
    query = (target or "").strip()
    if not query:
        raise _bad("Give a target in q, for example q=example.com.")
    if len(query) > 300:
        raise _bad("That target is too long.")
    cache = _caches.get(kind, _default_cache)
    result = cache.get_or_load(f"{kind}:{query.lower()}", lambda: _RUNNERS[kind](query))
    return {"kind": kind, **result}
