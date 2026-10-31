"""Bibliothèque électronique du Québec (beq.ebooksgratuits.com): static HTML catalogue pages, harvested.

BEQ publishes no OPDS feed or catalogue file, so its collection pages are parsed (the owner allowed it,
AGENTS.md ground rule 3, decision of 2026-10-31). The pages are hand-made FrontPage HTML that changes
rarely: an author heading in bold ("Achard, Amédée :"), then one paragraph per book holding the
download links (EPUB, MOBI, PDF) followed by the title as plain text.

**French public domain.** BEQ follows Canadian law (life + 50 years for authors who died before 1972),
so some of its books are still protected in France (life + 70). Its pages carry no dates. A book is kept
only when its author is identified AND known (from Project Gutenberg's official catalogue, which lists
lifespans) to have died at least 71 years ago AND to write in French (an original French text has no
translator whose rights could still run). Anything else (anthologies, unknown authors, translations)
is left out. Conservative on purpose: a missing book is better than an illegal one.

config keys:
  pages          [url | {"url": ..., "author": "Nom, Prénom"}]  collection or author pages to parse;
                 "author" applies to entries of a page that has no bold author heading
  lifespans_url  Project Gutenberg pg_catalog.csv (author lifespans)
  max_pages      safety limit on `pages` (default 40)
"""

from __future__ import annotations

import collections
import csv
import datetime
import html.parser
import io
import re
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass, field

from ..domain import Acquisition, Book, display_author, fold
from .base import SourceAdapter, SourceError
from .gutenberg import public_domain_in_france

FORMATS = {
    ".epub": "application/epub+zip",
    ".mobi": "application/x-mobipocket-ebook",
    ".pdf": "application/pdf",
}
_HEADING = re.compile(r"^\s*(?P<name>[^:]{3,120}?)\s*:\s*(voir\b.*)?$", re.IGNORECASE)
_HEART = "♥"


@dataclass
class Entry:
    author: str | None
    links: dict[str, str] = field(default_factory=dict)  # media type -> absolute URL
    text: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        t = " ".join(" ".join(self.text).replace(_HEART, " ").replace("\xa0", " ").split())
        return t.strip(" .;,-").strip()


class _PageParser(html.parser.HTMLParser):
    """Turns one BEQ page into Entries. Tolerant: unknown markup is ignored, never fatal."""

    def __init__(self, base_url: str, default_author: str | None):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.author = default_author
        self.entries: list[Entry] = []
        self._entry: Entry | None = None
        self._in_bold = 0
        self._bold_text: list[str] = []
        self._bold_carry = ""  # FrontPage splits headings: <b>Ba</b><b>zin, René :</b>
        self._link: str | None = None
        self._link_has_img = False

    # -- structure ------------------------------------------------------------------------------
    def _flush(self):
        e = self._entry
        if e is not None and e.links:
            self.entries.append(e)
        self._entry = None

    def handle_starttag(self, tag, attrs):
        if tag in ("p", "div", "td", "tr", "li", "table"):
            self._flush()
            self._bold_carry = ""
        if tag == "b" or tag == "strong":
            self._in_bold += 1
            if self._in_bold == 1:
                self._bold_text = []
        elif tag == "a":
            self._link = dict(attrs).get("href")
            self._link_has_img = False
        elif tag == "img" and self._link is not None:
            self._link_has_img = True

    def handle_endtag(self, tag):
        if tag in ("b", "strong") and self._in_bold:
            self._in_bold -= 1
            if self._in_bold == 0:
                text = " ".join("".join(self._bold_text).split())
                if self._heading(self._bold_carry + text):
                    self._bold_carry = ""
                else:
                    self._bold_carry += text
        elif tag == "a" and self._link is not None:
            self._add_link(self._link, self._link_has_img)
            self._link = None
        elif tag == "body":
            self._flush()

    def handle_data(self, data):
        if self._in_bold:
            self._bold_text.append(data)
        elif data.strip():
            self._bold_carry = ""
        if self._link is not None:
            return  # link captions are icons or blanks, never the title
        if self._entry is not None and self._entry.links and not self._in_bold:
            self._entry.text.append(data)

    def close(self):
        super().close()
        self._flush()

    # -- content --------------------------------------------------------------------------------
    def _heading(self, text: str) -> bool:
        m = _HEADING.match(text)
        if m and "," in m.group("name") and not any(ch.isdigit() for ch in m.group("name")):
            self._flush()
            self.author = m.group("name").strip()
            return True
        return False

    def _add_link(self, href: str | None, has_img: bool):
        if not href or not has_img:
            return  # BEQ puts empty spacer links between icons
        url = urllib.parse.urljoin(self.base_url, href)
        path = urllib.parse.urlsplit(url).path
        ext = path[path.rfind("."):].lower() if "." in path else ""
        media = FORMATS.get(ext)
        if media is None:
            return
        if media == "application/pdf" and "-xpdf/" not in path:
            return  # the large-screen PDF; keep only the e-reader one
        if self._entry is None:
            self._entry = Entry(author=self.author)
        self._entry.links.setdefault(media, url)


def parse_page(body: bytes, base_url: str, default_author: str | None = None) -> list[Entry]:
    text = _decode(body)
    p = _PageParser(base_url, default_author)
    try:
        p.feed(text)
        p.close()
    except Exception as e:  # html.parser is lenient; anything else is a real surprise
        raise SourceError("parse", f"unreadable page {base_url}: {type(e).__name__}") from e
    return p.entries


def _decode(body: bytes) -> str:
    head = body[:2048].decode("ascii", errors="ignore").lower()
    m = re.search(r"charset=([a-z0-9_\-]+)", head)
    enc = m.group(1) if m else "windows-1252"
    if enc in ("iso-8859-1", "latin-1", "latin1"):
        enc = "windows-1252"  # what FrontPage really writes
    try:
        return body.decode(enc, errors="replace")
    except LookupError:
        return body.decode("windows-1252", errors="replace")


# -- who is in the French public domain ---------------------------------------------------------
def author_key(name: str) -> str:
    """'Achard, Amédée' / 'Achard, Amédée, 1814-1875' -> 'achard amedee'."""
    parts = [p for p in (name or "").split(",") if p.strip() and not re.search(r"\d", p)]
    return fold(",".join(parts[:2]))


_ROMANCE = ("it", "es", "pt", "ca", "la", "ro")  # other languages Library of Congress files under PQ


def french_public_domain_authors(catalog_csv: str, this_year: int | None = None) -> set[str]:
    """Author keys from Gutenberg's catalogue who (a) write in French and (b) died at least 71 years ago
    (same rule as the Gutenberg source, incl. "morts pour la France").

    "Writes in French" = at least one of their books is in French, credits no translator AND is filed by
    the Library of Congress under PQ (French literature), and they have at least as many French books as
    Italian/Spanish/Portuguese/Latin ones (PQ also covers those). This rules out Gutenberg's uncredited
    French translations of Conan Doyle (PR), Wilde (PR) or Sacher-Masoch (PT), and Dante (Italian)."""
    reader = csv.DictReader(io.StringIO(catalog_csv))
    if not reader.fieldnames or not {"Language", "Authors"}.issubset(reader.fieldnames):
        raise SourceError("parse", "lifespans: unexpected Gutenberg CSV header")
    year = this_year or datetime.date.today().year
    languages: dict[str, collections.Counter] = {}
    french_writer: set[str] = set()
    eligible: set[str] = set()
    for row in reader:
        langs = {c.strip() for c in (row.get("Language") or "").split(";") if c.strip()}
        people = [a.strip() for a in (row.get("Authors") or "").split(";") if a.strip()]
        credits_translator = any("[" in a for a in people)
        french_lit = "PQ" in {c.strip()[:2] for c in (row.get("LoCC") or "").split(";")}
        for person in people:
            if "[" in person:
                continue  # translators, editors, illustrators: not the author
            key = author_key(person)
            if not key:
                continue
            languages.setdefault(key, collections.Counter()).update(langs)
            if "fr" in langs and french_lit and not credits_translator:
                french_writer.add(key)
            if public_domain_in_france(person, year):
                eligible.add(key)
    keep = set()
    for key in eligible & french_writer:
        counts = languages[key]
        if counts.get("fr", 0) >= max((counts.get(lang, 0) for lang in _ROMANCE), default=0):
            keep.add(key)
    return keep


_PARTICLES = {"de", "d", "du", "des", "la", "le", "van", "von", "di", "da"}


def surname(author: str) -> str:
    """'Abrantès, Laure Junot, duchesse d\'' -> 'abrantes'; 'La Fontaine, Jean de' -> 'la fontaine'."""
    return fold((author or "").split(",", 1)[0])


def file_matches_author(entry: Entry, author: str) -> bool:
    """BEQ names its files after the author ('Aimard-Michigan.epub'). Requiring the author's surname in
    every EPUB/MOBI file name stops a misplaced heading from attributing books to the wrong person."""
    name = surname(author)
    words = [w for w in name.split() if w not in _PARTICLES] or name.split()
    if not words:
        return False
    for media in ("application/epub+zip", "application/x-mobipocket-ebook"):
        href = entry.links.get(media)
        if href is None:
            continue
        base = fold(urllib.parse.unquote(urllib.parse.urlsplit(href).path.rsplit("/", 1)[-1]).replace("_", " "))
        if not all(w in base for w in words):
            return False
    return True


class BeqHtmlAdapter(SourceAdapter):
    required_config = ("pages", "lifespans_url")

    def harvest(self) -> Iterator[Book]:
        cfg = self.spec.config
        pages = list(cfg["pages"])[: int(cfg.get("max_pages", 40))]
        lifespans = self.fetch(cfg["lifespans_url"], max_bytes=120_000_000, accept="text/csv")
        allowed = french_public_domain_authors(lifespans.body.decode("utf-8", errors="replace"))
        if not allowed:
            raise SourceError("parse", "lifespans: no author found in the Gutenberg catalogue")
        seen: set[str] = set()
        found = 0
        for page in pages:
            url, author = (page, None) if isinstance(page, str) else (page["url"], page.get("author"))
            resp = self.fetch(url, max_bytes=5_000_000, accept="text/html")
            entries = parse_page(resp.body, resp.url or url, author)
            found += len(entries)
            for book in self.to_books(entries, allowed):
                if book.identifier not in seen:
                    seen.add(book.identifier)
                    yield book
        if found == 0:
            raise SourceError("parse", "no book link found on any BEQ page: the layout changed")

    def to_books(self, entries: list[Entry], allowed: set[str]) -> Iterator[Book]:
        for e in entries:
            if not e.author or author_key(e.author) not in allowed:
                continue
            if not file_matches_author(e, e.author):
                continue  # heading and file disagree (tables, misplaced headings): never guess
            if not ({"application/epub+zip", "application/x-mobipocket-ebook"} & e.links.keys()):
                continue  # Mom's Kindle needs EPUB or MOBI; PDF-only entries are skipped
            title = e.title
            low = title.lower()
            if not title or len(title) > 300 or ", par " in low or "trad." in low or "traduction" in low:
                continue
            main = e.links.get("application/epub+zip") or e.links["application/x-mobipocket-ebook"]
            path = urllib.parse.urlsplit(main).path.strip("/")
            stable = path.rsplit(".", 1)[0]
            yield Book(
                identifier=f"{self.spec.id}:{stable}",
                source_id=self.spec.id,
                title=title,
                authors=(display_author(e.author),),
                language="fr",
                page_url=None,
                acquisitions=tuple(Acquisition(href, media) for media, href in e.links.items()),
            )
