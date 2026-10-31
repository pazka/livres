"""Golden contract with the "Ma liseuse" app (copied into the app's own tests).

If this test fails, you changed what the app receives. Allowed: ADDING fields. Not allowed: removing or
renaming a field, changing a rel, a media type, the ordering of acquisition links, or an error string's
meaning. Breaking changes go to /opds/v3. To accept an additive change: LIVRES_UPDATE_CONTRACT=1, re-run,
review the diff of contract/, and tell the app (AGENTS.md P5).
"""

import json
import os
import unittest

from helpers import ROOT, book, spec

from livres.bookrequests import RequestError, parse_request
from livres.domain import Acquisition, Book, SearchPage
from livres.web import opds

CONTRACT = ROOT / "contract"
BASE = "https://livres.hosh.it"


def sample_books():
    return [
        Book(
            identifier="ebooksgratuits:https://www.ebooksgratuits.com/details.php?book=101",
            source_id="ebooksgratuits",
            title="Bel-Ami",
            authors=("Guy de Maupassant",),
            description="Georges Duroy, ancien sous-officier, fait carrière dans le journalisme parisien.",
            cover="https://www.ebooksgratuits.com/covers/101.jpg",
            page_url="https://www.ebooksgratuits.com/details.php?book=101",
            acquisitions=(
                Acquisition("https://www.ebooksgratuits.com/newsendbook.php?id=101&format=pdf", "application/pdf"),
                Acquisition("https://www.ebooksgratuits.com/newsendbook.php?id=101&format=epub", "application/epub+zip", 412345),
                Acquisition("https://www.ebooksgratuits.com/newsendbook.php?id=101&format=mp", "application/x-mobipocket-ebook"),
            ),
        ),
        Book(
            identifier="gutenberg:135",
            source_id="gutenberg",
            title="Les Misérables",
            authors=("Victor Hugo",),
            page_url="https://www.gutenberg.org/ebooks/135",
            cover="https://www.gutenberg.org/cache/epub/135/pg135.cover.medium.jpg",
            acquisitions=(
                Acquisition("https://www.gutenberg.org/ebooks/135.epub3.images", "application/epub+zip"),
                Acquisition("https://www.gutenberg.org/ebooks/135.kindle.images", "application/x-mobipocket-ebook"),
            ),
            issued="1994-04-01",
        ),
    ]


def sources():
    return {
        "ebooksgratuits": spec("ebooksgratuits", name="Ebooks libres et gratuits"),
        "gutenberg": spec("gutenberg", name="Project Gutenberg"),
    }


def request_error(payload) -> dict:
    try:
        parse_request(payload)
    except RequestError as e:
        return {"error": str(e)}
    raise AssertionError("expected an error")


GOLDEN = {
    "catalog.json": lambda: opds.catalog_feed(BASE),
    "search-page1.json": lambda: opds.search_feed(BASE, SearchPage("maupassant", 1, 2, sample_books(), True), sources()),
    "search-page2.json": lambda: opds.search_feed(BASE, SearchPage("maupassant", 2, 2, [], False), sources()),
    "request-202.json": lambda: {"id": 42, "status": "sent"},
    "request-400-missing-title.json": lambda: request_error({"author": "Hugo"}),
    "request-400-bad-email.json": lambda: request_error({"title": "Germinal", "contact": "maman"}),
}


class ContractTests(unittest.TestCase):
    def test_golden_files(self):
        update = os.environ.get("LIVRES_UPDATE_CONTRACT") == "1"
        for name, make in GOLDEN.items():
            path = CONTRACT / name
            produced = json.dumps(make(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            if update:
                path.write_text(produced, encoding="utf-8")
                continue
            self.assertTrue(path.exists(), f"contract/{name} is missing: run with LIVRES_UPDATE_CONTRACT=1")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), json.loads(produced), f"contract/{name} changed (see module docstring)")

    def test_acquisition_order_is_epub_mobi_pdf(self):
        pub = opds.publication(sample_books()[0], sources())
        types = [l["type"] for l in pub["links"] if l["rel"] == opds.OPEN_ACCESS]
        self.assertEqual(types, ["application/epub+zip", "application/x-mobipocket-ebook", "application/pdf"])

    def test_identifier_is_stable_and_namespaced(self):
        pub = opds.publication(book("gutenberg:135", source_id="gutenberg"), sources())
        self.assertEqual(pub["metadata"]["identifier"], "gutenberg:135")


if __name__ == "__main__":
    unittest.main()
