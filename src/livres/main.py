"""Composition root: wires settings, store, sources, services and the HTTP server; runs the scheduler."""

from __future__ import annotations

import json
import logging
import signal
import sys
import threading
import time
from dataclasses import dataclass

from . import __version__
from .bookrequests import RequestService
from .catalog import Catalog
from .config import Settings
from .health import HealthMonitor
from .http import HttpClient, PoliteHttpClient
from .mail import Mailer
from .search import SearchService
from .sources import load_specs
from .store import Store
from .web.app import App
from .web.auth import Sessions, load_or_create_key
from .web.server import make_server


class JsonFormatter(logging.Formatter):
    def format(self, record):
        msg = record.getMessage()
        try:
            data = json.loads(msg)
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            data = {"msg": msg}
        data = {"ts": round(record.created, 3), "level": record.levelname.lower(), "logger": record.name, **data}
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


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
                    self.sv.monitor.check_all()
                    next_health = time.time() + self.sv.settings.health_interval
                self.sv.mailer.flush()
            except Exception:
                log.exception("scheduler tick failed")
            self.stop.wait(60)


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
    logging.getLogger("livres").info(json.dumps({
        "event": "start", "version": __version__, "port": settings.port,
        "sources": [s.id for s in services.catalog.specs()], "mail": settings.mail_configured,
    }))
    server.serve_forever()


if __name__ == "__main__":
    main()
