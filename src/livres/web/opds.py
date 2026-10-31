"""OPDS 2.0 output for "Ma liseuse". The contract (shape, rels, ordering) is frozen by the golden files in
contract/ and tests/test_contract.py: changes must be additive; a breaking change means /opds/v3."""

from __future__ import annotations

import urllib.parse

from ..domain import Book, SearchPage, SourceSpec

OPDS_JSON = "application/opds+json"
OPEN_ACCESS = "http://opds-spec.org/acquisition/open-access"
DOWNLOAD_HOSTS_REL = "https://livres.hosh.it/rel/download-hosts"

SUGGESTIONS = (
    "Victor Hugo",
    "Jules Verne",
    "Alexandre Dumas",
    "Émile Zola",
    "Guy de Maupassant",
    "Honoré de Balzac",
    "George Sand",
    "Gustave Flaubert",
    "Jean de La Fontaine",
    "Comtesse de Ségur",
)


def _search_href(query: str, page: int = 1) -> str:
    href = "/opds/v2/search?query=" + urllib.parse.quote(query)
    return href + (f"&page={page}" if page > 1 else "")


def _common_links(base: str) -> list[dict]:
    return [
        {"rel": "start", "href": f"{base}/opds/v2/catalog.json", "type": OPDS_JSON},
        {"rel": "search", "href": f"{base}/opds/v2/search{{?query}}", "type": OPDS_JSON, "templated": True},
        {"rel": DOWNLOAD_HOSTS_REL, "href": f"{base}/api/v1/download-hosts", "type": "application/json"},
    ]


def catalog_feed(base: str) -> dict:
    return {
        "metadata": {"title": "Livres gratuits et légaux", "description": "Livres du domaine public et catalogues gratuits officiels, en français, sans DRM."},
        "links": [{"rel": "self", "href": f"{base}/opds/v2/catalog.json", "type": OPDS_JSON}, *_common_links(base)],
        "navigation": [
            {"href": base + _search_href(name), "title": name, "type": OPDS_JSON, "rel": "subsection"} for name in SUGGESTIONS
        ],
    }


def publication(book: Book, sources: dict[str, SourceSpec]) -> dict:
    spec = sources.get(book.source_id)
    metadata = {
        "@type": "http://schema.org/Book",
        "identifier": book.identifier,
        "title": book.title,
        "author": [{"name": a} for a in book.authors],
        "language": book.language or "fr",
        "belongsTo": {"collection": [{"name": spec.name if spec else book.source_id, "identifier": book.source_id}]},
    }
    if book.description:
        metadata["description"] = book.description
    if book.issued:
        metadata["published"] = book.issued
    links = []
    for acq in book.sorted_acquisitions():
        link = {"rel": OPEN_ACCESS, "href": acq.href, "type": acq.media_type}
        if acq.size:
            link["properties"] = {"size": acq.size}
        links.append(link)
    if book.page_url:
        links.append({"rel": "alternate", "href": book.page_url, "type": "text/html"})
    pub = {"metadata": metadata, "links": links}
    if book.cover:
        pub["images"] = [{"href": book.cover, "type": _image_type(book.cover)}]
    return pub


def search_feed(base: str, result: SearchPage, sources: dict[str, SourceSpec]) -> dict:
    links = [{"rel": "self", "href": base + _search_href(result.query, result.page), "type": OPDS_JSON}, *_common_links(base)]
    if result.has_next:
        links.append({"rel": "next", "href": base + _search_href(result.query, result.page + 1), "type": OPDS_JSON})
    if result.page > 1:
        links.append({"rel": "previous", "href": base + _search_href(result.query, result.page - 1), "type": OPDS_JSON})
    return {
        "metadata": {
            "title": f"Résultats pour « {result.query} »",
            "itemsPerPage": result.page_size,
            "currentPage": result.page,
        },
        "links": links,
        "publications": [publication(b, sources) for b in result.books],
    }


def _image_type(href: str) -> str:
    lower = href.lower().split("?", 1)[0]
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"
