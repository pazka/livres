"""Pure domain types. No I/O here: everything is a frozen dataclass so it can be shared between threads."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

# Media types, ordered by how well they suit a Kindle 4 through "Ma liseuse":
# EPUB is converted by the app (and kept as the master copy), MOBI goes on as is, PDF reads badly.
EPUB = "application/epub+zip"
MOBI = "application/x-mobipocket-ebook"
PDF = "application/pdf"
FORMAT_PREFERENCE = (EPUB, MOBI, PDF)

FORMAT_ALIASES = {
    "application/epub": EPUB,
    "application/x-mobi8-ebook": MOBI,
    "application/vnd.amazon.ebook": MOBI,
    "application/x-mobipocket": MOBI,
}

# Anything that needs a DRM-capable reader or an account: never offered to Mom.
DRM_TYPES = frozenset(
    {
        "application/vnd.adobe.adept+xml",
        "application/vnd.readium.lcp.license.v1.0+json",
        "application/vnd.readium.license.status.v1.0+json",
        "application/vnd.librarysimplified.bearer-token+json",
    }
)


def normalise_type(media_type: str) -> str:
    base = (media_type or "").split(";", 1)[0].strip().lower()
    return FORMAT_ALIASES.get(base, base)


@dataclass(frozen=True)
class Acquisition:
    """A direct, free, DRM-free download link at the original source."""

    href: str
    media_type: str
    size: int | None = None

    @property
    def rank(self) -> int:
        t = normalise_type(self.media_type)
        return FORMAT_PREFERENCE.index(t) if t in FORMAT_PREFERENCE else len(FORMAT_PREFERENCE)


@dataclass(frozen=True)
class Book:
    """One publication as found at one source.

    `identifier` is stable across refreshes: "<source_id>:<id at the source>" (the app stores it).
    """

    identifier: str
    source_id: str
    title: str
    authors: tuple[str, ...] = ()
    language: str = "fr"
    description: str = ""
    cover: str | None = None
    page_url: str | None = None
    acquisitions: tuple[Acquisition, ...] = ()
    issued: str | None = None

    def sorted_acquisitions(self) -> tuple[Acquisition, ...]:
        return tuple(sorted(self.acquisitions, key=lambda a: a.rank))

    @property
    def usable(self) -> bool:
        """True when Mom can actually get the book: at least one EPUB/MOBI/PDF link."""
        return any(a.rank < len(FORMAT_PREFERENCE) for a in self.acquisitions)

    def dedupe_key(self) -> str:
        author = self.authors[0] if self.authors else ""
        return fold(self.title) + "|" + _surname(author)


class Mode(str, Enum):
    SEARCH = "search"  # live query forwarded to the source (polite: cached, rate-limited)
    HARVEST = "harvest"  # whole catalogue downloaded periodically into the local index


class HealthState(str, Enum):
    UNKNOWN = "unknown"
    OK = "ok"
    DEGRADED = "degraded"  # failed the last check, not yet alerting
    DOWN = "down"  # failed `fail_threshold` checks in a row: owner alerted, GitHub issue opened
    BLOCKED = "blocked"  # robots.txt or terms forbid us: never fetched, needs a human decision


@dataclass(frozen=True)
class Canary:
    query: str
    min_results: int = 1
    check_download: bool = True


@dataclass(frozen=True)
class SourceSpec:
    """Declarative description of a source (sources/*.json, or added from the admin page)."""

    id: str
    name: str
    kind: str
    homepage: str
    legal: str
    modes: tuple[Mode, ...]
    config: dict
    allowed_hosts: tuple[str, ...]
    canary: Canary
    enabled: bool = True
    priority: int = 50
    status: str = "verified"  # verified | candidate (not yet proven by a live canary)
    notes: str = ""
    origin: str = "repo"  # repo | admin


@dataclass(frozen=True)
class CheckResult:
    source_id: str
    ok: bool
    detail: str
    results: int = 0
    duration_ms: int = 0
    blocked: bool = False


@dataclass
class SearchPage:
    query: str
    page: int
    page_size: int
    books: list[Book]
    has_next: bool
    errors: dict[str, str] = field(default_factory=dict)


_SPACES = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def fold(text: str) -> str:
    """Lowercase, strip accents and punctuation: 'Les Misérables !' -> 'les miserables'."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _NON_WORD.sub(" ", text.lower())
    return _SPACES.sub(" ", text).strip()


def _surname(author: str) -> str:
    author = fold(author.split(",", 1)[0] if "," in author else (author.split()[-1] if author.split() else ""))
    return author


def display_author(raw: str) -> str:
    """'Hugo, Victor, 1802-1885' -> 'Victor Hugo'."""
    parts = [p.strip() for p in (raw or "").split(",")]
    parts = [p for p in parts if p and not re.fullmatch(r"[\d\s?\-–.BCE]+", p)]
    if len(parts) >= 2:
        return f"{parts[1]} {parts[0]}".strip()
    return parts[0] if parts else ""
