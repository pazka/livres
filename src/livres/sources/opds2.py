"""Generic OPDS 2.0 (JSON) adapter, for catalogues that moved to the JSON flavour.

config keys:
  search_url    URI template with {searchTerms} or {?query} (RFC 6570 level 1 form is enough)
  harvest_url   first page of a publications feed (follows rel="next") for Mode.HARVEST
  max_pages     harvest page limit (default 200)
  languages     accepted metadata.language values (default ["fr"])
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Iterator

from ..domain import DRM_TYPES, Acquisition, Book, normalise_type
from .base import SourceAdapter, SourceError
from .opds1 import FREE_ACQ, strip_html


def fill_json_template(template: str, query: str) -> str:
    q = urllib.parse.quote(query.strip())
    if "{?query}" in template:
        sep = "&" if "?" in template.split("{?query}")[0] else "?"
        return template.replace("{?query}", f"{sep}query={q}")
    return template.replace("{searchTerms}", q).replace("{query}", q)


class Opds2Adapter(SourceAdapter):
    def search(self, query: str, limit: int = 20) -> list[Book]:
        template = self.spec.config.get("search_url")
        if not template:
            raise SourceError("config", "search_url is not configured")
        url: str | None = fill_json_template(template, query)
        books: list[Book] = []
        pages = 0
        while url and len(books) < limit and pages < 3:
            try:
                page, url = self._page(url)
            except SourceError:
                if pages == 0:
                    raise
                break
            books.extend(page)
            pages += 1
        return books[:limit]

    def harvest(self) -> Iterator[Book]:
        url = self.spec.config.get("harvest_url")
        if not url:
            raise SourceError("config", "harvest_url is not configured")
        seen: set[str] = set()
        for _ in range(int(self.spec.config.get("max_pages", 200))):
            if not url or url in seen:
                return
            seen.add(url)
            books, url = self._page(url)
            yield from books

    def _page(self, url: str):
        resp = self.fetch(url, accept="application/opds+json")
        return self.parse_feed(resp.body, resp.url)

    def parse_feed(self, body: bytes, base_url: str) -> tuple[list[Book], str | None]:
        try:
            doc = json.loads(body)
        except ValueError as e:
            raise SourceError("parse", f"invalid JSON: {e}") from e
        if not isinstance(doc, dict):
            raise SourceError("parse", "an OPDS 2 feed must be a JSON object")
        pubs = list(doc.get("publications") or [])
        for group in doc.get("groups") or []:
            pubs.extend(group.get("publications") or [])
        languages = set(self.spec.config.get("languages", ["fr"]))
        books = [b for b in (self._publication(p, base_url, languages) for p in pubs if isinstance(p, dict)) if b]
        next_url = None
        for link in doc.get("links") or []:
            if _has_rel(link, "next") and link.get("href"):
                next_url = urllib.parse.urljoin(base_url, link["href"])
        return books, next_url

    def _publication(self, pub: dict, base: str, languages: set[str]) -> Book | None:
        meta = pub.get("metadata") or {}
        lang = meta.get("language") or ""
        lang = (lang[0] if isinstance(lang, list) and lang else lang if isinstance(lang, str) else "").lower()[:2]
        if lang and languages and lang not in languages:
            return None
        acquisitions, page = [], None
        for link in pub.get("links") or []:
            ltype = normalise_type(link.get("type", ""))
            href = urllib.parse.urljoin(base, link.get("href", ""))
            rels = link.get("rel") if isinstance(link.get("rel"), list) else [link.get("rel", "")]
            props = link.get("properties") or {}
            if any(r in FREE_ACQ for r in rels):
                indirect = {normalise_type(i.get("type", "")) for i in props.get("indirectAcquisition") or []}
                price = (props.get("price") or {}).get("value", 0)
                if ltype in DRM_TYPES or indirect & DRM_TYPES or (price or 0) > 0:
                    continue
                size = props.get("size")
                acquisitions.append(Acquisition(href, ltype, size if isinstance(size, int) else None))
            elif "alternate" in rels and "html" in ltype:
                page = href
        cover = None
        for image in pub.get("images") or []:
            if image.get("href"):
                cover = urllib.parse.urljoin(base, image["href"])
                break
        authors = meta.get("author") or []
        if isinstance(authors, (str, dict)):
            authors = [authors]
        names = tuple(
            (a.get("name") if isinstance(a, dict) else a).strip()
            for a in authors
            if (a.get("name") if isinstance(a, dict) else a)
        )
        title = meta.get("title")
        if isinstance(title, dict):  # language map
            title = title.get("fr") or next(iter(title.values()), "")
        book = Book(
            identifier=f"{self.spec.id}:{meta.get('identifier') or page or title}",
            source_id=self.spec.id,
            title=(title or "").strip(),
            authors=names,
            language=lang or "fr",
            description=strip_html(meta.get("description") or "")[:1000],
            cover=cover,
            page_url=page,
            acquisitions=tuple(acquisitions),
            issued=meta.get("published"),
        )
        return book if book.title and book.usable else None


def _has_rel(link: dict, rel: str) -> bool:
    value = link.get("rel")
    return rel in value if isinstance(value, list) else value == rel
