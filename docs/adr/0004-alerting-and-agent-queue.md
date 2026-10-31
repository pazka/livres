# ADR-0004: Alerts by e-mail, repairs through GitHub issues

Date: 2026-10-31 · Status: accepted

**Context.** The owner wants to know when a source breaks and to hand repairs to agents.

**Decision.** Two independent detectors: the service itself (every 6 h, e-mail through a persistent
outbox) and a daily GitHub Actions canary that also reads production status. The canary owns one issue
per broken source (`source-down: <id>`), updates it daily and closes it on recovery; an optional repo
variable mentions an agent so it starts automatically. Agents follow `AGENTS.md` protocols, and deploy only
by cutting a release (the box verifies and rolls back by itself).

**Consequences.** A box outage still produces issues (from GitHub); a GitHub outage still produces
e-mails (from the box). No credentials for GitHub live on the box.
