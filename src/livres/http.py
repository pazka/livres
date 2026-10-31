"""Polite outbound HTTP: the ONLY way the service talks to sources.

Guarantees (each has a test in tests/test_http.py):
  * robots.txt is honoured for every URL (RFC 9309: 4xx = allowed, 5xx/unreachable = disallowed);
  * only hosts declared by the source are contacted, redirects included;
  * at most one request per host every `min_interval` seconds;
  * bodies are capped (no memory blow-up from a hostile or broken server);
  * a clear User-Agent with a contact page.
"""

from __future__ import annotations

import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from typing import Protocol


class FetchError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # network | http | robots | host | too_large


@dataclass
class Response:
    url: str
    status: int
    headers: dict
    body: bytes

    def text(self) -> str:
        charset = "utf-8"
        ctype = self.headers.get("content-type", "")
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";", 1)[0].strip() or "utf-8"
        return self.body.decode(charset, errors="replace")


class HttpClient(Protocol):
    def get(self, url: str, *, allowed_hosts: tuple[str, ...], max_bytes: int = ..., accept: str = ...) -> Response: ...

    def probe(self, url: str, *, allowed_hosts: tuple[str, ...]) -> Response:
        """Fetch only the first bytes of a download (Range request) to prove it still works."""
        ...


def host_allowed(url: str, allowed_hosts: tuple[str, ...]) -> bool:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http"):
        return False
    host = (parts.hostname or "").lower()
    for allowed in allowed_hosts:
        allowed = allowed.lower()
        if allowed.startswith("*.") and host.endswith(allowed[1:]):
            return True
        if host == allowed:
            return True
    return False


class _RedirectGuard(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...]):
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not host_allowed(newurl, self.allowed_hosts):
            raise FetchError("host", f"redirect to an undeclared host: {urllib.parse.urlsplit(newurl).hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PoliteHttpClient:
    ROBOTS_TTL = 24 * 3600
    ROBOTS_RETRY = 300  # an unreadable robots.txt is retried after 5 minutes

    def __init__(self, user_agent: str, timeout: float = 10.0, min_interval: float = 1.0, opener_factory=None):
        self.user_agent = user_agent
        self.timeout = timeout
        self.min_interval = min_interval
        self._opener_factory = opener_factory or (lambda hosts: urllib.request.build_opener(_RedirectGuard(hosts)))
        self._robots: dict[str, tuple[float, urllib.robotparser.RobotFileParser | FetchError]] = {}
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()
        self._host_locks: dict[str, threading.Lock] = {}

    # -- public -------------------------------------------------------------------------------
    def get(self, url, *, allowed_hosts, max_bytes=20_000_000, accept="*/*") -> Response:
        return self._fetch(url, allowed_hosts, max_bytes, {"Accept": accept})

    def probe(self, url, *, allowed_hosts) -> Response:
        return self._fetch(url, allowed_hosts, 4096, {"Range": "bytes=0-4095"}, truncate=True)

    # -- internals ----------------------------------------------------------------------------
    def _fetch(self, url, allowed_hosts, max_bytes, headers, truncate=False) -> Response:
        if not host_allowed(url, allowed_hosts):
            raise FetchError("host", f"host not declared for this source: {urllib.parse.urlsplit(url).hostname}")
        if not self.allowed_by_robots(url, allowed_hosts):
            raise FetchError("robots", f"robots.txt disallows {url}")
        # (allowed_by_robots raises FetchError("network"/"http") when robots.txt cannot be read)
        host = urllib.parse.urlsplit(url).hostname or ""
        with self._host_lock(host):
            self._throttle(host)
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, **headers})
            try:
                with self._opener_factory(tuple(allowed_hosts)).open(req, timeout=self.timeout) as resp:
                    body = resp.read(max_bytes + 1)
                    if len(body) > max_bytes and not truncate:
                        raise FetchError("too_large", f"response larger than {max_bytes} bytes")
                    return Response(resp.geturl(), resp.status, {k.lower(): v for k, v in resp.headers.items()}, body[:max_bytes])
            except FetchError:
                raise
            except urllib.error.HTTPError as e:
                raise FetchError("http", f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise FetchError("network", f"{type(e).__name__}: {getattr(e, 'reason', e)}") from e

    def allowed_by_robots(self, url: str, allowed_hosts) -> bool:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        now = time.monotonic()
        with self._lock:
            cached = self._robots.get(origin)
        ttl = self.ROBOTS_TTL if cached is None or not isinstance(cached[1], FetchError) else self.ROBOTS_RETRY
        if cached is None or now - cached[0] > ttl:
            parser = self._load_robots(origin, allowed_hosts)
            with self._lock:
                self._robots[origin] = (now, parser)
            cached = (now, parser)
        parser = cached[1]
        if isinstance(parser, FetchError):
            # robots.txt unreachable or 5xx: RFC 9309 says assume complete disallow, so we fetch nothing.
            # Reported as the network/http failure it is (not as a robots ban, which needs a human).
            raise parser
        return parser.can_fetch(self.user_agent, url)

    def _load_robots(self, origin, allowed_hosts):
        parser = urllib.robotparser.RobotFileParser()
        req = urllib.request.Request(origin + "/robots.txt", headers={"User-Agent": self.user_agent})
        host = urllib.parse.urlsplit(origin).hostname or ""
        try:
            with self._host_lock(host):
                self._throttle(host)
                with self._opener_factory(tuple(allowed_hosts)).open(req, timeout=self.timeout) as resp:
                    parser.parse(resp.read(500_000).decode("utf-8", errors="replace").splitlines())
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                parser.parse([])  # no robots.txt: everything allowed
            else:
                return FetchError("http", f"robots.txt answers HTTP {e.code}: nothing fetched from {origin}")
        except FetchError as e:
            return e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return FetchError("network", f"robots.txt unreachable ({type(e).__name__}): nothing fetched from {origin}")
        return parser

    def _host_lock(self, host):
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def _throttle(self, host):
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()
