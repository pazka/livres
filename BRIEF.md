# livres — brief

**What:** a small service on livres.hosh.it that lets "Ma liseuse" (the owner's mother's Windows app for
her Kindle 4) search many **legal, free, French** ebook sources at once, and lets her **ask the owner for
a book** she could not find.

**Users:** Mom (through the app only, never the web page), the owner (admin page, e-mails), agents
(GitHub issues + agent API, `AGENTS.md`).

**Features (v0.1):**
- Sources declared as data (`sources/*.json`): live-search sources (OPDS) and harvested catalogues (full
  dump indexed locally in SQLite FTS5). Enabled: Ebooks libres et gratuits, Project Gutenberg (French
  public domain only), Wikisource FR. Candidates: Gallica, Feedbooks.
- One OPDS 2.0 feed for the app: `/opds/v2/catalog.json`, `/opds/v2/search?query=`; acquisition links
  EPUB → MOBI → PDF pointing at the original source; `/api/v1/download-hosts` for the app's allowlist.
- `POST /api/v1/requests` → stored + e-mail to the owner (outbox with retries), rate-limited.
- Health: canary per source every 6 h, state machine OK/DEGRADED/DOWN/BLOCKED, e-mail on DOWN and on
  recovery; daily GitHub canary opens/closes `source-down` issues for agents.
- Admin page (password) and agent API (token): enable/disable/test/harvest sources, add OPDS sources at
  runtime, see requests and the audit log.

**Constraints:** box contract (`docs/BOX-CONTRACT.md`), standard library only, robots.txt and terms
honoured, no re-hosting, no piracy sources (`docs/SOURCES.md`), French error messages for the app.
