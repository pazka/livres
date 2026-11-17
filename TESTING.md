# Testing

Maintenance protocols: `AGENTS.md`. Standard library only, nothing to install.

## Run it

```bash
python -m unittest discover -s tests -t tests          # unit, integration, end-to-end, contract (no network)
PYTHONPATH=src python -m livres.cli validate           # source registry
docker build -t livres:local . && bash visual/run.sh livres:local   # visual regression (needs docker, node >= 22, Chrome)
```

CI (`.github/workflows/ci.yml`): `test` runs the first two; `image` builds, runs the Trivy CRITICAL gate,
the hardened container smoke (`livres.cli smoke`), then the visual regression.

## Visual use cases

Harness: `visual/` (hosh-management AGENTS.md section 3). `visual/run.sh` starts the image hardened as on
the box (uid 10001, read-only rootfs, no capabilities, no-new-privileges) on an empty volume with
`LIVRES_SCHEDULER=off` and an admin password that exists only in that container, then seeds fixed data
(`visual/seed.py`): the live-search source is switched off, so **no search leaves the container**. Results
come from the harvested index, which holds 23 fixed Gutenberg books (22 by Victor Hugo) and 1 BEQ book. One book
request is already listed. `visual/shoot.mjs` shoots every case below at **1400, 1024 and 390 px** wide
and compares it with `visual/baseline/`. Dates in tables are replaced by `2026-01-01 12:00`.

| Id | Page / state | How it is reached |
|---|---|---|
| H1 | Home: search form + request form | `/` |
| H2 | Search with results, page 1 of 2 ("Page suivante") | `/recherche?q=Victor+Hugo` |
| H3 | Search, page 2 | `/recherche?q=Victor+Hugo&page=2` |
| H4 | Search with no result ("Aucun livre trouvé") | `/recherche?q=Zzyzx+introuvable` |
| H5 | Search too short (error message) | `/recherche?q=a` |
| H6 | Book request sent ("Merci !") | `/`, fill Titre + Auteur, submit the request form |
| I1 | Robot information page | `/robots-info` |
| N1 | Unknown address (plain-text 404) | `/nope` |
| A1 | Admin login form | `/admin/login` |
| A2 | Admin login, wrong password | `/admin/login`, submit a wrong password |
| A3 | Admin: sources table + add-catalogue form | log in, `/admin` |
| A4 | Admin: sources with a message | log in, `/admin?msg=…` |
| A5 | Admin: book requests | log in, `/admin/requests` |
| A6 | Admin: event journal | log in, `/admin/events` |

Not covered: the OPDS feeds (JSON, tested by `tests/`), admin actions that change the state of a source
(Tester, Activer/Désactiver, Recharger, Supprimer, Ajouter: they call the network or change the data of the
other cases), the request rate-limit messages, and the mail.

**Phone width (390 px):** every page fits the screen: form controls are capped at the width of their container and the two-column form grid becomes one column below 600 px (fixed in pazka/hosh-management#34; `tests/test_web.py::test_stylesheet_keeps_forms_inside_a_phone_screen`). The harness fails on any change of page size, so a regression to a horizontal scroll shows in the 390 px shots. N1 is plain text without a viewport, so a phone lays it out at 980 px: that is expected.

**Renewing the baselines** (only in a PR that says the visual change is wanted):
`bash visual/run.sh livres:local --update`, look at every changed picture, commit. Baselines are only
meaningful for the Chrome that made them (`visual/baseline/chrome.txt`). CI uses the runner's Chrome, so CI
baselines are taken back from a CI log with `node visual/from-log.mjs <job log> visual/baseline --chrome`.
