"""The adapter contract every source kind implements. See AGENTS.md, protocol P1."""

from __future__ import annotations

from abc import ABC
from collections.abc import Iterator

from ..domain import Book, Mode, SourceSpec
from ..http import FetchError, HttpClient, Response


class SourceError(Exception):
    """Raised by adapters. `kind` drives the health state: 'robots' -> BLOCKED, anything else -> failure."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # robots | network | http | parse | config | host | too_large


class SourceAdapter(ABC):
    """Turns one source into `Book`s.

    Rules for implementers (enforced by tests/test_registry.py and code review):
      * fetch only through `self.fetch()` (robots.txt, host allowlist, throttling, size cap);
      * return only books that are free, DRM-free and need no account (filter them out otherwise);
      * `identifier` = f"{spec.id}:{stable id at the source}";
      * raise SourceError, never a bare exception, when the source misbehaves.
    """

    #: config keys the adapter needs in SourceSpec.config
    required_config: tuple[str, ...] = ()

    def __init__(self, spec: SourceSpec, http: HttpClient):
        self.spec = spec
        self.http = http

    @property
    def id(self) -> str:
        return self.spec.id

    def supports(self, mode: Mode) -> bool:
        return mode in self.spec.modes

    def search(self, query: str, limit: int = 20) -> list[Book]:
        raise SourceError("config", f"{self.spec.id} does not support live search")

    def harvest(self) -> Iterator[Book]:
        raise SourceError("config", f"{self.spec.id} does not support harvesting")

    def fetch(self, url: str, *, max_bytes: int = 20_000_000, accept: str = "*/*") -> Response:
        try:
            resp = self.http.get(url, allowed_hosts=self.spec.allowed_hosts, max_bytes=max_bytes, accept=accept)
        except FetchError as e:
            raise SourceError(e.kind, str(e)) from e
        if resp.status >= 400:
            raise SourceError("http", f"HTTP {resp.status} for {url}")
        return resp

    def probe(self, url: str) -> Response:
        try:
            return self.http.probe(url, allowed_hosts=self.spec.allowed_hosts)
        except FetchError as e:
            raise SourceError(e.kind, str(e)) from e
