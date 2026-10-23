"""Recon target validation, parsers and the guarded tls/headers connectors."""

import http.server
import socket
import threading
from datetime import datetime, timezone

import pytest

from osintmap import http as net
from osintmap import recon
from osintmap.http import UpstreamError


def status_of(call):
    with pytest.raises(UpstreamError) as err:
        call()
    return err.value.status, str(err.value)


# --- Validation ------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind,target", [
    ("dns", "localhost"), ("dns", "http://example.com"), ("dns", "10.0.0.1"), ("subdomains", "example"),
    ("rdap", "not a thing"), ("rdap", "192.168.1.1"), ("ip", "10.1.2.3"), ("ip", "::1"), ("ip", "example.com"),
    ("ip", "100.64.1.1"), ("tls", "example.com:22"), ("tls", "example.com:80"), ("tls", "https://example.com"),
    ("headers", "ftp://example.com"), ("headers", "https://user:pw@example.com/"), ("headers", "https://example.com:22/"),
    ("headers", "file:///etc/passwd"), ("cve", "CVE-21-1"), ("cve", "log4shell"), ("github", "-bad-"),
    ("github", "a" * 40), ("mac", "zz:zz:zz"), ("mac", "00:11"), ("dns", ""), ("dns", "x" * 400),
])
def test_bad_targets_are_refused_before_any_request(kind, target):
    code, message = status_of(lambda: recon.run(kind, target))
    assert code == 400 and message.endswith(".")


def test_unknown_kind():
    code, message = status_of(lambda: recon.run("nmap", "example.com"))
    assert code == 404 and "dns" in message


def test_ip_literal_redirect_targets_are_refused_by_the_guard():
    # A literal private/loopback host never needs DNS: the guard refuses it on sight.
    code, message = status_of(lambda: recon.headers("http://127.0.0.1/"))
    assert code == 403 and "loopback" in message
    code, message = status_of(lambda: recon.headers("http://[::ffff:169.254.169.254]/latest/meta-data"))
    assert code == 403 and "link-local" in message


def test_split_host_port_and_url_targets():
    assert recon.split_host_port("Example.com:8443", recon.TLS_PORTS, 443) == ("example.com", 8443)
    assert recon.parse_url_target("example.com") == ("https", "example.com", 443, "/")
    assert recon.parse_url_target("http://example.com:8080/a?b=1") == ("http", "example.com", 8080, "/a?b=1")


# --- Headers end to end through a local server ----------------------------------------------------


class Redirector(http.server.BaseHTTPRequestHandler):
    location = "http://internal.example.com/admin"

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", self.location)
        self.send_header("Server", "test")
        self.end_headers()

    def log_message(self, *args):
        pass


class Plain(Redirector):
    def do_GET(self):
        self.send_response(200)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'")
        self.end_headers()
        self.wfile.write(b"body that is never read")


@pytest.fixture
def local_server(monkeypatch):
    servers = []

    def start(handler):
        server = http.server.HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address

    # "public.example.com" resolves to a public-looking address; connections to it are redirected
    # to the local server. Everything else resolves to a private address.
    names = {"public.example.com": ["93.184.215.14"], "internal.example.com": ["10.20.30.40"]}
    monkeypatch.setattr(net, "_system_resolve", lambda host, port: names.get(host, ["192.168.0.9"]))
    target = {}
    real = socket.create_connection

    def connect(address, timeout=None, *args, **kwargs):
        if address[0] == "93.184.215.14":
            return real(target["addr"], timeout)
        raise AssertionError(f"connected to {address}")

    monkeypatch.setattr(socket, "create_connection", connect)
    yield lambda handler: target.update(addr=start(handler))
    for server in servers:
        server.shutdown()


def test_headers_redirect_to_a_private_host_is_refused(local_server):
    local_server(Redirector)
    code, message = status_of(lambda: recon.headers("http://public.example.com/"))
    assert code == 403 and "10.20.30.40" in message and "private" in message


def test_headers_redirect_to_a_disallowed_port_is_refused(local_server, monkeypatch):
    monkeypatch.setattr(Redirector, "location", "http://public.example.com:6379/")
    local_server(Redirector)
    code, message = status_of(lambda: recon.headers("http://public.example.com/"))
    assert code == 400 and "6379" in message


def test_headers_analysis(local_server):
    local_server(Plain)
    answer = recon.headers("http://public.example.com/")
    result = answer["result"]
    assert result["chain"] == [{"url": "http://public.example.com/", "status": 200, "address": "93.184.215.14", "note": None}]
    assert set(result["present"]) == {"CSP", "X-Content-Type-Options"}
    assert "HSTS" not in result["missing"]  # HSTS only means something over https
    assert "X-Frame-Options" in result["missing"]


# --- Parsers ---------------------------------------------------------------------------------------


def test_parse_doh_filters_to_the_asked_type():
    raw = {"Status": 0, "Answer": [{"name": "www.example.com.", "type": 5, "data": "example.com."},
                                   {"name": "example.com.", "type": 1, "data": "93.184.215.14"},
                                   {"name": "example.com.", "type": 1, "data": "93.184.215.14"}]}
    assert recon.parse_doh(raw, "A") == (0, ["93.184.215.14"])
    assert recon.parse_doh({"Status": 0, "Answer": [{"type": 16, "data": '"v=spf1 -all"'}]}, "TXT") == (0, ["v=spf1 -all"])
    assert recon.parse_doh({"Status": 3}, "A") == (3, [])


def test_dns_nxdomain(monkeypatch):
    monkeypatch.setattr(recon.net, "get_json", lambda url, **k: {"Status": 3})
    code, message = status_of(lambda: recon.dns("no-such-name-here.com"))
    assert code == 404 and "NXDOMAIN" in message


def vcard(name):
    return ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", name]]]


def test_parse_rdap_domain():
    raw = {"objectClassName": "domain", "ldhName": "EXAMPLE.COM", "status": ["client transfer prohibited"],
           "events": [{"eventAction": "registration", "eventDate": "1995-08-14T04:00:00Z"},
                      {"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"}],
           "entities": [{"roles": ["registrar"], "vcardArray": vcard("RESERVED-Internet Assigned Numbers Authority")}],
           "nameservers": [{"ldhName": "A.IANA-SERVERS.NET"}, {"ldhName": "B.IANA-SERVERS.NET"}]}
    result = recon.parse_rdap(raw)
    assert result["kind"] == "domain" and result["registrar"].startswith("RESERVED")
    assert result["nameservers"] == ["a.iana-servers.net", "b.iana-servers.net"]
    assert result["registered"].startswith("1995") and result["expires"].startswith("2027")


def test_parse_rdap_ip_network():
    raw = {"objectClassName": "ip network", "handle": "NET-8-8-8-0-2", "name": "GOGL", "startAddress": "8.8.8.0",
           "endAddress": "8.8.8.255", "country": "US", "type": "DIRECT ALLOCATION",
           "cidr0_cidrs": [{"v4prefix": "8.8.8.0", "length": 24}],
           "entities": [{"roles": ["registrant"], "vcardArray": vcard("Google LLC"),
                         "entities": [{"roles": ["abuse"], "vcardArray": vcard("Abuse")}]}]}
    result = recon.parse_rdap(raw)
    assert result["kind"] == "ip" and result["cidr"] == ["8.8.8.0/24"] and result["org"] == "Google LLC"


def test_parse_peer_cert():
    cert = {"subject": ((("commonName", "github.com"),),), "issuer": ((("organizationName", "Sectigo Limited"),),
            (("commonName", "Sectigo DV E36"),)), "subjectAltName": tuple(("DNS", f"n{i}.example.com") for i in range(70)),
            "notBefore": "Feb  5 00:00:00 2026 GMT", "notAfter": "Nov 29 23:59:59 2026 GMT", "serialNumber": "AB"}
    result = recon.parse_peer_cert(cert, now=datetime(2026, 10, 22, tzinfo=timezone.utc).timestamp())
    assert result["subject"] == "github.com" and result["issuer"] == "Sectigo Limited, Sectigo DV E36"
    assert len(result["sans"]) == 50 and result["san_count"] == 70
    assert result["days_left"] == 38


def test_parse_crtsh():
    raw = [{"name_value": "example.com\nwww.example.com", "not_before": "2024-01-01T00:00:00"},
           {"name_value": "*.api.example.com\nevil.com", "not_before": "2026-05-01T00:00:00"},
           {"name_value": "www.example.com", "not_before": "2025-03-01T00:00:00"}]
    names, total = recon.parse_crtsh(raw, "example.com")
    assert total == 3 and names[0]["name"] == "api.example.com"
    www = next(n for n in names if n["name"] == "www.example.com")
    assert www["certs"] == 2 and www["first_seen"] < www["last_seen"]
    assert all(n["name"].endswith("example.com") for n in names)
    capped, total = recon.parse_crtsh(raw, "example.com", limit=1)
    assert len(capped) == 1 and total == 3


def test_parse_cve_and_kev(fixture):
    record = recon.parse_cve(fixture("cve_log4shell.json"), "CVE-2021-44228")
    assert record["cvss"]["score"] == 10.0 and record["cvss"]["version"] == "3.1"
    assert "CWE-502" in record["cwe"] and record["published"].startswith("2021-12-10")
    assert record["title"].startswith("Apache Log4j2")
    kev = recon.parse_kev({"vulnerabilities": [{"cveID": "CVE-2021-44228", "dateAdded": "2021-12-10",
                                                "dueDate": "2021-12-24", "knownRansomwareCampaignUse": "Known"}]})
    assert kev["CVE-2021-44228"]["due"] == "2021-12-24"
    code, _ = status_of(lambda: recon.parse_cve({"error": "not found"}, "CVE-2099-0001"))
    assert code == 404


def test_cve_answers_even_when_kev_is_down(monkeypatch, fixture):
    record = fixture("cve_log4shell.json")
    monkeypatch.setattr(recon, "_kev_cache", net.TTLCache(60))

    def fake(url, **kwargs):
        if "cisa.gov" in url:
            raise UpstreamError(503, "CISA KEV is having trouble (HTTP 503).")
        return record

    monkeypatch.setattr(recon.net, "get_json", fake)
    answer = recon.cve("cve-2021-44228")
    assert answer["target"] == "CVE-2021-44228" and answer["result"]["kev_checked"] is False
    assert ["Known exploited (CISA KEV)", "Could not check"] in answer["summary"]


def test_parse_ipwho():
    raw = {"success": True, "ip": "8.8.8.8", "country": "United States", "country_code": "US", "city": "Mountain View",
           "latitude": 37.38, "longitude": -122.08, "connection": {"asn": 15169, "org": "Google LLC", "isp": "Google LLC"},
           "timezone": {"id": "America/Los_Angeles"}}
    result = recon.parse_ipwho(raw)
    assert result["asn"] == 15169 and result["country_code"] == "US" and result["timezone"] == "America/Los_Angeles"
    code, _ = status_of(lambda: recon.parse_ipwho({"success": False, "message": "Reserved range"}))
    assert code == 404


def test_parse_github_top_repos_by_stars():
    user = {"login": "octo", "name": "Octo Cat", "type": "User", "public_repos": 3, "followers": 9,
            "created_at": "2011-01-25T18:44:36Z", "html_url": "https://github.com/octo"}
    repos = [{"name": f"r{i}", "stargazers_count": i, "html_url": f"https://github.com/octo/r{i}"} for i in range(15)]
    result = parse = recon.parse_github(user, repos)
    assert [r["name"] for r in parse["repos"]][:3] == ["r14", "r13", "r12"] and len(result["repos"]) == 10


@pytest.mark.parametrize("text,oui", [("00:1A:2B:3C:4D:5E", "00:1A:2B"), ("00-1a-2b", "00:1A:2B"),
                                       ("001A.2B3C.4D5E", "00:1A:2B"), ("001a2b", "00:1A:2B")])
def test_parse_mac(text, oui):
    assert recon.parse_mac(text)[0] == oui


def test_locally_administered_mac_needs_no_lookup(monkeypatch):
    monkeypatch.setattr(recon.net, "get", lambda *a, **k: pytest.fail("no network for a random MAC"))
    answer = recon.mac("DA:A1:19:00:00:01")
    assert answer["result"]["local"] is True and answer["result"]["vendor"] is None


def test_mac_unknown_vendor_is_an_answer_not_an_error(monkeypatch):
    monkeypatch.setattr(recon, "MAC_INTERVAL", 0)
    monkeypatch.setattr(recon.net, "get", lambda *a, **k: (_ for _ in ()).throw(UpstreamError(404, "x")))
    answer = recon.mac("00:00:5E:00:53:01")
    assert answer["result"]["vendor"] is None and answer["summary"][1][1].startswith("Not registered")


def test_successful_lookups_are_cached(monkeypatch):
    calls = []
    monkeypatch.setitem(recon._RUNNERS, "dns", lambda q: calls.append(q) or {"target": q, "summary": [], "result": {},
                                                                              "sources": []})
    monkeypatch.setattr(recon, "_default_cache", net.TTLCache(60))
    recon.run("dns", "Example.com")
    recon.run("dns", "example.com")
    assert calls == ["Example.com"]
