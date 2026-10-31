# ADR-0002: Sources are data, adapters are code

Date: 2026-10-31 · Status: accepted

**Context.** Sources come and go; agents must add, fix and remove them often and safely.

**Decision.** A source is a JSON spec in `sources/` validated by `sources.validate()` and tests; its
behaviour comes from a generic adapter per *kind* (OPDS 1, OPDS 2, Gutenberg-style CSV). Most new sources
are config only. OPDS sources can also be added at runtime from the admin page or the agent API (tested
before saving); on/off overrides live in the database for immediate mitigation; the repo stays the source
of truth.

**Consequences.** Adding a source rarely needs code; every verified source must ship fixtures; registry
tests enforce legal notes, https, host allowlists, canaries, and a floor of three verified sources.
