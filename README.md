# livres

https://livres.hosh.it — legal, free, French ebooks for "Ma liseuse" (OPDS 2.0 catalogue + "ask for a
book" by e-mail). Python standard library only.

| Read | For |
|---|---|
| `BRIEF.md` | what it does |
| `AGENTS.md` | **how to add / fix / remove a source, release, deploy** |
| `docs/ARCHITECTURE.md` | layers and data flow |
| `docs/SOURCES.md` | which sources, and why others are excluded |
| `contract/` | golden responses shared with the app |
| `DEPLOY.md` | the hosh.it box install and capability checks |

```sh
python -m unittest discover -s tests -t tests                       # all tests, no network
PYTHONPATH=src python -m livres.cli check --source ebooksgratuits     # live check of one source
LIVRES_DATA_DIR=./data LIVRES_SOURCES_DIR=./sources PYTHONPATH=src python -m livres   # http://localhost:8000
```
