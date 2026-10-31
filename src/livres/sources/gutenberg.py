"""Project Gutenberg via its weekly CSV catalogue (Gutenberg asks tools to use these files
instead of crawling the site: https://www.gutenberg.org/ebooks/offline_catalogs.html).

config keys:
  catalog_url     the CSV (pg_catalog.csv)
  languages       e.g. ["fr"]
  acquisitions    [{"href": "...{id}...", "type": "<media type>"}], EPUB first
  cover_url       optional, with {id}
  page_url        with {id}
  france_public_domain  true -> keep only books whose every author/translator died more than 70 years
                  before this year (French rule: public domain on 1 January after 70 years post mortem).
                  Gutenberg follows US law, so some of its French books are still protected in France.
"""

from __future__ import annotations

import csv
import datetime
import io
import re
from collections.abc import Iterator

from ..domain import Acquisition, Book, display_author, normalise_type
from .base import SourceAdapter, SourceError


_LIFESPAN = re.compile(r"(\d{1,4})?\??\s*(BCE)?\s*-\s*(\d{1,4})?\??\s*(BCE)?")
_ANONYMOUS = ("anonymous", "unknown", "various", "anonyme")

# Authors "morts pour la France" get 30 more years of protection in France (CPI art. L123-10). This list
# is a floor of well-known writers still affected (death + 100 years not reached); add names when found.
# Keys are "surname firstname" folded (no accents, lowercase).
MORTS_POUR_LA_FRANCE = frozenset({
    "saint exupery antoine de", "desnos robert", "jacob max", "prevost jean", "fondane benjamin",
    "nemirovsky irene", "bloch marc", "cavailles jean", "politzer georges", "decour jacques",
    "rabearivelo jean joseph", "pergaud louis", "alain fournier", "peguy charles", "apollinaire guillaume",
    "psichari ernest", "nizan paul", "hyvernaud georges",
})
MORT_POUR_LA_FRANCE_EXTRA_YEARS = 30


def _person_key(author: str) -> str:
    from ..domain import fold  # local import keeps the module import graph flat
    parts = [p for p in author.split(",") if p.strip() and not re.search(r"\d", p) and "[" not in p]
    return fold(",".join(parts[:2]))


def public_domain_in_france(authors_field: str, this_year: int | None = None) -> bool:
    """'Hugo, Victor, 1802-1885; Doe, John, 1900-1990 [Translator]' -> False in 2026 (Doe died < 71 years ago).

    Authors "morts pour la France" (MORTS_POUR_LA_FRANCE) need 30 more years.
    Conservative: an author with no death year (alive, or unknown dates) makes the book ineligible,
    except anonymous works."""
    cutoff = (this_year or datetime.date.today().year) - 71
    for author in (a.strip() for a in (authors_field or "").split(";")):
        if not author:
            continue
        name = author.split(",", 1)[0].strip().lower()
        match = None
        for match in _LIFESPAN.finditer(author):
            pass
        if match is None or not (match.group(1) or match.group(3)):
            if any(name.startswith(a) for a in _ANONYMOUS):
                continue
            return False
        death = match.group(3)
        if death is None:  # "1950-": still alive or death unknown
            return False
        if match.group(4):  # died BCE
            continue
        limit = cutoff
        if _person_key(author) in MORTS_POUR_LA_FRANCE:
            limit -= MORT_POUR_LA_FRANCE_EXTRA_YEARS
        if int(death) > limit:
            return False
    return True


class GutenbergCsvAdapter(SourceAdapter):
    required_config = ("catalog_url", "acquisitions", "page_url")

    def harvest(self) -> Iterator[Book]:
        resp = self.fetch(self.spec.config["catalog_url"], max_bytes=120_000_000, accept="text/csv")
        yield from self.parse_catalog(resp.body.decode("utf-8", errors="replace"))

    def parse_catalog(self, text: str) -> Iterator[Book]:
        reader = csv.DictReader(io.StringIO(text))
        needed = {"Text#", "Type", "Title", "Language", "Authors"}
        if not reader.fieldnames or not needed.issubset(reader.fieldnames):
            raise SourceError("parse", f"unexpected CSV header: {reader.fieldnames}")
        languages = set(self.spec.config.get("languages", ["fr"]))
        cfg = self.spec.config
        for row in reader:
            if row.get("Type", "").strip() != "Text":
                continue
            langs = {code.strip() for code in row.get("Language", "").split(";")}
            if not langs & languages:
                continue
            if cfg.get("france_public_domain") and not public_domain_in_france(row.get("Authors", "")):
                continue
            num = row.get("Text#", "").strip()
            title = " ".join((row.get("Title") or "").split())
            if not num.isdigit() or not title:
                continue
            authors = tuple(
                a
                for a in (display_author(x) for x in (row.get("Authors") or "").split(";") if "[" not in x)
                if a
            )
            yield Book(
                identifier=f"{self.spec.id}:{num}",
                source_id=self.spec.id,
                title=title,
                authors=authors,
                language="fr",
                description=" ; ".join(s.strip() for s in (row.get("Subjects") or "").split(";") if s.strip())[:500],
                cover=cfg["cover_url"].format(id=num) if cfg.get("cover_url") else None,
                page_url=cfg["page_url"].format(id=num),
                acquisitions=tuple(
                    Acquisition(a["href"].format(id=num), normalise_type(a["type"])) for a in cfg["acquisitions"]
                ),
                issued=(row.get("Issued") or "").strip() or None,
            )
