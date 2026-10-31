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
port registered in `11-BOX-STANDARD.md`, a `v0.1.0` release built.

```sh
# A — release checkout (provides docker-compose.example.yml, .env.example, deploy/*)
sudo mkdir -p /srv/livres && sudo chmod 750 /srv/livres && cd /srv/livres
[ -d .git ] || { sudo git init -q && sudo git remote add origin https://github.com/pazka/livres; }
sudo git fetch -q --depth 1 origin tag v0.1.0 && sudo git checkout -q v0.1.0
# B — env file from the template (no secret in it yet); never read it after step C
[ -e livres.env ] || sudo install -m 600 .env.example livres.env
```
**C — owner: secrets.** TODO (builder): the exact owner command(s), e.g.
`sudo bash -c 'cd /srv/livres && bash scripts/set-admin.sh ./livres.env'`; delete if none.
```sh
# D — pin the box config (validates the compose hardening), first deploy (writes the Traefik route)
sudo hosh-deploy register livres
sudo hosh-deploy deploy livres v0.1.0
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

## 2. Variables (`livres.env`, mode 600)
TODO (builder): table of every variable: default, who sets it, meaning.

## 3. Capability checks (what "works" means — not just a 200)
TODO (builder): one line per feature: request → expected shape/effect. Example:
`GET /api/items` → 200 JSON list; `POST /api/items` round-trip then `DELETE` (test item).

## 4. What runs
| Item | Value |
|---|---|
| Image | `ghcr.io/pazka/livres:vX.Y.Z` |
| Upstream | container :8000 → `127.0.0.1:9133` |
| Edge | `/root/hosh-deploy/edge/traefik/dynamic/livres.yml` (router `livres@file`, resolver `le`, HSTS) |
| Volume | docker volume `livres-data` → `/data` (in the monthly restic golden backup) |
| Health | `GET /healthz` → `{"status":"ok", …}` |
| Outbound | TODO (builder): external hosts the app calls, or "none" |

## 5. Decisions / known limits
TODO (builder).
