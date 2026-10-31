# Architecture

```
 Ma liseuse (Mom's PC) ──HTTPS──► Traefik (hosh.it edge) ──► livres container :8000 (127.0.0.1:9133)
   OPDS 2.0 search                                             │
   POST /api/v1/requests                                       ├─ web/      router, auth, OPDS/JSON/HTML
 Owner browser ── /admin (password)                            ├─ services  search · health · requests · mail
 Agents / CI ──── /api/v1/admin (token)                        ├─ catalog   repo specs + admin specs + overrides
                                                               ├─ sources/  opds1 · opds2 · gutenberg_csv · beq_html adapters
 GitHub Actions ─ sources-canary (daily) → issues              ├─ http      PoliteHttpClient (robots, allowlist, throttle)
                                                               └─ store     SQLite WAL in /data (index FTS5, health, requests, outbox, audit)
                                                                    │
                                     legal sources ◄── live search / weekly harvest
```

## Layers and rules

| Layer | Module | Knows about | Never |
|---|---|---|---|
| domain | `domain.py` | dataclasses, format ranking, text folding | I/O |
| outbound | `http.py` | urllib, robots.txt (incl. Crawl-delay) | business rules |
| adapters | `sources/*.py` | one source kind's format | the web layer, the store |
| registry | `sources/__init__.py` | spec validation, kind → adapter | network |
| catalog | `catalog.py` | which sources are in effect | parsing |
| services | `search.py`, `health.py`, `bookrequests.py`, `mail.py` | orchestration | HTTP requests/responses |
| persistence | `store.py` | SQL | network |
| web | `web/` | parsing requests, auth, rendering | parsing sources |
| composition | `main.py` | wiring, scheduler thread | logic |

## Two kinds of source

- **search**: the user's query is forwarded live (parallel, 12 s deadline, results cached 10 min). A
  slow or failing source is reported apart and never breaks the search.
- **harvest**: the whole catalogue is downloaded weekly and indexed in SQLite FTS5 (accent-insensitive).
  A harvest that loses more than half the catalogue is refused (the old index stays): protects against
  a broken upstream page wiping the index.

Results from both are deduplicated (folded title + author surname, the higher-priority source wins) and
ranked (title words > author words > prefix matches; EPUB preferred).

## Health

`HealthMonitor` runs each source's canary (a query that must find ≥ N books, then a 4 KB range request on
the first download link: a web page instead of a book counts as broken). States: OK → DEGRADED (1 failure,
silent) → DOWN (≥ 2, one e-mail) ; BLOCKED when robots.txt forbids us (one e-mail, source skipped).
Recovery sends one e-mail. Harvested sources also fail when their last good harvest is too old.
`sources-canary` repeats the checks from GitHub daily and manages `source-down` issues (agents' queue).

## Data

All state in `/data/livres.sqlite3` (WAL) + `/data/.secrets/session.key`. The book index is rebuildable;
requests, overrides, runtime sources and the audit log are not (the volume is in the monthly backup).

## Security

Box hardening (non-root, read-only rootfs, no capabilities). No third-party package. Admin: scrypt
password, HMAC-signed `HttpOnly; Secure; SameSite=Strict` session, CSRF token on every form, 5 failed
logins / 15 min. Agent API: sha256-compared bearer token. Strict CSP, no JavaScript. Request bodies
≤ 64 KB. XML parsing refuses entity declarations. Client IPs are only logged hashed. Outbound requests only
to hosts declared by enabled sources, redirects included.
