"""Actual destination validation, redirects, body bounds and worker deadline contracts."""
import io
import socket
import threading
import time
from unittest.mock import Mock

import pytest
import rag.public_web as web


def dns(address):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]


@pytest.mark.parametrize("address", ["127.0.0.1", "10.2.3.4", "169.254.169.254", "192.168.1.2", "0.0.0.0", "::1", "fe80::1", "224.0.0.1", "ff02::1", "::ffff:127.0.0.1"])
def test_block_nonpublic_destinations(monkeypatch, address):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: dns(address))
    with pytest.raises(ValueError):
        web.public_target("https://example.org/page")


@pytest.mark.parametrize("url", ["file:///secret", "ftp://example.org", "https://u:p@example.org", "http://example.org:8080"])
def test_reject_unsupported_urls_before_dns(monkeypatch, url):
    lookup = Mock(side_effect=AssertionError("DNS must not run"))
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    with pytest.raises(ValueError):
        web.public_target(url)
    lookup.assert_not_called()


class Response(io.BytesIO):
    status = 200

    def __init__(self, body=b"<main>SQL joins</main>", headers=None, status=200):
        super().__init__(body)
        self.headers = headers or {"Content-Type": "text/html"}
        self.status = status

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class Connection:
    sock = None

    def __init__(self, response):
        self.response = response

    def request(self, *args, **kwargs):
        self.request_args = (args, kwargs)

    def getresponse(self):
        return self.response

    def close(self):
        pass


def test_pins_actual_connection_to_validated_address(monkeypatch):
    lookup = Mock(side_effect=[dns("8.8.8.8"), dns("127.0.0.1")])
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    factory = Mock(return_value=Connection(Response()))
    monkeypatch.setattr(web, "_PinnedHTTPS", factory)
    url, text = web.fetch_public_page("https://example.org/page", time.monotonic() + 1)
    assert text == "SQL joins" and url == "https://example.org/page"
    assert factory.call_args.args[1] == "8.8.8.8"
    assert lookup.call_count == 1  # no second resolution after validation


def test_redirect_to_private_network_is_blocked_before_second_request(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: dns("8.8.8.8" if host == "example.org" else "127.0.0.1"))
    connection = Connection(Response(headers={"Location": "http://internal.local/secret"}, status=302))
    factory = Mock(return_value=connection)
    monkeypatch.setattr(web, "_PinnedHTTPS", factory)
    with pytest.raises(ValueError):
        web.fetch_public_page("https://example.org/page", time.monotonic() + 1)
    assert factory.call_count == 1


@pytest.mark.parametrize("response", [Response(b"x" * (web.MAX_BODY + 1)),
                                     Response(headers={"Content-Type": "text/html", "Content-Encoding": "gzip"}),
                                     Response(headers={"Content-Type": "application/octet-stream"})])
def test_reject_oversized_encoded_or_unsupported_responses(monkeypatch, response):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: dns("8.8.8.8"))
    monkeypatch.setattr(web, "_PinnedHTTPS", lambda *a, **k: Connection(response))
    with pytest.raises(ValueError):
        web.fetch_public_page("https://example.org", time.monotonic() + 2)


def test_hanging_research_returns_by_deadline_and_keeps_busy_slot(monkeypatch):
    event = threading.Event()
    monkeypatch.setattr(web, "_research", lambda *a: (event.wait(1), "ok"))
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(web, "_slots", slots)
    started = time.monotonic()
    try:
        assert web.research_web("SQL", seconds=0.02) == ([], "unavailable")
        assert time.monotonic() - started < 0.5
        assert web.research_web("SQL", seconds=0.02) == ([], "unavailable")
    finally:
        event.set()
        # Callback releases the same semaphore object even if monkeypatch later restores globals.
        deadline = time.monotonic() + 1
        while not slots.acquire(blocking=False) and time.monotonic() < deadline:
            time.sleep(0.01)
