# CLAUDE.md — pazka/livres

French ebook catalogue (OPDS 2.0) for "Ma liseuse", the owner's mother's Kindle app. Spec:
`BRIEF.md`. **Maintenance protocols (add / fix / remove a source, release): `AGENTS.md`** — read it
before any change. Architecture: `docs/ARCHITECTURE.md`. Sources and why: `docs/SOURCES.md`.
Owner: Alexandre (pazka).

Quick commands (Python 3.13, standard library only, nothing to install):
- tests: `python -m unittest discover -s tests -t tests`
- registry: `PYTHONPATH=src python -m livres.cli validate`
- live check of a source: `PYTHONPATH=src python -m livres.cli check --source <id>`
- run locally: `LIVRES_DATA_DIR=./data LIVRES_SOURCES_DIR=./sources PYTHONPATH=src python -m livres`
Target: **https://livres.hosh.it** on the owner's hosh.it box. **`docs/BOX-CONTRACT.md` is binding**:
an app that follows it deploys first try. Values already fixed for this project:

| | |
|---|---|
| Image | `ghcr.io/pazka/livres` |
| Host | `livres.hosh.it` (DNS already points at the box) |
| Loopback port | `127.0.0.1:9133` → container port 8000 |
| Data | named volume `livres-data` at `/data` |
| Env file on the box | `/srv/livres/livres.env` (mode 600, from `.env.example`) |

## Scope of the cloud builder
- You work on this GitHub repo only: code, tests, Dockerfile, CI. You have **no access to the
  production server**: the one-time first install is done by an on-box session; after that,
  releases deploy themselves (below). Never try to reach the box directly.
- The box-side files are already scaffolded. Keep them working and adapt them, don't replace them:
  `docker-compose.example.yml` (hardening is enforced by the box), `.env.example` (box defaults +
  `# required:` secrets line), `deploy/hosh-deploy.conf` (set `POST_DEPLOY` to a capability smoke
  that runs inside the container), `deploy/traefik-route.yml.tmpl`, `.github/workflows/ci.yml`
  (fill the test job for the stack), `release.yml` (its `deploy` job = auto-deploy), `deploy.yml`,
  `DEPLOY.md` (complete every TODO).
- **Deploying = cutting a release.** Once the owner has done the one-time install (DEPLOY.md §1b),
  every release you build is deployed by GitHub through the box's `hosh-deploy` runner, which
  verifies it and rolls back by itself. You may also run Actions → deploy (`vX.Y.Z`, `rollback`,
  `status`) and read its log. You can't reach the box any other way, and that's by design.
- Finish with: tests green, CI green on `main` (incl. Trivy gate + hardened container smoke), a
  `v0.1.0` release built (Actions → release → version, or push a tag), `.env.example`'s
  `APP_IMAGE` pinned to that tag, and `DEPLOY.md` listing the capability checks (not just a 200).

## Rules
- Never commit secrets, `*.env`, databases, exports. Never install MCP servers.
- Owner secrets (admin password, API keys) are set by an interactive owner-run script that writes
  the env file; nothing secret is printed, logged or passed on a command line.
- Small conventional commits (`feat:`, `fix:`, `test:`…), PRs to `main` with green CI (AGENTS.md ground rules).

## If you are the on-box deployment session (production server)
- Box rules (`/home/pazka/MIGRATION/CLAUDE.md`): `ACTIONS-LOG.md` intent BEFORE + result/revert
  AFTER; `scripts/smoke.sh` BEFORE and AFTER; root only inside an owner-opened sudo window.
- Releases deploy themselves (DEPLOY.md §1). You may run `sudo hosh-deploy deploy|rollback|status livres …`
  any time (no sudo window needed), with an ACTIONS-LOG line. A first install follows DEPLOY.md §1b
  exactly, inside an owner sudo window. Otherwise only read-only diagnostics.
- `livres.env`: you may create it from `.env.example` BEFORE any secret is in it; after the owner
  has filled it, **never print, cat, grep, copy or edit it**. If a variable is wrong, name it to the
  owner and stop. Never run owner-only interactive scripts; never delete the `livres-data` volume.

## Agent system: QA gate, journal, "what happened to X"
**Before any work in this repo, whatever the request:** read the hub `pazka/hosh-management`:
`AGENTS.md` (how the hosh.it agents and the owner work together), `README.md` (every app, site, repo and
journal), `roles/` (the full procedure of each agent: QA, Security, Developer, DevOps, dependency updates).
`gh api repos/pazka/hosh-management/contents/AGENTS.md -H "Accept: application/vnd.github.raw"`
(same for `README.md`, `roles/README.md`, `roles/qa.md`...). Those rules apply here; this file adds the
repo's own.

Canonical rules: `AGENTS.md` in the private hub `pazka/hosh-management`
(`gh api repos/pazka/hosh-management/contents/AGENTS.md -H "Accept: application/vnd.github.raw"`).
Summary, binding for every agent that works in this repo:
- **Nothing is merged without the independent QA agent.** Work on `agent/<topic>-<yyyymmdd>`, get CI
  green, write in the PR body what changed, which use cases it touches and how it was tested, then add
  the label `qa:requested`. Merge only when the PR has `qa:approved` AND a comment
  `QA-VERDICT: APPROVED sha=<current head sha>`. On `qa:changes-requested`: fix on the same branch,
  answer each finding, ask again; QA re-tests the whole PR. Never approve your own PR. After QA's
  approval the agent that opened the PR merges it: this replaces "the owner merges" and "merge when
  CI is green" wherever this file still says so.
- **Owner fast lane**: when the owner asks for a feature in a session he is in, do it end to end in
  that session (discuss, develop, QA by a fresh independent QA subagent or a QA routine run now, merge,
  release, deploy, show the live URL). Rules: AGENTS.md section 2b. The session that wrote the code never
  writes the verdict.
- **QA owns the tests and the visual regression**: every use case a user can reach (listed in
  `TESTING.md`), screenshots at 1400 / 1024 / 390 px, baselines in the repo. A wanted visual change
  must be announced in the PR. Never weaken a test or a gate to get green.
- A NEW third-party dependency always needs the owner: open an issue in `pazka/hosh-management`
  labelled `ready-for-human` (that label is the list of places where the owner is needed).
- Release = Actions → release with `version=vX.Y.Z` on the default branch. Never push a git tag.
- **Journal**: after each event (PR ready, QA verdict, merge, release, deploy, rollback, incident,
  blocker) add one comment to this project's journal, `pazka/hosh-management#8`.
- **When the owner asks "what happened to X"** (this app, another one, or the server): open the index
  in `pazka/hosh-management` `README.md`, read the last comments of X's journal issue
  (`gh issue view <n> -R pazka/hosh-management --comments`), then the open `ready-for-human` issues,
  then PRs / Actions runs if needed. Server facts you cannot see: ask the Box agent (mailbox from a
  laptop session, or an issue labelled `box-question`). Answer with: what happened (dated), the state
  now, where the owner is needed.
- Online checks use only the URLs in `pazka/hosh-management` `uptime/checks.json`.
