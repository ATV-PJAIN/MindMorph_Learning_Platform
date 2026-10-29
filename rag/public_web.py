"""Bounded public research with DNS-pinned connections and validated redirects."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from html.parser import HTMLParser
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_BODY = 2 * 1024 * 1024
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="public-research")
_slots = threading.BoundedSemaphore(2)


def public_target(url: str) -> tuple[str, str, int, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTP(S) URLs without credentials are supported")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port != (443 if parsed.scheme == "https" else 80):
        raise ValueError("Nonstandard public ports are unsupported")
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    ips = {item[4][0] for item in addresses}
    if not ips or any(not ipaddress.ip_address(ip).is_global or ipaddress.ip_address(ip).is_multicast
                      or ipaddress.ip_address(ip).is_reserved for ip in ips):
        raise ValueError("Private or reserved destinations are blocked")
    # The selected IP is used for the actual connection, without a second DNS lookup.
    return parsed.scheme, parsed.hostname, port, sorted(ips)[0]


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, address, port, timeout):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "footer", "header", "form"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "header", "form"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.parts.append(data.strip())


def fetch_public_page(url: str, deadline: float) -> tuple[str, str]:
    """At most three redirects and 2 MiB, no cookies, proxies, scripts or decompression."""
    for redirect in range(4):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Research deadline exceeded")
        scheme, host, port, address = public_target(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Research deadline exceeded")
        conn = (_PinnedHTTPS(host, address, port, remaining) if scheme == "https"
                else http.client.HTTPConnection(address, port, timeout=remaining))
        parsed = urlsplit(url)
        path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        try:
            conn.request("GET", path, headers={"Host": host, "User-Agent": "MindMorph/1.0",
                                              "Accept-Encoding": "identity", "Accept": "text/html,text/plain"})
            response = conn.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if redirect == 3 or not location:
                    raise ValueError("Unsupported redirect chain")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ValueError("Public page unavailable")
            content_type = response.getheader("Content-Type", "").lower()
            if not any(t in content_type for t in ("text/html", "text/plain")):
                raise ValueError("Unsupported public page type")
            if response.getheader("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise ValueError("Compressed responses unsupported")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Research deadline exceeded")
                if conn.sock:
                    conn.sock.settimeout(remaining)
                block = response.read1(min(65536, MAX_BODY + 1 - size))
                if not block:
                    break
                size += len(block)
                if size > MAX_BODY:
                    raise ValueError("Public page exceeds size limit")
                chunks.append(block)
            body = b"".join(chunks).decode("utf-8", errors="replace")
            if "text/html" in content_type:
                parser = _Text()
                parser.feed(body)
                body = " ".join(parser.parts)
            return url, body[:12000]
        finally:
            conn.close()
    raise ValueError("Too many redirects")


def _research(query: str, deadline: float) -> tuple[list[dict], str]:
    from ddgs import DDGS
    from services.generation_privacy import private_context
    # The dedicated worker does not inherit contextvars automatically.
    with private_context():
        with DDGS(timeout=max(1, deadline - time.monotonic())) as ddgs:
            results = list(ddgs.text(query[:1000], max_results=5))[:5]
        passages, degraded = [], False
        for index, item in enumerate(results):
            if time.monotonic() >= deadline:
                return passages, "unavailable"
            url = item.get("href") or item.get("url") or ""
            snippet = (item.get("body") or "")[:1000]
            try:
                public_target(url)
            except Exception:
                degraded = True
                continue
            title = (item.get("title") or "Public source")[:200]
            text, kind = snippet, "web_snippet"
            if index < 3:
                try:
                    url, body = fetch_public_page(url, deadline)
                    # Prefer the relevant search window rather than an unrelated page header.
                    words = [w.lower() for w in query.split() if len(w) > 3]
                    offsets = [body.lower().find(w) for w in words if w in body.lower()]
                    offset = max(0, min(offsets, default=0) - 200)
                    if body.strip():
                        text, kind = body[offset:offset + 1600], "web_page"
                except Exception:
                    degraded = True
            if text.strip():
                passages.append({"text": text, "source": title, "url": url, "kind": kind})
        return passages, "partial" if degraded and passages else ("unavailable" if degraded else "ok")


def research_web(query: str, seconds: float = 20) -> tuple[list[dict], str]:
    """Bound returned latency and outstanding workers even if an upstream search hangs."""
    if not _slots.acquire(blocking=False):
        return [], "unavailable"
    try:
        future = _pool.submit(_research, query, time.monotonic() + seconds)
    except BaseException:
        _slots.release()
        raise
    future.add_done_callback(lambda _, slots=_slots: slots.release())
    try:
        return future.result(timeout=seconds)
    except Exception:
        return [], "unavailable"
