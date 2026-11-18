"""Composition root: wires settings, store, sources, services and the HTTP server; runs the scheduler."""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from dataclasses import dataclass

from . import __version__
from .bookrequests import RequestService
from .catalog import Catalog
from .config import Settings
from .health import HealthMonitor
from .http import HttpClient, PoliteHttpClient
from .logs import JsonFormatter, setup_logging  # noqa: F401 - JsonFormatter re-exported
from .mail import Mailer
from .search import SearchService
from .sources import load_specs
from .store import Store
from .web.app import App
from .web.auth import Sessions, load_or_create_key
from .web.server import make_server


@dataclass
class Services:
    settings: Settings
    store: Store
    catalog: Catalog
    search: SearchService
    monitor: HealthMonitor
    mailer: Mailer
    requests: RequestService
    app: App


def build(settings: Settings, http: HttpClient | None = None, mail_transport=None) -> Services:
    store = Store(settings.db_path)
    http = http or PoliteHttpClient(settings.user_agent, settings.http_timeout, settings.min_host_interval)
    catalog = Catalog(load_specs(settings.sources_dir), store, http)
    mailer = Mailer(settings, store, mail_transport)
    store.retry_failed_mail()  # a restart (e.g. after fixing SMTP settings) retries what could not be sent
    search = SearchService(catalog, settings.search_timeout, settings.cache_ttl)
    monitor = HealthMonitor(catalog, mailer, settings.base_url, settings.fail_threshold, settings.harvest_interval)
    monitor.catalog_changed = search.clear_cache
    requests = RequestService(store, mailer, settings.base_url, settings.requests_per_hour, settings.requests_per_day_total, settings.client_tokens)
    sessions = Sessions(load_or_create_key(settings.secrets_dir))
    app = App(settings, catalog, search, monitor, requests, mailer, sessions)
    return Services(settings, store, catalog, search, monitor, mailer, requests, app)


class Scheduler(threading.Thread):
    """One background thread: harvest due catalogues, run health checks, retry the mail outbox."""

    def __init__(self, services: Services, stop: threading.Event):
        super().__init__(name="scheduler", daemon=True)
        self.sv = services
        self.stop = stop

    def run(self):
        log = logging.getLogger("livres.scheduler")
        next_health = time.time() + 60  # let the server settle first
        while not self.stop.is_set():
            try:
                self.sv.monitor.harvest_due()
                if time.time() >= next_health:
                    run_health_checks(self.sv.monitor)
                    next_health = time.time() + self.sv.settings.health_interval
                self.sv.mailer.flush()
            except Exception:
                log.exception(json.dumps({"event": "scheduler_failed", "msg": "scheduler tick failed"}))
            self.stop.wait(60)


def run_health_checks(monitor: HealthMonitor) -> list:
    """The periodic health check job: one line at start, one at the end with dur_ms and counts."""
    log = logging.getLogger("livres.scheduler")
    started = time.monotonic()
    log.info(json.dumps({"event": "health_check_start", "msg": "health check of every enabled source"}))
    results = monitor.check_all()
    ok = sum(1 for r in results if r.ok)
    log.info(json.dumps({"event": "health_check_done", "msg": f"{ok}/{len(results)} sources ok",
                         "dur_ms": int((time.monotonic() - started) * 1000), "sources": len(results), "ok": ok}))
    return results


def _data_dir_writable(path) -> bool:
    probe = path / f".write-probe-{os.getpid()}"
    try:
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def serve(services: Services, server) -> None:
    """Runs the HTTP server until it is shut down, between a `startup` and a `shutdown` line."""
    log = logging.getLogger("livres")
    settings = services.settings
    log.info(json.dumps({
        "event": "startup", "msg": "livres started", "version": __version__, "port": server.server_port,
        "data_dir_writable": _data_dir_writable(settings.data_dir),
        "sources": [s.id for s in services.catalog.specs()], "mail": settings.mail_configured,
        "config": sorted(k for k in os.environ if k.startswith(("LIVRES_", "APP_")) or k == "LOG_LEVEL"),
    }))
    try:
        server.serve_forever()
    finally:
        log.info(json.dumps({"event": "shutdown", "msg": "livres stopping"}))


def main() -> None:
    setup_logging()
    settings = Settings.from_env()
    services = build(settings)
    stop = threading.Event()
    if settings.scheduler:
        Scheduler(services, stop).start()
    server = make_server(services.app, settings.host, settings.port)

    def shutdown(*_):
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    serve(services, server)


if __name__ == "__main__":
    main()
