# ADR-0003: Link to the source, never re-host books

Date: 2026-10-31 · Status: accepted

**Context.** Re-hosting would make us a distributor (legal exposure), cost disk, and hide the source's
own corrections.

**Decision.** The catalogue only lists books; acquisition links point at the original site. The app
downloads from there, limited to `/api/v1/download-hosts`. A server-side fetch proxy (asked as "optional
later" by the app) would need its own ADR (licence check per source, checksum, cache size).

**Consequences.** When a source moves its download URLs, the health canary's download probe catches it.
