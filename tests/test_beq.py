"""BEQ adapter (static HTML catalogue) against pages recorded on 2026-10-31."""

import unittest

from helpers import FakeHttp, fixture, spec

from livres.domain import EPUB, MOBI, PDF, Mode
from livres.sources import SourceError
from livres.sources.beq import BeqHtmlAdapter, author_key, file_matches_author, french_public_domain_authors, parse_page

BEQ = "https://beq.ebooksgratuits.com"
VENTS = BEQ + "/vents/index.htm"
CLASSIQUES = BEQ + "/classiques/index.htm"
LIFESPANS = "https://www.gutenberg.org/cache/epub/feeds/pg_catalog.csv"
HOSTS = ("beq.ebooksgratuits.com", "www.gutenberg.org")


def adapter(routes, pages=(VENTS, CLASSIQUES), strict=True):
    cfg = {"pages": list(pages), "lifespans_url": LIFESPANS, "france_public_domain": strict}
    return BeqHtmlAdapter(spec("beq", kind="beq_html", modes=(Mode.HARVEST,), config=cfg, hosts=HOSTS), FakeHttp(routes))


ROUTES = {
    VENTS: lambda: fixture("beq/vents.htm"),
    CLASSIQUES: lambda: fixture("beq/classiques.htm"),
    LIFESPANS: lambda: fixture("beq/pg_lifespans.csv"),
}


def routes():
    return {k: v() for k, v in ROUTES.items()}


class ParseTests(unittest.TestCase):
    def test_entries_carry_author_heading_title_and_links(self):
        entries = parse_page(fixture("beq/vents.htm"), VENTS)
        rose = next(e for e in entries if e.title == "Madame Rose")
        self.assertEqual(rose.author, "Achard, Amédée")
        self.assertEqual(rose.links[EPUB], BEQ + "/vents-epub/Achard_Madame_Rose.epub")
        self.assertEqual(rose.links[PDF], BEQ + "/vents-xpdf/Achard-Madame.pdf", "the e-reader PDF, not the large one")
        self.assertNotIn(".doc", " ".join(rose.links.values()))

    def test_split_bold_headings_are_joined(self):
        # FrontPage writes <b>Al</b><b>cott, Louisa May :</b>
        authors = {e.author for e in parse_page(fixture("beq/vents.htm"), VENTS)}
        self.assertIn("Alcott, Louisa May", authors)
        self.assertNotIn("cott, Louisa May", authors)

    def test_mobi_link_is_kept(self):
        entries = parse_page(fixture("beq/vents.htm"), VENTS)
        self.assertTrue(any(MOBI in e.links for e in entries))

    def test_spacer_links_and_hearts_are_not_titles(self):
        for e in parse_page(fixture("beq/vents.htm"), VENTS):
            self.assertNotIn("medan.pdf", " ".join(e.links.values()))
            self.assertNotIn("♥", e.title)

    def test_garbage_gives_no_entries(self):
        self.assertEqual(parse_page(b"<html><body><p>Maintenance</p></body></html>", VENTS), [])


class LegalFilterTests(unittest.TestCase):
    def setUp(self):
        self.allowed = french_public_domain_authors(fixture("beq/pg_lifespans.csv").decode(), this_year=2026)

    def test_french_authors_dead_71_years_are_kept(self):
        for name in ("Aimard, Gustave", "Achard, Amédée", "Audoux, Marguerite", "Alanic, Mathilde"):
            self.assertIn(author_key(name), self.allowed, name)

    def test_recently_dead_alive_or_translated_authors_are_refused(self):
        self.assertNotIn(author_key("Giono, Jean"), self.allowed, "died 1970: protected in France until 2041")
        self.assertNotIn(author_key("Ages, Lucie des"), self.allowed, "no death year")
        self.assertNotIn(author_key("Alcott, Louisa May"), self.allowed, "English author: BEQ text is a translation")
        self.assertNotIn(author_key("Doyle, Arthur Conan"), self.allowed, "uncredited French translation on Gutenberg")
        self.assertNotIn(author_key("Saint-Exupéry, Antoine de"), self.allowed, "mort pour la France: +30 years")
        self.assertNotIn(author_key("Dante Alighieri"), self.allowed, "Italian author filed under PQ too")

    def test_french_author_widely_translated_into_english_is_kept(self):
        self.assertIn(author_key("Aimard, Gustave"), self.allowed, "more English than French books on Gutenberg")

    def test_file_name_must_match_the_heading(self):
        entries = parse_page(fixture("beq/classiques.htm"), CLASSIQUES)
        for e in entries:
            if e.author and file_matches_author(e, e.author):
                surname = author_key(e.author).split()[0]
                self.assertIn(surname, e.links.get(EPUB, e.links.get(MOBI, "")).lower())

    def test_bad_csv_is_a_parse_error(self):
        with self.assertRaises(SourceError) as ctx:
            french_public_domain_authors("a,b\n1,2\n")
        self.assertEqual(ctx.exception.kind, "parse")


class FullLibraryTests(unittest.TestCase):
    """Default since 2026-10-31 (owner): every BEQ book, no legal filter."""

    def setUp(self):
        r = routes()
        del r[LIFESPANS]  # the full library never downloads the lifespans file
        self.books = {b.identifier: b for b in adapter(r, strict=False).harvest()}

    def test_everything_with_a_title_and_a_download_is_kept(self):
        authors = {a for b in self.books.values() for a in b.authors}
        self.assertIn("Louisa May Alcott", authors)
        self.assertIn("Gustave Aimard", authors)
        self.assertTrue(all(b.title and b.usable for b in self.books.values()))

    def test_anthology_keeps_its_authors_from_the_title(self):
        medan = self.books["beq:vents-epub/Les_soirees_de_Medan"]
        self.assertEqual(medan.title, "Les Soirées de Médan")
        self.assertIn("Zola", medan.authors[0])

    def test_misattributed_heading_is_dropped_not_guessed(self):
        for b in self.books.values():
            for a in b.authors:
                if a == "Mathilde Alanic":
                    self.assertIn("alanic", b.identifier.lower())

    def test_more_books_than_the_filtered_library(self):
        filtered = list(adapter(routes(), strict=True).harvest())
        self.assertGreater(len(self.books), len(filtered))


class HarvestTests(unittest.TestCase):
    def test_harvest_keeps_only_french_public_domain_books(self):
        books = {b.identifier: b for b in adapter(routes()).harvest()}
        authors = {b.authors[0] for b in books.values()}
        self.assertIn("Gustave Aimard", authors)
        self.assertIn("Amédée Achard", authors)
        self.assertNotIn("Louisa May Alcott", authors)
        self.assertIn("Mathilde Alanic", authors, "died 1948: French public domain since 2019")
        rose = books["beq:vents-epub/Achard_Madame_Rose"]
        self.assertEqual(rose.title, "Madame Rose")
        self.assertEqual(rose.sorted_acquisitions()[0].media_type, EPUB)
        for b in books.values():
            self.assertTrue(b.usable)
            self.assertTrue(any(a.media_type in (EPUB, MOBI) for a in b.acquisitions), b.identifier)
            self.assertNotIn(", par ", b.title.lower())

    def test_author_dead_less_than_71_years_is_left_out(self):
        page = (b'<html><body><p><b>Giono, Jean :</b><p><a href="../vents-epub/Giono-Regain.epub">'
                b'<img src="e.gif"></a> Regain.</body></html>')
        parsed = parse_page(page, VENTS)
        self.assertEqual([(e.author, e.title) for e in parsed], [("Giono, Jean", "Regain")], "the page itself parses")
        r = routes()
        r[VENTS] = page
        titles = {b.title for b in adapter(r).harvest()}
        self.assertNotIn("Regain", titles)
        self.assertTrue(titles, "the other page still yields books")

    def test_anthologies_without_author_are_left_out(self):
        titles = {b.title for b in adapter(routes()).harvest()}
        self.assertFalse(any("Soirées de Médan" in t for t in titles))

    def test_identifiers_are_stable_and_unique(self):
        a = [b.identifier for b in adapter(routes()).harvest()]
        b = [b.identifier for b in adapter(routes()).harvest()]
        self.assertEqual(a, b)
        self.assertEqual(len(a), len(set(a)))

    def test_changed_layout_is_reported(self):
        r = routes()
        r[VENTS] = b"<html><body><p>Nouveau site</p></body></html>"
        r[CLASSIQUES] = r[VENTS]
        with self.assertRaises(SourceError) as ctx:
            list(adapter(r).harvest())
        self.assertEqual(ctx.exception.kind, "parse")

    def test_a_missing_page_fails_the_harvest(self):
        r = routes()
        del r[CLASSIQUES]
        with self.assertRaises(SourceError) as ctx:
            list(adapter(r).harvest())
        self.assertEqual(ctx.exception.kind, "network")

    def test_empty_lifespans_refuses_to_publish_anything(self):
        r = routes()
        r[LIFESPANS] = b"Text#,Type,Issued,Title,Language,Authors\n"
        with self.assertRaises(SourceError):
            list(adapter(r).harvest())


if __name__ == "__main__":
    unittest.main()
