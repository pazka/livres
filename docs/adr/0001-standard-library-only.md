# ADR-0001: Python standard library only

Date: 2026-10-31 · Status: accepted

**Context.** The box's supply-chain rule (Decision #17) requires the owner's yes for every third-party
dependency. The service needs HTTP server and client, XML/JSON/CSV parsing, SQLite with full-text search,
SMTP and password hashing: Python 3.13's standard library has all of them (`http.server`, `urllib`,
`xml.etree`, `sqlite3` + FTS5, `smtplib`, `hashlib.scrypt`).

**Decision.** No PyPI package, at runtime or for tests (`unittest`). The image is `python:3.13-slim`
pinned by digest plus our code.

**Consequences.** Nothing to audit but our code and the base image; no lockfile; CI needs no install step.
We maintain a small router instead of a framework. If load ever needs it, a framework is a new ADR.
