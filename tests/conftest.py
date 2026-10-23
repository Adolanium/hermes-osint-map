import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    path = FIXTURES / name
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return path.read_text(encoding="utf-8")


@pytest.fixture
def fixture():
    return load


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Unit tests never reach the internet: any real connection attempt fails loudly."""
    import socket

    real = socket.create_connection

    def refuse(address, *args, **kwargs):
        host = address[0]
        if host in ("127.0.0.1", "::1", "localhost"):
            return real(address, *args, **kwargs)
        raise AssertionError(f"unit test tried to connect to {address}")

    real_dns = socket.getaddrinfo

    def no_dns(host, *args, **kwargs):
        if host in ("127.0.0.1", "::1", "localhost"):
            return real_dns(host, *args, **kwargs)
        raise AssertionError(f"unit test tried to resolve {host}")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
