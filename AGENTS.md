# AGENTS.md — how an agent maintains livres

You are maintaining **livres.hosh.it**, the legal French ebook catalogue behind "Ma liseuse", the Windows
app the owner (Alexandre, `pazka`) built for his mother. She is not technical. Every change must keep her
search working. Read `CLAUDE.md` first (box rules), then the protocol you need below.

## Ground rules (all protocols)

1. **Legal sources only** (`docs/SOURCES.md` §Rules). Public domain, or a catalogue the rights holder offers
   for free. No shadow library, no "free download" aggregator of copyrighted books, no DRM, no account.
   `tests/test_registry.py` refuses known piracy hosts; that list is a floor, not the definition.
2. **Polite by construction.** All fetching goes through `livres.http.PoliteHttpClient` (robots.txt,
   host allowlist, 1 req/s/host, size cap). Never add a second HTTP path. If robots.txt forbids what we
   need, the answer is "drop the source" or "ask the site", never a workaround.
3. **Prefer official machine interfaces**: OPDS 1/2 > documented API/catalogue dump > HTML. HTML
   scraping needs the owner's explicit yes in the issue first.
4. **Tests first.** A fix starts with a fixture that reproduces the failure. Never delete, skip or loosen
   a test to get green. Standard library only: a new dependency needs the owner's yes.
5. **Contract with the app is additive.** `tests/test_contract.py` + `contract/*.json` freeze what the app
   receives. Adding fields is fine (P5). Anything else is `/opds/v3` and needs the owner.
6. **No secrets** in the repo, issues or logs. You never see `livres.env` on the box.
7. **Conventional commits**, one concern per PR, CI green before merge. The owner merges unless he said
   otherwise in the issue.

## Your tools

| Need | How |
|---|---|
| Run everything locally | `python -m unittest discover -s tests -t tests` (≈20 s, no network) |
| Validate the registry | `PYTHONPATH=src python -m livres.cli validate` |
| Live-check one source (real network) | `PYTHONPATH=src python -m livres.cli check --source <id>` |
| Live-check all, incl. candidates | `PYTHONPATH=src python -m livres.cli check --include-candidates --json` |
| See production | `curl -s https://livres.hosh.it/api/v1/status` (public) |
| Act on production now (no deploy) | Agent API with the `LIVRES_ADMIN_TOKEN` secret, see below |
| Deploy | cut a release (P4); the box verifies and rolls back by itself |

**Agent API** (`Authorization: Bearer $LIVRES_ADMIN_TOKEN`, base `https://livres.hosh.it`):

| Call | Effect |
|---|---|
| `GET /api/v1/admin/sources` | every source with state, last check, detail, override |
| `POST /api/v1/admin/sources/<id>/check` | run its canary now and record the result |
| `POST /api/v1/admin/sources/<id>/disable` `{"reason": "..."}` | stop using it immediately (override) |
| `POST /api/v1/admin/sources/<id>/enable` / `reset` | re-enable / go back to the repo setting |
| `POST /api/v1/admin/sources/<id>/harvest` | re-download a harvested catalogue now |
| `POST /api/v1/admin/validate` `<spec JSON>` | validate a spec and run its canary, saves nothing |
| `POST /api/v1/admin/sources` `<spec JSON>` | add an OPDS source at runtime (tested first; kind opds1/opds2 only) |
| `DELETE /api/v1/admin/sources/<id>` | remove a runtime-added source |
| `GET /api/v1/admin/requests` | Mom's book requests |

Every call is written to the audit log (`/admin/events`). Runtime changes are for **mitigation**; the
durable fix is a PR (the repo is the source of truth, runtime overrides survive restarts until reset).

## How alerts reach you

- The service checks every enabled source every 6 h. Two failures in a row → **e-mail to the owner**.
- `sources-canary` (GitHub Actions, daily 05:17 UTC) checks every source from GitHub and reads production
  status. Each broken source gets **one issue `source-down: <id>`** (label `source-down`), updated daily,
  closed automatically when it passes again. If the repo variable `LIVRES_AGENT_MENTION` is set (e.g.
  `@claude`), the issue mentions it so an agent starts on its own.
- A candidate that starts passing gets a `source-candidate-ok: <id>` issue.

---

## P1a — Add a source of an existing kind (OPDS 1, OPDS 2, Gutenberg-style CSV)

1. **Legal check.** Write in the PR why it is legal (who publishes, which rights) and fill `legal` in one
   French sentence (shown to the owner). If unsure, stop and ask in the issue.
2. **Find the official interface.** Look for `rel="search"` / OPDS links, `/opds`, `opensearch.xml`,
   documented dumps. Read `robots.txt` and the terms. Note both in `notes`.
3. **Write `sources/<id>.json`** (copy the closest existing one). Keys: `id` (= file name), `name`, `kind`,
   `homepage`, `legal`, `modes` (`search` = live query, `harvest` = periodic full download into the local
   index), `config` (see the adapter docstring), `allowed_hosts` (every host incl. download hosts — the
   app only downloads from these), `canary` (a query that must always return ≥ `min_results` books),
   `priority` (lower wins on duplicates), `status`, `enabled`.
4. **Record fixtures** in `tests/fixtures/<id>/` from the live feed (trim to a few entries, keep the real
   structure, no personal data). Add a test in `tests/test_sources.py` that parses them.
5. **Live check:** `python -m livres.cli check --source <id>` must pass. Paste its output in the PR.
6. New, unproven sources ship as `"status": "candidate", "enabled": false`. Flip to `verified` + `enabled`
   only with a passing live check (here or from a `source-candidate-ok` issue).
7. PR → CI green → owner merges → P4 release.

## P1b — Add a new source KIND (a new adapter)

Only when no existing kind fits. `src/livres/sources/<kind>.py` subclassing `SourceAdapter`
(read its docstring: fetch only via `self.fetch`, return only free DRM-free books, stable identifier
`<source>:<id>`, raise `SourceError`). Register it in `KINDS`, document its `config` keys in the module
docstring, add `required_config`, fixtures and tests for: happy path, a malformed response
(→ `SourceError("parse")`), paid/DRM filtering if the source can have them. Then P1a for the source.

## P2 — Fix a broken source (`source-down` issue)

1. Read the issue: the detail says what failed (`http`, `network`, `parse`, `robots`, `harvest`, `bug`).
2. Reproduce: `python -m livres.cli check --source <id>`. Fetch the failing URL yourself and compare with
   `tests/fixtures/<id>/`.
3. Decide:
   - **Transient** (5xx, timeout, passes now): comment with the evidence; the canary closes the issue.
   - **Moved URL / changed format**: update the spec and/or adapter. Re-record the fixture (keep the old
     one as `*-old.*` if the adapter must still read it), add a regression test reproducing the failure,
     then the fix. Live check must pass.
   - **`robots`**: the site now forbids us. Do not work around it. Go to P3 and explain in the issue.
   - **Site gone / now paid / now DRM**: P3.
4. If Mom is affected while you work (the source is a big one), disable it now through the agent API and
   say so in the issue; re-enable (`reset`) after the deploy.
5. PR referencing the issue (`Fixes #N`) → CI → merge → P4. Verify with
   `POST /api/v1/admin/sources/<id>/check` after the deploy and paste the result in the issue.

## P3 — Disable or remove a source

- **Immediately (minutes):** `POST /api/v1/admin/sources/<id>/disable {"reason": "..."}`.
- **Durably:** PR setting `"enabled": false` with a `notes` line explaining why and the date, or deleting
  `sources/<id>.json` + `tests/fixtures/<id>/` if it will never come back. Keep **at least three verified
  enabled sources** (`test_registry` enforces it); if that would break, find a replacement first (P1a).
- Update `docs/SOURCES.md` (move it to "Removed" with the reason).
- After the deploy, `reset` the runtime override so the repo setting rules again.

## P4 — Release and deploy

1. `main` green. Bump nothing by hand: the version is the tag.
2. Actions → **release** → `vX.Y.Z` (SemVer: fix = patch, new source/feature = minor, contract break = never
   without the owner). The release job tests, builds, scans (Trivy), pushes `ghcr.io/pazka/livres:vX.Y.Z`,
   then the `deploy` job asks the box's `hosh-deploy` to deploy it. The box checks health, loopback and
   HTTPS `/healthz`, runs `python -m livres.cli smoke` inside the container, and **rolls back by itself**
   if anything fails (the workflow then goes red).
3. Verify: `curl -s https://livres.hosh.it/healthz` shows the new version; `GET /api/v1/status`.
4. Bad release that passed the checks: Actions → **deploy** → `rollback`.

## P5 — Change what the app receives

Additive only (new optional field, new endpoint). Run `LIVRES_UPDATE_CONTRACT=1 python -m unittest
discover -s tests -t tests -p test_contract.py`, review the `contract/` diff, describe it in the PR so
the app (repo `pazka/kindle-for-mom`) can copy the golden files into its tests.

## P6 — Book requests

Requests arrive by e-mail to the owner and in `/admin/requests`. Agents may search legal sources for a
requested book and comment with links, but never fetch a copy from a non-listed site and never reply to
Mom directly.
