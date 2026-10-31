"""The set of sources in effect right now: repo specs + admin-added OPDS sources + on/off overrides."""

from __future__ import annotations

import json
import logging
import threading

from .domain import HealthState, Mode, SourceSpec
from .http import HttpClient
from .sources import SourceAdapter, SpecError, build_adapter, spec_from_dict
from .store import Store

log = logging.getLogger("livres.catalog")


class Catalog:
    def __init__(self, repo_specs: list[SourceSpec], store: Store, http: HttpClient):
        self.repo_specs = {s.id: s for s in repo_specs}
        self.store = store
        self.http = http
        self._adapters: dict[str, tuple[str, SourceAdapter]] = {}
        self._lock = threading.Lock()

    def specs(self) -> list[SourceSpec]:
        specs = dict(self.repo_specs)
        for raw in self.store.admin_sources():
            try:
                spec = spec_from_dict(raw, origin="admin")
            except SpecError as e:
                log.warning(json.dumps({"event": "invalid_admin_source", "error": str(e)}))
                continue
            if spec.id not in specs:  # a repo source always wins over an admin one with the same id
                specs[spec.id] = spec
        return sorted(specs.values(), key=lambda s: (s.priority, s.id))

    def get(self, source_id: str) -> SourceSpec | None:
        return next((s for s in self.specs() if s.id == source_id), None)

    def is_enabled(self, spec: SourceSpec, overrides: dict[str, bool] | None = None) -> bool:
        overrides = self.store.overrides() if overrides is None else overrides
        return overrides.get(spec.id, spec.enabled)

    def adapter(self, spec: SourceSpec) -> SourceAdapter:
        fingerprint = json.dumps([spec.kind, spec.config, spec.allowed_hosts], sort_keys=True)
        with self._lock:
            cached = self._adapters.get(spec.id)
            if cached and cached[0] == fingerprint:
                return cached[1]
            adapter = build_adapter(spec, self.http)
            self._adapters[spec.id] = (fingerprint, adapter)
            return adapter

    def active(self, mode: Mode | None = None) -> list[tuple[SourceSpec, SourceAdapter]]:
        overrides = self.store.overrides()
        health = self.store.all_health()
        out = []
        for spec in self.specs():
            if not self.is_enabled(spec, overrides):
                continue
            if mode is not None and mode not in spec.modes:
                continue
            row = health.get(spec.id)
            if row is not None and row["state"] == HealthState.BLOCKED.value:
                continue
            out.append((spec, self.adapter(spec)))
        return out
