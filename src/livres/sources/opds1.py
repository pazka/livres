"""Generic OPDS 1.x (Atom) adapter: most legal catalogues speak it, so a new OPDS source is config only.

config keys (sources/*.json):
  search_url    OpenSearch template with {searchTerms}; if missing it is discovered from root_url
  root_url      catalogue root, used to discover the search template (rel="search")
  harvest_url   first page of an acquisition feed to crawl (follows rel="next") for Mode.HARVEST
  max_pages     harvest page limit (default 200)
  languages     accepted dc:language values (default ["fr"]); entries without a language are kept
"""

from __future__ import annotations

import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Iterator

from ..domain import DRM_TYPES, Acquisition, Book, normalise_type
from .base import SourceAdapter, SourceError

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/terms/",
    "dc11": "http://purl.org/dc/elements/1.1/",
    "opds": "http://opds-spec.org/2010/catalog",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}
ACQ = "http://opds-spec.org/acquisition"
FREE_ACQ = {ACQ, ACQ + "/open-access"}
IMAGE_RELS = ("http://opds-spec.org/image", "http://opds-spec.org/cover", "x-stanza-cover-image")
THUMB_RELS = ("http://opds-spec.org/image/thumbnail", "http://opds-spec.org/thumbnail", "x-stanza-cover-image-thumbnail")
_TAGS = re.compile(r"<[^>]+>")
_OPTIONAL_PARAM = re.compile(r"\{[a-zA-Z:]+\?\}")


def parse_xml(body: bytes) -> ET.Element:
    head = body[:4096]
    if b"<!ENTITY" in head or b"<!entity" in head:
        raise SourceError("parse", "XML entity declarations are refused")
    try:
        return ET.fromstring(body)
    except ET.ParseError as e:
        raise SourceError("parse", f"invalid XML: {e}") from e


def strip_html(text: str) -> str:
    text = html.unescape(_TAGS.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def fill_template(template: str, query: str) -> str:
    url = template.replace("{searchTerms}", urllib.parse.quote(query.strip()))
    return _OPTIONAL_PARAM.sub("", url)


class Opds1Adapter(SourceAdapter):
    required_config = ()

    def __init__(self, spec, http):
        super().__init__(spec, http)
        self._search_template: str | None = spec.config.get("search_url")

    # -- live search ---------------------------------------------------------------------------
    def search(self, query: str, limit: int = 20) -> list[Book]:
        url = fill_template(self.search_template(), query)
        books: list[Book] = []
        pages = 0
        while url and len(books) < limit and pages < 3:
            try:
                feed, url = self._feed(url)
            except SourceError:
                if pages == 0:
                    raise
                break  # a later page failing keeps the results we already have
            books.extend(feed)
            pages += 1
        return books[:limit]

    def search_template(self) -> str:
        if self._search_template:
            return self._search_template
        root = self.spec.config.get("root_url")
        if not root:
            raise SourceError("config", "neither search_url nor root_url is configured")
        resp = self.fetch(root, accept="application/atom+xml")
        doc = parse_xml(resp.body)
        for link in doc.findall("atom:link", NS):
            if link.get("rel") != "search":
                continue
            href = urllib.parse.urljoin(resp.url, link.get("href", ""))
            ltype = link.get("type", "")
            if "{searchTerms}" in href:
                self._search_template = href
                return href
            if "opensearchdescription" in ltype:
                self._search_template = self._from_opensearch(href)
                return self._search_template
        raise SourceError("parse", "the catalogue root has no search link")

    def _from_opensearch(self, url: str) -> str:
        resp = self.fetch(url, accept="application/opensearchdescription+xml")
        doc = parse_xml(resp.body)
        candidates = []
        for node in doc.iter():
            if node.tag.rsplit("}", 1)[-1] == "Url" and "{searchTerms}" in node.get("template", ""):
                candidates.append((node.get("type", ""), urllib.parse.urljoin(resp.url, node.get("template"))))
        for wanted in ("opds-catalog", "atom+xml"):
            for ctype, template in candidates:
                if wanted in ctype:
                    return template
        raise SourceError("parse", "the OpenSearch description has no Atom template")

    # -- harvest -------------------------------------------------------------------------------
    def harvest(self) -> Iterator[Book]:
        url = self.spec.config.get("harvest_url")
        if not url:
            raise SourceError("config", "harvest_url is not configured")
        seen: set[str] = set()
        for _ in range(int(self.spec.config.get("max_pages", 200))):
            if not url or url in seen:
                return
            seen.add(url)
            books, url = self._feed(url)
            yield from books

    # -- parsing -------------------------------------------------------------------------------
    def _feed(self, url: str) -> tuple[list[Book], str | None]:
        resp = self.fetch(url, accept="application/atom+xml")
        return self.parse_feed(resp.body, resp.url)

    def parse_feed(self, body: bytes, base_url: str) -> tuple[list[Book], str | None]:
        doc = parse_xml(body)
        if doc.tag != f"{{{NS['atom']}}}feed":
            raise SourceError("parse", f"not an Atom feed (root element {doc.tag})")
        languages = set(self.spec.config.get("languages", ["fr"]))
        books = []
        for entry in doc.findall("atom:entry", NS):
            book = self._entry(entry, base_url, languages)
            if book is not None:
                books.append(book)
        next_url = None
        for link in doc.findall("atom:link", NS):
            if link.get("rel") == "next" and link.get("href"):
                next_url = urllib.parse.urljoin(base_url, link.get("href"))
        return books, next_url

    def _entry(self, entry: ET.Element, base: str, languages: set[str]) -> Book | None:
        lang = (_text(entry, "dc:language") or _text(entry, "dc11:language") or "").lower()[:2]
        if lang and languages and lang not in languages:
            return None
        acquisitions, cover, thumb, page = [], None, None, None
        for link in entry.findall("atom:link", NS):
            rel, ltype = link.get("rel", ""), normalise_type(link.get("type", ""))
            href = urllib.parse.urljoin(base, link.get("href", ""))
            if rel.startswith(ACQ):
                if rel in FREE_ACQ and _is_free(link) and ltype not in DRM_TYPES:
                    length = link.get("length")
                    acquisitions.append(Acquisition(href, ltype, int(length) if length and length.isdigit() else None))
            elif rel in IMAGE_RELS:
                cover = href
            elif rel in THUMB_RELS:
                thumb = href
            elif rel == "alternate" and "html" in ltype:
                page = href
        book = Book(
            identifier=f"{self.spec.id}:{_text(entry, 'atom:id') or page or _text(entry, 'atom:title')}",
            source_id=self.spec.id,
            title=_text(entry, "atom:title"),
            authors=tuple(n.text.strip() for n in entry.findall("atom:author/atom:name", NS) if n.text and n.text.strip()),
            language=lang or "fr",
            description=strip_html(_text(entry, "atom:summary") or _text(entry, "atom:content"))[:1000],
            cover=cover or thumb,
            page_url=page,
            acquisitions=tuple(acquisitions),
            issued=_text(entry, "dc:issued") or None,
        )
        return book if book.title and book.usable else None


def _text(node: ET.Element, path: str) -> str:
    found = node.find(path, NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def _is_free(link: ET.Element) -> bool:
    for price in link.findall("opds:price", NS):
        try:
            if float((price.text or "0").replace(",", ".")) > 0:
                return False
        except ValueError:
            return False
    for indirect in link.iter(f"{{{NS['opds']}}}indirectAcquisition"):
        if normalise_type(indirect.get("type", "")) in DRM_TYPES:
            return False
    return True
