"""Source health: canary checks, state machine, owner alerts, and catalogue harvesting.

State machine (per source):
    any --ok--> OK                      (recovery mail if an alert was sent)
    any --robots/terms--> BLOCKED       (one mail; the source is skipped until a human decides)
    OK/DEGRADED --fail--> DEGRADED      (failures < threshold, no mail: sources hiccup)
    DEGRADED --fail--> DOWN             (failures >= threshold: one alert mail)
"""

from __future__ import annotations

import json
import logging
import time

from .catalog import Catalog
from .domain import Book, CheckResult, HealthState, Mode, SourceSpec
from .mail import Mailer
from .sources import SourceError

log = logging.getLogger("livres.health")

SHRINK_GUARD = 0.5  # a harvest that loses more than half the catalogue is refused (index kept)


class HealthMonitor:
    def __init__(self, catalog: Catalog, mailer: Mailer, base_url: str, fail_threshold: int = 2, harvest_interval: int = 7 * 86400):
        self.catalog = catalog
        self.store = catalog.store
        self.mailer = mailer
        self.base_url = base_url
        self.fail_threshold = fail_threshold
        self.harvest_interval = harvest_interval

    # -- checks ---------------------------------------------------------------------------------
    def run_check(self, spec: SourceSpec) -> CheckResult:
        """Run the canary without recording it (used by the CLI and the admin 'Tester' button)."""
        started = time.monotonic()
        try:
            if Mode.SEARCH in spec.modes:
                books = self.catalog.adapter(spec).search(spec.canary.query, 10)
                origin = "live search"
            else:
                books = self._index_canary(spec)
                origin = "local index"
            if len(books) < spec.canary.min_results:
                return self._result(spec, False, f"canary '{spec.canary.query}' found {len(books)} book(s) via {origin}, expected ≥ {spec.canary.min_results}", len(books), started)
            detail = f"canary '{spec.canary.query}': {len(books)} book(s) via {origin}"
            if spec.canary.check_download:
                detail += "; " + self._probe_download(spec, books)
            return self._result(spec, True, detail, len(books), started)
        except SourceError as e:
            return self._result(spec, False, f"{e.kind}: {e}", 0, started, blocked=e.kind == "robots")
        except Exception as e:  # adapter bug: report it as a failure, never crash the monitor
            log.exception("canary crashed")
            return self._result(spec, False, f"bug: {type(e).__name__}: {e}", 0, started)

    def _index_canary(self, spec: SourceSpec) -> list[Book]:
        last = self.store.last_harvest(spec.id, ok_only=True)
        if last is None:
            raise SourceError("harvest", "the catalogue has never been harvested successfully")
        age = time.time() - last["finished_at"]
        if age > 2 * self.harvest_interval + 86400:
            raise SourceError("harvest", f"last successful harvest is {int(age // 86400)} days old")
        return self.store.search_index(spec.canary.query, [spec.id], 10)

    def _probe_download(self, spec: SourceSpec, books: list[Book]) -> str:
        acq = books[0].sorted_acquisitions()[0]
        try:
            resp = self.catalog.adapter(spec).probe(acq.href)
        except SourceError as e:
            if e.kind == "robots":
                return "download not probed (robots.txt keeps robots off download links; the app downloads for the user)"
            raise SourceError(e.kind, f"download link broken ({acq.href}): {e}") from e
        if resp.status >= 400:
            raise SourceError("http", f"download link answers HTTP {resp.status}: {acq.href}")
        ctype = resp.headers.get("content-type", "")
        if "text/html" in ctype:
            raise SourceError("parse", f"download link returns a web page instead of a book: {acq.href}")
        return f"download OK ({ctype or 'no content-type'})"

    @staticmethod
    def _result(spec, ok, detail, results, started, blocked=False) -> CheckResult:
        return CheckResult(spec.id, ok, detail, results, int((time.monotonic() - started) * 1000), blocked)

    def check(self, spec: SourceSpec) -> CheckResult:
        result = self.run_check(spec)
        self.record(spec, result)
        return result

    def check_all(self) -> list[CheckResult]:
        return [self.check(spec) for spec, _ in self._enabled()]

    def _enabled(self):
        overrides = self.store.overrides()
        return [(s, None) for s in self.catalog.specs() if self.catalog.is_enabled(s, overrides)]

    # -- state machine --------------------------------------------------------------------------
    def record(self, spec: SourceSpec, result: CheckResult) -> HealthState:
        prev = self.store.health(spec.id)
        failures = prev["failures"] if prev else 0
        alerted = prev["alerted_state"] if prev else None
        self.store.log_check(spec.id, result.ok, result.detail, result.results, result.duration_ms)
        log.info(json.dumps({"event": "source_check", "source": spec.id, "ok": result.ok, "detail": result.detail, "ms": result.duration_ms}))

        if result.ok:
            state, failures = HealthState.OK, 0
            if alerted in (HealthState.DOWN.value, HealthState.BLOCKED.value):
                self._mail(f"[livres] Source rétablie : {spec.name}", self._recovered_body(spec, result))
                alerted = HealthState.OK.value
        elif result.blocked:
            state = HealthState.BLOCKED
            failures += 1
            if alerted != HealthState.BLOCKED.value:
                self._mail(f"[livres] Source bloquée par robots.txt : {spec.name}", self._down_body(spec, result, HealthState.BLOCKED))
                alerted = HealthState.BLOCKED.value
        else:
            failures += 1
            state = HealthState.DOWN if failures >= self.fail_threshold else HealthState.DEGRADED
            if state is HealthState.DOWN and alerted != HealthState.DOWN.value:
                self._mail(f"[livres] Source en panne : {spec.name}", self._down_body(spec, result, state))
                alerted = HealthState.DOWN.value
        self.store.save_health(spec.id, state, failures, result.detail, result.results, result.ok, alerted)
        return state

    def _mail(self, subject, body):
        self.mailer.notify_owner(subject, body)

    def _down_body(self, spec: SourceSpec, result: CheckResult, state: HealthState) -> str:
        row = self.store.health(spec.id)
        last_ok = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(row["last_ok"])) if row and row["last_ok"] else "jamais"
        what = (
            "Le site interdit désormais l'accès aux robots (robots.txt). La source est ignorée tant que personne n'a décidé quoi faire."
            if state is HealthState.BLOCKED
            else f"La source a échoué {self.fail_threshold} vérifications de suite. Les recherches continuent avec les autres sources."
        )
        return f"""Bonjour,

{what}

Source       : {spec.name} ({spec.id})
Erreur       : {result.detail}
Dernier succès : {last_ok}

Que faire :
  1. Regarder la source dans l'admin : {self.base_url}/admin
     (bouton « Tester », ou « Désactiver » pour la retirer tout de suite).
  2. Ou confier la réparation à un agent : le canari GitHub quotidien ouvre une issue « source-down »
     dans le dépôt pazka/livres ; le protocole P2 d'AGENTS.md dit à l'agent quoi faire.

-- livres.hosh.it
"""

    def _recovered_body(self, spec, result) -> str:
        return f"""Bonjour,

La source {spec.name} ({spec.id}) fonctionne de nouveau.
Vérification : {result.detail}

-- livres.hosh.it
"""

    # -- harvesting -----------------------------------------------------------------------------
    def harvest(self, spec: SourceSpec) -> tuple[bool, str]:
        hid = self.store.start_harvest(spec.id)
        try:
            books = [b for b in self.catalog.adapter(spec).harvest() if b.usable]
            previous = self.store.count_books(spec.id)
            if previous > 100 and len(books) < previous * SHRINK_GUARD:
                raise SourceError("parse", f"catalogue shrank from {previous} to {len(books)} books: index kept, please check")
            if not books:
                raise SourceError("parse", "the harvest returned no book")
            count = self.store.replace_source_books(spec.id, books)
            self.store.finish_harvest(hid, True, count, "ok")
            self.catalog_changed()
            log.info(json.dumps({"event": "harvest", "source": spec.id, "books": count}))
            return True, f"{count} books"
        except SourceError as e:
            self.store.finish_harvest(hid, False, 0, f"{e.kind}: {e}")
            log.warning(json.dumps({"event": "harvest_failed", "source": spec.id, "error": str(e)}))
            return False, f"{e.kind}: {e}"
        except Exception as e:
            self.store.finish_harvest(hid, False, 0, f"bug: {e}")
            log.exception("harvest crashed")
            return False, f"bug: {type(e).__name__}: {e}"

    def harvest_due(self) -> list[tuple[str, bool, str]]:
        out = []
        for spec, _ in self._enabled():
            if Mode.HARVEST not in spec.modes:
                continue
            last = self.store.last_harvest(spec.id)
            if last is not None and time.time() - last["finished_at"] < (self.harvest_interval if last["ok"] else 6 * 3600):
                continue
            ok, detail = self.harvest(spec)
            out.append((spec.id, ok, detail))
        return out

    def catalog_changed(self) -> None:
        """Hook set by the app to clear the search cache after a harvest."""
