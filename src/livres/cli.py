"""Command line for agents, CI and the box.

  python -m livres.cli validate                  validate every sources/*.json (CI, agents)
  python -m livres.cli check [--source ID] [--include-candidates] [--json]
                                                 run the canaries LIVE against the real sites
  python -m livres.cli smoke http://127.0.0.1:8000   capability smoke (hosh-deploy POST_DEPLOY)
  python -m livres.cli healthcheck               docker healthcheck (GET /healthz)
  python -m livres.cli hash-password             read a password on stdin, print its scrypt hash
  python -m livres.cli new-token                 print a random API token and its sha256
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import secrets
import sys
import tempfile
import urllib.request
from dataclasses import replace
from pathlib import Path

from .config import Settings
from .domain import Mode
from .sources import SpecError, load_specs


def _sources_dir(args) -> Path:
    if getattr(args, "sources", None):
        return Path(args.sources)
    env = os.environ.get("LIVRES_SOURCES_DIR")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parents[2] / "sources"
    return here if here.exists() else Path("/app/sources")


def cmd_validate(args) -> int:
    try:
        specs = load_specs(_sources_dir(args))
    except SpecError as e:
        print(f"INVALID: {e}")
        return 1
    for s in specs:
        print(f"ok  {s.id:<20} {s.kind:<14} {'enabled' if s.enabled else 'disabled':<8} {s.status}")
    return 0


def cmd_check(args) -> int:
    from .main import build

    with tempfile.TemporaryDirectory() as tmp:
        settings = replace(Settings.from_env({}), data_dir=Path(tmp), sources_dir=_sources_dir(args), scheduler=False)
        sv = build(settings)
        report = []
        for spec in sv.catalog.specs():
            if args.source and spec.id != args.source:
                continue
            if not args.source and not spec.enabled and not (args.include_candidates and spec.status == "candidate"):
                continue
            harvest = None
            if Mode.HARVEST in spec.modes:
                harvest = sv.monitor.harvest(spec)
            result = sv.monitor.run_check(spec)
            report.append({
                "id": spec.id, "name": spec.name, "enabled": spec.enabled, "status": spec.status,
                "ok": result.ok, "blocked": result.blocked, "detail": result.detail,
                "results": result.results, "ms": result.duration_ms,
                "harvest": None if harvest is None else {"ok": harvest[0], "detail": harvest[1]},
            })
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for r in report:
            print(f"{'OK ' if r['ok'] else 'FAIL'} {r['id']:<20} {r['detail']}")
    failing = [r for r in report if not r["ok"] and (r["enabled"] or args.source)]
    return 1 if failing else 0


def _get_json(url: str):
    with urllib.request.urlopen(url, timeout=15) as resp:
        if resp.status != 200:
            raise AssertionError(f"{url}: HTTP {resp.status}")
        return json.loads(resp.read())


def cmd_smoke(args) -> int:
    """Capability, not a 200 (box Decision #14). Never sends a book request (it would e-mail the owner)."""
    base = args.base.rstrip("/")
    checks = []
    try:
        health = _get_json(base + "/healthz")
        assert health["status"] == "ok" and health["db"] == "ok", health
        checks.append(f"healthz ok, {health['books_indexed']} books indexed, {health['sources']['enabled']} sources enabled")
        catalog = _get_json(base + "/opds/v2/catalog.json")
        assert any(l.get("rel") == "search" and l.get("templated") for l in catalog["links"]), "no search link"
        assert catalog["navigation"], "empty navigation"
        checks.append("OPDS catalogue has a templated search link and navigation")
        feed = _get_json(base + "/opds/v2/search?query=hugo")
        assert isinstance(feed["publications"], list) and "metadata" in feed, "bad search feed"
        checks.append(f"OPDS search answers ({len(feed['publications'])} publications for 'hugo')")
        hosts = _get_json(base + "/api/v1/download-hosts")
        assert isinstance(hosts["hosts"], list), "bad download-hosts"
        checks.append(f"download-hosts lists {len(hosts['hosts'])} hosts")
        status = _get_json(base + "/api/v1/status")
        assert isinstance(status["sources"], list)
        checks.append("status lists the sources")
    except Exception as e:  # noqa: BLE001 - report any failure as a red smoke
        print(f"SMOKE FAIL: {e}")
        return 1
    for c in checks:
        print("ok ", c)
    return 0


def cmd_healthcheck(args) -> int:
    try:
        data = _get_json(f"http://127.0.0.1:{os.environ.get('LIVRES_PORT', '8000')}/healthz")
        return 0 if data.get("status") == "ok" else 1
    except Exception:
        return 1


def cmd_hash_password(args) -> int:
    from .web.auth import hash_password

    password = getpass.getpass("Admin password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
    if len(password) < 12:
        print("password too short (12 characters minimum)", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


def cmd_new_token(args) -> int:
    token = secrets.token_urlsafe(32)
    print(json.dumps({"token": token, "sha256": hashlib.sha256(token.encode()).hexdigest()}))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="livres")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("validate")
    p.add_argument("--sources")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("check")
    p.add_argument("--sources")
    p.add_argument("--source")
    p.add_argument("--include-candidates", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("smoke")
    p.add_argument("base")
    p.set_defaults(fn=cmd_smoke)
    sub.add_parser("healthcheck").set_defaults(fn=cmd_healthcheck)
    sub.add_parser("hash-password").set_defaults(fn=cmd_hash_password)
    sub.add_parser("new-token").set_defaults(fn=cmd_new_token)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
