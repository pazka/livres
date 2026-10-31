"""Source registry: source specs (sources/*.json, plus OPDS sources added from the admin page) -> adapters.

Adding a source KIND = a new module here + an entry in KINDS + fixtures/tests (AGENTS.md, P1b).
Adding a SOURCE of an existing kind = one JSON file in sources/ + a fixture (AGENTS.md, P1a).
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path

from ..domain import Canary, Mode, SourceSpec
from ..http import HttpClient, host_allowed
from .base import SourceAdapter, SourceError
from .beq import BeqHtmlAdapter
from .gutenberg import GutenbergCsvAdapter
from .opds1 import Opds1Adapter
from .opds2 import Opds2Adapter

KINDS: dict[str, type[SourceAdapter]] = {
    "opds1": Opds1Adapter,
    "opds2": Opds2Adapter,
    "gutenberg_csv": GutenbergCsvAdapter,
    "beq_html": BeqHtmlAdapter,
}

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
URL_KEYS = ("search_url", "root_url", "harvest_url", "catalog_url", "lifespans_url")


class SpecError(ValueError):
    pass


def validate(raw: dict) -> list[str]:
    """Return the list of problems in a raw spec (empty list = valid). Used by tests, admin and CLI."""
    errors = []
    for key in ("id", "name", "kind", "homepage", "legal", "modes", "config", "allowed_hosts", "canary"):
        if key not in raw:
            errors.append(f"missing '{key}'")
    if errors:
        return errors
    if not ID_RE.match(str(raw["id"])):
        errors.append("id must be lowercase letters, digits and dashes")
    if raw["kind"] not in KINDS:
        errors.append(f"unknown kind '{raw['kind']}' (known: {', '.join(sorted(KINDS))})")
    if len(str(raw["legal"]).strip()) < 20:
        errors.append("'legal' must explain in a sentence why the books are legal and free")
    try:
        modes = [Mode(m) for m in raw["modes"]]
        if not modes:
            errors.append("at least one mode is needed")
    except ValueError:
        errors.append("modes must be 'search' and/or 'harvest'")
        modes = []
    config = raw["config"] if isinstance(raw["config"], dict) else {}
    if not isinstance(raw["config"], dict):
        errors.append("config must be an object")
    hosts = tuple(raw["allowed_hosts"]) if isinstance(raw["allowed_hosts"], list) else ()
    if not hosts:
        errors.append("allowed_hosts must list every host the source uses")
    for key in URL_KEYS:
        url = config.get(key)
        if url is None:
            continue
        if urllib.parse.urlsplit(url).scheme != "https":
            errors.append(f"config.{key} must be https")
        if hosts and not host_allowed(url.replace("{", "").replace("}", ""), hosts):
            errors.append(f"config.{key} host is not in allowed_hosts")
    pages = config.get("pages", [])
    if not isinstance(pages, list):
        errors.append("config.pages must be a list")
        pages = []
    for page in pages:
        url = page if isinstance(page, str) else (page.get("url", "") if isinstance(page, dict) else "")
        if urllib.parse.urlsplit(url).scheme != "https":
            errors.append(f"config.pages: {url!r} must be an https URL")
        elif hosts and not host_allowed(url, hosts):
            errors.append(f"config.pages: host of {url} is not in allowed_hosts")
    kind = KINDS.get(raw["kind"])
    if kind:
        for key in kind.required_config:
            if key not in config:
                errors.append(f"config.{key} is required for kind {raw['kind']}")
    if Mode.SEARCH in modes and raw["kind"] == "opds1" and not (config.get("search_url") or config.get("root_url")):
        errors.append("an opds1 search source needs config.search_url or config.root_url")
    if Mode.SEARCH in modes and raw["kind"] == "opds2" and not config.get("search_url"):
        errors.append("an opds2 search source needs config.search_url")
    if Mode.HARVEST in modes and raw["kind"] in ("opds1", "opds2") and not config.get("harvest_url"):
        errors.append("a harvest source needs config.harvest_url")
    canary = raw["canary"] if isinstance(raw["canary"], dict) else {}
    if not str(canary.get("query", "")).strip():
        errors.append("canary.query is required (a search that must always find a book)")
    if raw.get("status", "verified") not in ("verified", "candidate"):
        errors.append("status must be 'verified' or 'candidate'")
    return errors


def spec_from_dict(raw: dict, origin: str = "repo") -> SourceSpec:
    problems = validate(raw)
    if problems:
        raise SpecError(f"source {raw.get('id', '?')}: " + "; ".join(problems))
    canary = raw["canary"]
    return SourceSpec(
        id=raw["id"],
        name=raw["name"],
        kind=raw["kind"],
        homepage=raw["homepage"],
        legal=raw["legal"],
        modes=tuple(Mode(m) for m in raw["modes"]),
        config=dict(raw["config"]),
        allowed_hosts=tuple(h.lower() for h in raw["allowed_hosts"]),
        canary=Canary(
            query=canary["query"],
            min_results=int(canary.get("min_results", 1)),
            check_download=bool(canary.get("check_download", True)),
        ),
        enabled=bool(raw.get("enabled", True)),
        priority=int(raw.get("priority", 50)),
        status=raw.get("status", "verified"),
        notes=raw.get("notes", ""),
        origin=origin,
    )


def load_specs(directory: Path) -> list[SourceSpec]:
    specs = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise SpecError(f"{path.name}: invalid JSON: {e}") from e
        spec = spec_from_dict(raw)
        if spec.id != path.stem:
            raise SpecError(f"{path.name}: the file name must be the source id ('{spec.id}.json')")
        specs.append(spec)
    ids = [s.id for s in specs]
    if len(ids) != len(set(ids)):
        raise SpecError("duplicate source ids")
    return specs


def build_adapter(spec: SourceSpec, http: HttpClient) -> SourceAdapter:
    return KINDS[spec.kind](spec, http)


__all__ = ["KINDS", "SourceAdapter", "SourceError", "SpecError", "build_adapter", "load_specs", "spec_from_dict", "validate"]
