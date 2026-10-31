# ADR-0005: BEQ through its HTML pages, with a French public-domain filter

Date: 2026-10-31 · Status: accepted (filter switched OFF the same day by the owner: full library for now, `france_public_domain: false`, to be discussed)

**Context.** Wikisource's export tool went behind bot protection (issue #9), which left two verified
sources, one fewer than the minimum. The Bibliothèque électronique du Québec has a large French
catalogue with EPUB and MOBI files, but no OPDS feed and no catalogue file. It follows Canadian law
(life + 50 years for authors who died before 1972), so part of its catalogue is still protected in France.

**Decision.** The owner allowed parsing its static HTML pages (ground rule 3). The `beq_html` adapter
reads the collection pages once a week, honouring robots.txt `Crawl-delay: 10`. It keeps a book only when:
- the author heading is known;
- the file name contains the author's surname, which guards against misplaced headings;
- Gutenberg's official catalogue shows that author wrote a French book with no translator and died at
  least 71 years ago, with a French Gutenberg book filed under Library of Congress class PQ (French literature) and
  no translator credited (so uncredited translations are refused), with 30 more years for authors "morts pour la France". Titles marked "Trad." are
  refused too.

There is no Wikimedia lookup, because Wikipedia and Wikidata APIs are disallowed to generic robots.

**Consequences.** Some legal books are missed: Québec authors that Gutenberg doesn't know, anthologies,
and translations. That is accepted, because a missing book is better than an illegal one. A layout change
yields zero entries, so the harvest fails and the canary opens an issue instead of silently emptying the
index (the 50 % shrink guard also applies).
