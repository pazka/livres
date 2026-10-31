# Sources

Last reviewed: 2026-10-31 (Wikisource disabled, BEQ added). Machine-readable truth: `sources/*.json`. This page explains the choices.

## Rules

A source is acceptable only if **all** of these hold:

1. **Legal**: works in the public domain *in France* (author and translator dead for more than 70 years;
   authors "morts pour la France" get 30 more years: `MORTS_POUR_LA_FRANCE` in `sources/gutenberg.py`
   lists the known ones and is applied by every filter), or offered for free by the rights holder or
   an official distributor.
2. **Directly usable by Mom**: no account, no DRM (Adobe ADEPT, LCP…), no app lock-in. EPUB preferred,
   MOBI fine, PDF tolerated (the app marks it orange).
3. **Polite access**: an official machine interface (OPDS, documented API or catalogue dump) that robots.txt
   and the terms allow. We never re-host books: the app downloads from the source itself.

## Enabled (verified)

| id | Source | How | Why it is legal |
|---|---|---|---|
| `ebooksgratuits` | Ebooks libres et gratuits | Official OPDS 1.2, live search (`opds/feed.php?mode=search`) | Public-domain texts proofread by volunteers, free on their own site, EPUB/MOBI/PDF without DRM |
| `gutenberg` | Project Gutenberg | Weekly `pg_catalog.csv` (Gutenberg asks tools to use it instead of crawling), harvested into the local index; French books only, **filtered to French public domain** (all authors/translators died ≥ 71 years ago) | US public domain; the filter removes books still protected in France |
| `beq` | Bibliothèque électronique du Québec | Static collection pages (`vents`, `classiques`, `pdf`) parsed weekly: no OPDS or catalogue file exists, the owner allowed HTML parsing on 2026-10-31. robots.txt `Crawl-delay: 10` honoured | Public-domain texts, free EPUB/MOBI without DRM. BEQ follows Canadian law (life + 50), so the adapter keeps only **French-language authors dead for 71+ years** according to Gutenberg's catalogue (French must be the language of most of their Gutenberg books, so uncredited translations such as Gutenberg's French Conan Doyle are excluded), and only when the file name matches the author heading; anthologies, translations and unknown authors are left out |

## Candidates (shipped disabled, enable after a passing live check — AGENTS.md P1a)

| id | Source | Status |
|---|---|---|
| `gallica` | Gallica (BnF) | OPDS root `https://gallica.bnf.fr/opds` listed by liseuses.net; HTTP 403 from GitHub on 2026-10-31. Many items are PDF only. |
| `feedbooks` | Feedbooks public domain | OPDS root listed by liseuses.net; the store also sells books, the adapter keeps only free DRM-free links. HTTP 403 from GitHub on 2026-10-31. |
| `wikisource` | Wikisource FR (WS Export OPDS) | **Disabled 2026-10-31**: ws-export.wmcloud.org is now behind Anubis bot protection, so neither we nor the app can fetch its EPUBs (issue #9). The canary reports when it opens again. |

To investigate (no spec yet): **Bibebook** (bibebook.com, free public-domain EPUBs; no OPDS found yet),
**Bibliothèque numérique romande** (ebooks-bnr.com, no OPDS found), **noslivres.net** (index of 15 French
public-domain libraries, only an RSS of recent additions), more BEQ pages (author pages such as
`vents/zola.htm`, `libertinage`, `Philosophie`; `contemporain` holds living authors published by permission
and needs its own legal look), **Standard Ebooks** (English, OPDS available) if Mom ever wants English, the
**Internet Archive** advanced-search API restricted to public-domain collections (its lending library is
DRM-protected and must be excluded).

## Asked for, not included (and why)

| Source | Why not (for now) |
|---|---|
| **Kobo free list** (kobo.com/fr/fr/list/…) | Free Kobo books are delivered through a Kobo account and the Kobo apps, usually with DRM: they cannot go on a Kindle 4. No public API; their pages also could not be fetched by robots. |
| **Numilog free ebooks** (numilog.com, EbookGratuit.aspx) | No official API or OPDS found; the page refused our robot fetch. Numilog free titles are often Adobe-DRM protected, which a Kindle 4 cannot open. Can come back as a source if they publish a DRM-free OPDS feed. |
| **Gutenberg OPDS search** | Disallowed by gutenberg.org robots.txt; we use the official CSV catalogue instead. |
| **Atramenta** (OPDS) | Its OPDS path is disallowed to robots. Contemporary self-published works; could be reconsidered with permission. |

## From fmhy.pages.dev/reading

That page mixes legitimate libraries with shadow libraries. Taken: nothing directly yet, the legitimate
French-useful entries are already covered (Internet Archive/Open Library are "to investigate" above,
Gutenberg is enabled). **Left out on purpose**: Anna's Archive, Z-Library, Library Genesis, WeLib,
BookSee, Mobilism, MyAnonaMouse, Flibusta, Open Slum, eBooks1001, BDeBooks, eBookoz, Telegram/IRC/VK
channels and similar. Reasons:

- **Legal risk**: they distribute copyrighted books without permission. Downloading them is
  counterfeiting under French law (Code de la propriété intellectuelle), and the person exposed is the
  one downloading, i.e. Mom.
- **Malware risk**: these sites live on ads, fake "download" buttons and mirrors that change weekly; it is
  the most common way malware reaches non-technical users.
- **Reliability**: domains get seized and move constantly, so they would keep breaking the canary.

`tests/test_registry.py` fails if any of them appears in `sources/`.

## Removed

(none yet)
