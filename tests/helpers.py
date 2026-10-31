"""Shared test doubles. No network: every outbound call goes through FakeHttp."""

from __future__ import annotations

import sys
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from livres.config import Settings  # noqa: E402
from livres.domain import Acquisition, Book, Canary, Mode, SourceSpec  # noqa: E402
from livres.http import FetchError, Response, host_allowed  # noqa: E402

import logging  # noqa: E402

logging.disable(logging.CRITICAL)

FIXTURES = ROOT / "tests" / "fixtures"
SOURCES = ROOT / "sources"


def fixture(path: str) -> bytes:
    return (FIXTURES / path).read_bytes()


class FakeHttp:
    """url -> bytes | Response | Exception | callable(url)->(...). Unknown URLs raise a network error."""

    def __init__(self, routes: dict | None = None):
        self.routes = dict(routes or {})
        self.calls: list[str] = []
        self.probes: list[str] = []
        self.lock = threading.Lock()

    def _answer(self, url, allowed_hosts):
        with self.lock:
            self.calls.append(url)
        if not host_allowed(url, allowed_hosts):
            raise FetchError("host", f"host not declared: {url}")
        value = self.routes.get(url)
        if callable(value):
            value = value(url)
        if value is None:
            raise FetchError("network", f"no fake route for {url}")
        if isinstance(value, Exception):
            raise value
        if isinstance(value, Response):
            return value
        return Response(url, 200, {"content-type": "application/octet-stream"}, value)

    def get(self, url, *, allowed_hosts, max_bytes=20_000_000, accept="*/*"):
        return self._answer(url, allowed_hosts)

    def probe(self, url, *, allowed_hosts):
        with self.lock:
            self.probes.append(url)
        value = self.routes.get("PROBE " + url, Response(url, 206, {"content-type": "application/epub+zip"}, b"PK\x03\x04"))
        if isinstance(value, Exception):
            raise value
        return value


class FakeTransport:
    def __init__(self, fail: Exception | None = None):
        self.sent = []
        self.fail = fail

    def send(self, msg):
        if self.fail:
            raise self.fail
        self.sent.append(msg)


def make_settings(tmp: str | Path, **overrides) -> Settings:
    base = Settings.from_env({})
    values = dict(
        data_dir=Path(tmp),
        sources_dir=SOURCES,
        base_url="https://livres.test",
        owner_email="owner@example.org",
        mail_from="livres@example.org",
        smtp_host="smtp.example.org",
        scheduler=False,
        min_host_interval=0,
    )
    values.update(overrides)
    return replace(base, **values)


def spec(id="src", kind="opds1", modes=(Mode.SEARCH,), config=None, hosts=("books.example",), priority=50,
         enabled=True, canary=None, name=None) -> SourceSpec:
    return SourceSpec(
        id=id, name=name or id.title(), kind=kind, homepage="https://books.example/",
        legal="Domaine public, diffusé gratuitement par le site, sans DRM.",
        modes=tuple(modes), config=config if config is not None else {"search_url": "https://books.example/s?q={searchTerms}"},
        allowed_hosts=tuple(hosts), canary=canary or Canary("hugo"), enabled=enabled, priority=priority,
    )


def book(identifier="src:1", title="Les Misérables", authors=("Victor Hugo",), source_id="src", types=("application/epub+zip",)) -> Book:
    return Book(
        identifier=identifier, source_id=source_id, title=title, authors=tuple(authors),
        acquisitions=tuple(Acquisition(f"https://books.example/{identifier}.{t.rsplit('/', 1)[-1]}", t) for t in types),
    )


class TempDir:
    def __enter__(self):
        self._t = tempfile.TemporaryDirectory()
        return self._t.name

    def __exit__(self, *exc):
        self._t.cleanup()
