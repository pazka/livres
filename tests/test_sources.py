"""Adapters against recorded fixtures: parsing, legality filters, discovery, paging."""

import unittest

from helpers import FakeHttp, fixture, spec

from livres.domain import EPUB, MOBI, PDF, Mode
from livres.http import FetchError
from livres.sources import SourceError
from livres.sources.gutenberg import GutenbergCsvAdapter, public_domain_in_france
from livres.sources.opds1 import Opds1Adapter, fill_template
from livres.sources.opds2 import Opds2Adapter, fill_json_template

EG = "https://www.ebooksgratuits.com"
EG_HOSTS = ("www.ebooksgratuits.com",)


class Opds1Tests(unittest.TestCase):
    def adapter(self, routes, config=None, hosts=EG_HOSTS):
        cfg = config or {"search_url": EG + "/opds/feed.php?mode=search&query={searchTerms}"}
        return Opds1Adapter(spec("ebooksgratuits", config=cfg, hosts=hosts), FakeHttp(routes))

    def test_search_parses_entries_and_orders_formats(self):
        a = self.adapter({EG + "/opds/feed.php?mode=search&query=Maupassant": fixture("ebooksgratuits/search.xml")})
        books = a.search("Maupassant", limit=3)
        self.assertEqual([b.title for b in books], ["Bel-Ami", "Boule de suif", "Une vie"])
        bel = books[0]
        self.assertEqual(bel.identifier, "ebooksgratuits:https://www.ebooksgratuits.com/details.php?book=101")
        self.assertEqual(bel.authors, ("Maupassant, Guy de",))
        self.assertEqual([x.media_type for x in bel.sorted_acquisitions()][:3], [EPUB, MOBI, PDF])
        self.assertEqual(bel.sorted_acquisitions()[0].size, 412345)
        self.assertEqual(bel.cover, EG + "/covers/101.jpg", "relative cover resolved against the feed URL")
        self.assertNotIn("<b>", bel.description)

    def test_other_languages_are_dropped(self):
        a = self.adapter({EG + "/opds/feed.php?mode=search&query=Maupassant": fixture("ebooksgratuits/search.xml")})
        titles = [b.title for b in a.search("Maupassant", limit=3)]
        self.assertNotIn("A Life (English translation)", titles)

    def test_follows_next_page_when_more_results_are_needed(self):
        page2 = fixture("ebooksgratuits/search.xml").replace(b"book=10", b"book=20").replace(b'rel="next"', b'rel="prev"')
        a = self.adapter({
            EG + "/opds/feed.php?mode=search&query=Maupassant": fixture("ebooksgratuits/search.xml"),
            EG + "/opds/feed.php?mode=search&query=Maupassant&page=1": page2,
        })
        self.assertEqual(len(a.search("Maupassant", limit=6)), 6)

    def test_discovers_search_template_through_opensearch(self):
        a = self.adapter(
            {
                EG + "/opds/index.php": fixture("ebooksgratuits/root.xml"),
                EG + "/opds/opensearch.xml": fixture("ebooksgratuits/opensearch.xml"),
                EG + "/opds/feed.php?mode=search&query=Maupassant": fixture("ebooksgratuits/search.xml"),
            },
            config={"root_url": EG + "/opds/index.php"},
        )
        self.assertEqual(a.search_template(), EG + "/opds/feed.php?mode=search&query={searchTerms}")
        self.assertEqual(len(a.search("Maupassant")), 3)

    def test_paid_drm_and_borrowed_books_are_never_offered(self):
        a = Opds1Adapter(spec("mixed", config={"harvest_url": "https://books.example/feed"}, modes=(Mode.HARVEST,)), FakeHttp())
        books, _ = a.parse_feed(fixture("generic/mixed_store.xml"), "https://books.example/feed")
        self.assertEqual([b.title for b in books], ["Le Comte de Monte-Cristo"])
        self.assertEqual(books[0].cover, "https://books.example/thumbs/1.png")

    def test_harvest_stops_on_loops_and_page_limit(self):
        body = fixture("wikisource/Bon_pour_export.xml").replace(
            b'<link rel="self"', b'<link rel="next" href="https://ws-export.wmcloud.org/opds/fr/Bon_pour_export.xml"/><link rel="self"'
        )
        url = "https://ws-export.wmcloud.org/opds/fr/Bon_pour_export.xml"
        a = Opds1Adapter(spec("wikisource", config={"harvest_url": url}, modes=(Mode.HARVEST,), hosts=("ws-export.wmcloud.org",)), FakeHttp({url: body}))
        self.assertEqual(len(list(a.harvest())), 4)

    def test_entity_declarations_are_refused(self):
        a = self.adapter({EG + "/opds/feed.php?mode=search&query=x": b'<?xml version="1.0"?><!DOCTYPE f [<!ENTITY a "aaaa">]><feed/>'})
        with self.assertRaises(SourceError) as ctx:
            a.search("x")
        self.assertEqual(ctx.exception.kind, "parse")

    def test_html_error_page_is_a_parse_error(self):
        a = self.adapter({EG + "/opds/feed.php?mode=search&query=x": b"<html><body>Maintenance</body></html>"})
        with self.assertRaises(SourceError) as ctx:
            a.search("x")
        self.assertEqual(ctx.exception.kind, "parse")

    def test_robots_block_surfaces_as_robots_error(self):
        a = self.adapter({EG + "/opds/feed.php?mode=search&query=x": FetchError("robots", "disallowed")})
        with self.assertRaises(SourceError) as ctx:
            a.search("x")
        self.assertEqual(ctx.exception.kind, "robots")

    def test_template_quotes_query_and_drops_optional_params(self):
        self.assertEqual(fill_template("https://x/s?q={searchTerms}&n={count?}", "les misérables"), "https://x/s?q=les%20mis%C3%A9rables&n=")


class Opds2Tests(unittest.TestCase):
    def test_parses_publications_and_filters_paid_drm_language(self):
        a = Opds2Adapter(spec("o2", kind="opds2", config={"search_url": "https://books.example/search{?query}"}), FakeHttp())
        books, nxt = a.parse_feed(fixture("generic/opds2_search.json"), "https://books.example/search?query=verne")
        self.assertEqual([b.title for b in books], ["Vingt mille lieues sous les mers", "Voyage au centre de la Terre"])
        self.assertEqual(books[0].identifier, "o2:urn:isbn:9780000000001")
        self.assertEqual(books[0].acquisitions[0].size, 1234567)
        self.assertEqual(books[0].cover, "https://books.example/c/1.jpg")
        self.assertEqual(books[1].authors, ("Jules Verne",))
        self.assertEqual(nxt, "https://books.example/search?query=verne&page=2")

    def test_json_template(self):
        self.assertEqual(fill_json_template("https://x/s{?query}", "a b"), "https://x/s?query=a%20b")
        self.assertEqual(fill_json_template("https://x/s?lang=fr{?query}", "a"), "https://x/s?lang=fr&query=a")

    def test_invalid_json(self):
        a = Opds2Adapter(spec("o2", kind="opds2"), FakeHttp())
        with self.assertRaises(SourceError):
            a.parse_feed(b"<html>", "https://books.example/")


class GutenbergTests(unittest.TestCase):
    def adapter(self, pd=True):
        cfg = {
            "catalog_url": "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv",
            "languages": ["fr"],
            "france_public_domain": pd,
            "acquisitions": [
                {"href": "https://www.gutenberg.org/ebooks/{id}.epub3.images", "type": EPUB},
                {"href": "https://www.gutenberg.org/ebooks/{id}.kindle.images", "type": MOBI},
            ],
            "page_url": "https://www.gutenberg.org/ebooks/{id}",
        }
        url = cfg["catalog_url"]
        return GutenbergCsvAdapter(spec("gutenberg", kind="gutenberg_csv", modes=(Mode.HARVEST,), config=cfg, hosts=("www.gutenberg.org",)), FakeHttp({url: fixture("gutenberg/pg_catalog.csv")}))

    def test_harvest_keeps_french_texts_in_french_public_domain(self):
        books = {b.identifier: b for b in self.adapter().harvest()}
        self.assertEqual(set(books), {"gutenberg:135", "gutenberg:17489", "gutenberg:2650", "gutenberg:99905"})
        hugo = books["gutenberg:135"]
        self.assertEqual(hugo.authors, ("Victor Hugo",))
        self.assertEqual(hugo.acquisitions[0].href, "https://www.gutenberg.org/ebooks/135.epub3.images")
        self.assertEqual(hugo.page_url, "https://www.gutenberg.org/ebooks/135")

    def test_without_french_rule_us_public_domain_books_are_kept(self):
        ids = {b.identifier for b in self.adapter(pd=False).harvest()}
        self.assertIn("gutenberg:99901", ids)
        self.assertIn("gutenberg:99904", ids)

    def test_french_public_domain_rule(self):
        self.assertTrue(public_domain_in_france("Hugo, Victor, 1802-1885", 2026))
        self.assertTrue(public_domain_in_france("Colette, 1873-1954", 2026))
        self.assertFalse(public_domain_in_france("Colette, 1873-1954", 2024))
        self.assertFalse(public_domain_in_france("Camus, Albert, 1913-1960", 2026))
        self.assertFalse(public_domain_in_france("Grimm, Jacob, 1785-1863; Doe, Jane, 1960- [Translator]", 2026))
        self.assertFalse(public_domain_in_france("Someone, Unknown Dates", 2026))
        self.assertTrue(public_domain_in_france("Anonymous", 2026))

    def test_morts_pour_la_france_get_thirty_more_years(self):
        self.assertFalse(public_domain_in_france("Saint-Exupéry, Antoine de, 1900-1944", 2026), "PD in France in 2045")
        self.assertTrue(public_domain_in_france("Saint-Exupéry, Antoine de, 1900-1944", 2045))
        self.assertTrue(public_domain_in_france("Pergaud, Louis, 1882-1915", 2026), "1915 + 70 + 30 is past")
        self.assertTrue(public_domain_in_france("Giraudoux, Jean, 1882-1944", 2026), "not mort pour la France")
        self.assertTrue(public_domain_in_france("Homer, 751? BCE-651? BCE", 2026))

    def test_unexpected_header_is_a_parse_error(self):
        with self.assertRaises(SourceError):
            list(self.adapter().parse_catalog("a,b\n1,2\n"))


if __name__ == "__main__":
    unittest.main()
