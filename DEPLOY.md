# Deploying livres.hosh.it

Deployment on the hosh.it box (box standard: `docs/BOX-CONTRACT.md`). Releases deploy themselves
through **hosh-deploy** (§1); a first install is a one-time on-box step (§1b). `.env.example`
defaults already fit the box; the **owner** only provides secrets.

## 1. How releases go live (hosh-deploy — the only deploy path on the box)

Every release (`release` workflow: tag `vX.Y.Z` or Actions → release) ends with the **`deploy`** job:
GitHub connects to the box as `deployer` with this repo's `HOSH_DEPLOY_KEY` (bound to livres only)
and runs `hosh-deploy deploy livres vX.Y.Z`. The box pulls the image, swaps the tag in
`livres.env`, waits healthy, checks `/healthz` on the loopback port and through Traefik, runs
`POST_DEPLOY` inside the container (`deploy/hosh-deploy.conf`) and **rolls back by itself** if any
check fails (the workflow then goes red). Nothing from the repo runs on the host.

- Redeploy / roll forward / back: Actions → **deploy** → `vX.Y.Z`, `rollback` or `status`.
- On the box (owner, or the on-box Claude without a sudo window): `sudo hosh-deploy deploy livres vX.Y.Z`,
  `sudo hosh-deploy rollback livres`, `sudo hosh-deploy status livres`. Log: `/var/log/hosh-deploy.log`.
- Repo variable `HOSH_AUTODEPLOY=false` stops auto-deploy after releases (manual dispatch still works).

## 1b. First install on the box (once; on-box Claude in an owner-opened sudo window)

Box rules: `ACTIONS-LOG.md` intent BEFORE, result + revert AFTER; `/home/pazka/MIGRATION/scripts/smoke.sh`
BEFORE and AFTER. Prerequisites done by `scripts/new-project.sh` + the on-box session: DNS record,
port registered in `11-BOX-STANDARD.md`, a `v0.2.0` release built.

```sh
# A — release checkout (provides docker-compose.example.yml, .env.example, deploy/*)
sudo mkdir -p /srv/livres && sudo chmod 750 /srv/livres && cd /srv/livres
[ -d .git ] || { sudo git init -q && sudo git remote add origin https://github.com/pazka/livres; }
sudo git fetch -q --depth 1 origin tag v0.2.0 && sudo git checkout -q v0.2.0
# B — env file from the template (no secret in it yet); never read it after step C
[ -e livres.env ] || sudo install -m 600 .env.example livres.env
```
**C — owner: secrets** (in your own SSH terminal; the image must be pulled first:
`sudo docker pull ghcr.io/pazka/livres:v0.2.0`):
`sudo bash /srv/livres/scripts/set-secrets.sh /srv/livres/livres.env`
It asks for the admin password (hashed inside the image, offline), generates the agent API token (shown
once: store it as the GitHub secret `LIVRES_ADMIN_TOKEN` and in your password manager), and asks for the
SMTP settings used for book requests and source alerts (a Gmail app password works).
```sh
# D — pin the box config (validates the compose hardening), first deploy (writes the Traefik route)
sudo hosh-deploy register livres
sudo hosh-deploy deploy livres v0.2.0
# E — GitHub key for auto-deploys (claude_agent: keygen; root: bind; gh: secret; private key shredded)
ssh-keygen -q -t ed25519 -N '' -C hosh-deploy:livres -f /tmp/livres-deploy
sudo /home/pazka/MIGRATION/scripts/setup-hosh-deploy.sh key livres /tmp/livres-deploy.pub
gh secret set HOSH_DEPLOY_KEY -R pazka/livres < /tmp/livres-deploy && shred -u /tmp/livres-deploy /tmp/livres-deploy.pub
```
Then add `svc_livres` to the box smoke harness (`scripts/smoke.sh`: function, host in `TLS_HOSTS`, name
in `SERVICES`, new `c_*` helpers in its `export -f` list) from §3, run `scripts/smoke.sh all` (AFTER),
and test the GitHub path once: Actions → deploy → `status`.

**Revert:** `cd /srv/livres && sudo docker compose --env-file livres.env down` (keeps the volume) +
`sudo rm /root/hosh-deploy/edge/traefik/dynamic/livres.yml /etc/hosh-deploy/apps/livres.conf` +
`sudo .../setup-hosh-deploy.sh unkey livres`.

## 1c. Change the mail settings later (owner)

Sender account wrong, password changed, new recipient: in your own SSH terminal,
```sh
cd /srv/livres && sudo git fetch -q --depth 1 origin tag vX.Y.Z && sudo git checkout -q vX.Y.Z  # script with --mail (v0.2.0+)
sudo bash /srv/livres/scripts/set-secrets.sh --mail /srv/livres/livres.env
sudo hosh-deploy status livres && sudo hosh-deploy deploy livres <version in use>   # restart with the new settings
```
Only the mail questions are asked; the admin password and the agent token stay as they are. Messages that
failed meanwhile stay in the outbox and go out with the new settings. Check: `/api/v1/status` shows
`"mail": "configured"`, `/admin/events` shows "en attente : 0", and a test book request reaches you.

## 2. Variables (`livres.env`, mode 600)
| Variable | Default | Set by | Meaning |
|---|---|---|---|
| `APP_IMAGE` | `ghcr.io/pazka/livres:v0.2.0` | hosh-deploy | release tag in use |
| `APP_BIND` | `127.0.0.1:9133` | box | loopback port |
| `APP_TRUSTED_PROXIES` | `127.0.0.0/8,::1/128,172.16.0.0/12` | box | peers whose `X-Forwarded-For` is believed |
| `APP_BASE_URL` | `https://livres.hosh.it` | box | absolute links in feeds and mails |
| `LIVRES_ADMIN_PASSWORD_HASH` | — | owner (script) | scrypt hash of the admin password |
| `LIVRES_ADMIN_TOKEN_SHA256` | — | owner (script) | sha256 of the agent/CI token |
| `LIVRES_CLIENT_TOKENS` | empty | owner | optional allowlist of app install tokens for requests |
| `LIVRES_OWNER_EMAIL` | — | owner (script) | recipient of requests and alerts |
| `LIVRES_MAIL_FROM` | — | owner (script) | sender address |
| `LIVRES_SMTP_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_TLS` | — / 587 / — / — / starttls | owner (script) | outgoing mail |
| `LIVRES_HEALTH_INTERVAL` | 21600 (6 h) | box | seconds between source checks |
| `LIVRES_HARVEST_INTERVAL` | 604800 (7 d) | box | seconds between catalogue harvests |
| `LIVRES_FAIL_THRESHOLD` | 2 | box | failed checks in a row before an alert |
| `LIVRES_REQUESTS_PER_HOUR` / `_PER_DAY` | 5 / 50 | box | book request limits (per install / total) |
| `LIVRES_MIN_HOST_INTERVAL` | 1 | box | seconds between two requests to one source host |
| `LIVRES_CACHE_TTL` | 600 | box | search cache seconds |

## 3. Capability checks (what "works" means — not just a 200)
`POST_DEPLOY` runs `python -m livres.cli smoke http://127.0.0.1:8000` inside the container (checks 1–5).
1. `GET /healthz` → 200 `{"status":"ok","db":"ok","books_indexed":N,"sources":{"enabled":≥3,"down":[…]},"mail":"configured"}`.
   After the first hour `books_indexed` > 1000 (Gutenberg + Wikisource harvested); `mail` must be `configured`.
2. `GET /opds/v2/catalog.json` → 200 `application/opds+json`, a `search` link with `templated: true`, non-empty `navigation`.
3. `GET /opds/v2/search?query=hugo` → 200 OPDS feed; once sources answer, `publications` non-empty, first link `application/epub+zip`.
4. `GET /api/v1/download-hosts` → `{"hosts":[…]}` containing `www.ebooksgratuits.com`, `www.gutenberg.org`.
5. `GET /api/v1/status` → every enabled source with `state` (`ok` after the first check, ~1 min after start).
6. `GET /admin` → 303 to `/admin/login`; `GET /api/v1/admin/sources` without token → 401.
7. Mail path (owner, once): `POST /api/v1/requests {"title":"Test smoke"}` → 202 and an e-mail arrives.
   Not in the automatic smoke on purpose (it would e-mail the owner on every deploy).

## 4. What runs
| Item | Value |
|---|---|
| Image | `ghcr.io/pazka/livres:vX.Y.Z` |
| Upstream | container :8000 → `127.0.0.1:9133` |
| Edge | `/root/hosh-deploy/edge/traefik/dynamic/livres.yml` (router `livres@file`, resolver `le`, HSTS) |
| Volume | docker volume `livres-data` → `/data` (in the monthly restic golden backup) |
| Health | `GET /healthz` → `{"status":"ok", …}` |
| Outbound | HTTPS to the hosts listed in enabled `sources/*.json` (`www.ebooksgratuits.com`, `www.gutenberg.org`, `gutenberg.org`, `beq.ebooksgratuits.com`; candidates add `ws-export.wmcloud.org`, `fr.wikisource.org`, `upload.wikimedia.org`, `gallica.bnf.fr`, `*.feedbooks.*`) + the owner's SMTP server. The code refuses any other host (`livres.http.host_allowed`). Remaining exfiltration route: a malicious spec could add a host, which needs a reviewed PR or the admin token. |

## 5. Decisions / known limits
- ADRs in `docs/adr/`. Standard library only; sources as data; never re-host; alerts by mail + GitHub issues.
- Port 9133 is the next free one in the registry as of 2026-10-31; the on-box session confirms it when
  adding the registry line (`scripts/new-project.sh` would pick the same).
- The French public-domain filter for Gutenberg uses death years only; war extensions ("morts pour la
  France", e.g. Saint-Exupéry) are not modelled.
- Candidate sources (Gallica, Feedbooks) are off until a live check passes (`AGENTS.md` P1a).
- Single process, one scheduler thread; the first harvest runs at start (Gutenberg CSV ≈ 15 MB).
