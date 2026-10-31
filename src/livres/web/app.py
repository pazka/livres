"""HTTP layer: a tiny router over the services. No business logic here, only parsing, auth and rendering."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from http.cookies import SimpleCookie

from .. import __version__
from ..bookrequests import RequestError, RequestService
from ..catalog import Catalog
from ..config import Settings
from ..domain import HealthState, Mode
from ..health import HealthMonitor
from ..mail import Mailer
from ..search import QueryError, SearchService
from ..sources import KINDS, spec_from_dict, validate
from . import opds, pages
from .auth import LoginThrottle, Sessions, token_matches, verify_password

log = logging.getLogger("livres.web")

MAX_BODY = 64 * 1024
COOKIE = "livres_session"
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes
    client_ip: str
    cookies: dict[str, str] = field(default_factory=dict)

    def json(self):
        try:
            return json.loads(self.body or b"null")
        except ValueError:
            return None

    def form(self) -> dict[str, str]:
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.body.decode("utf-8", "replace")).items()}

    def bearer(self) -> str | None:
        auth = self.headers.get("authorization", "")
        return auth[7:].strip() if auth.lower().startswith("bearer ") else None


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    content_type: str = "text/plain; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, data, status=200, content_type="application/json"):
        return cls(status, json.dumps(data, ensure_ascii=False, indent=None).encode(), content_type + "; charset=utf-8")

    @classmethod
    def html(cls, text: str, status=200):
        return cls(status, text.encode(), "text/html; charset=utf-8")

    @classmethod
    def redirect(cls, location: str):
        return cls(303, b"", headers={"Location": location})


def client_ip(peer: str, xff: str | None, trusted) -> str:
    """Box contract rule 7: X-Forwarded-For is believed only when the peer is a trusted proxy."""
    def is_trusted(ip):
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in trusted)

    if not xff or not is_trusted(peer):
        return peer
    for hop in reversed([h.strip() for h in xff.split(",") if h.strip()]):
        if not is_trusted(hop):
            return hop
    return peer


class App:
    def __init__(self, settings: Settings, catalog: Catalog, search: SearchService, monitor: HealthMonitor,
                 requests: RequestService, mailer: Mailer, sessions: Sessions):
        self.s = settings
        self.catalog = catalog
        self.store = catalog.store
        self.search = search
        self.monitor = monitor
        self.requests = requests
        self.mailer = mailer
        self.sessions = sessions
        self.throttle = LoginThrottle()
        self.routes = [
            ("GET", r"/healthz", self.healthz),
            ("GET", r"/robots\.txt", self.robots),
            ("GET", r"/robots-info", self.robots_info),
            ("GET", r"/static/style\.css", self.css),
            ("GET", r"/", self.home),
            ("GET", r"/recherche", self.home_search),
            ("POST", r"/demande", self.home_request),
            ("GET", r"/opds/v2/catalog\.json", self.opds_catalog),
            ("GET", r"/opds/v2/search", self.opds_search),
            ("GET", r"/api/v1/download-hosts", self.download_hosts),
            ("GET", r"/api/v1/status", self.public_status),
            ("POST", r"/api/v1/requests", self.api_request),
            ("GET", r"/admin/login", self.login_form),
            ("POST", r"/admin/login", self.login),
            ("POST", r"/admin/logout", self.admin(self.logout)),
            ("GET", r"/admin", self.admin(self.admin_home)),
            ("POST", r"/admin/sources", self.admin(self.admin_add_source)),
            ("POST", r"/admin/sources/(?P<sid>[a-z0-9-]+)/(?P<action>enable|disable|reset|check|harvest|delete)", self.admin(self.admin_source_action)),
            ("GET", r"/admin/requests", self.admin(self.admin_requests)),
            ("POST", r"/admin/requests/(?P<rid>\d+)/status", self.admin(self.admin_request_status)),
            ("GET", r"/admin/events", self.admin(self.admin_events)),
            ("GET", r"/api/v1/admin/sources", self.agent(self.api_sources)),
            ("POST", r"/api/v1/admin/sources", self.agent(self.api_add_source)),
            ("POST", r"/api/v1/admin/validate", self.agent(self.api_validate)),
            ("POST", r"/api/v1/admin/sources/(?P<sid>[a-z0-9-]+)/(?P<action>enable|disable|reset|check|harvest)", self.agent(self.api_source_action)),
            ("DELETE", r"/api/v1/admin/sources/(?P<sid>[a-z0-9-]+)", self.agent(self.api_delete_source)),
            ("GET", r"/api/v1/admin/requests", self.agent(self.api_requests)),
        ]
        self._compiled = [(m, re.compile(p + r"\Z"), h) for m, p, h in self.routes]

    # -- dispatch -------------------------------------------------------------------------------
    def handle(self, req: Request) -> Response:
        started = time.monotonic()
        try:
            resp = self._dispatch(req)
        except Exception:
            log.exception("unhandled error")
            resp = Response.json({"error": "Une erreur est survenue sur le service. Réessayez dans un moment."}, 500)
        for k, v in SECURITY_HEADERS.items():
            resp.headers.setdefault(k, v)
        if req.path.startswith(("/admin", "/api/v1/admin")):
            resp.headers.setdefault("Cache-Control", "no-store")
        log.info(json.dumps({
            "event": "http", "method": req.method, "path": req.path, "status": resp.status,
            "ms": int((time.monotonic() - started) * 1000),
            "client": hashlib.sha256(req.client_ip.encode()).hexdigest()[:12],
        }))
        return resp

    def _dispatch(self, req: Request) -> Response:
        if any(part.startswith(".") for part in req.path.split("/")):
            return Response(404, b"not found")
        method = "GET" if req.method == "HEAD" else req.method
        allowed = False
        for m, pattern, handler in self._compiled:
            match = pattern.match(req.path)
            if match:
                if m == method:
                    return handler(req, **match.groupdict())
                allowed = True
        return Response(405, b"method not allowed") if allowed else Response(404, b"not found")

    # -- decorators -----------------------------------------------------------------------------
    def admin(self, handler):
        def wrapped(req: Request, **kw):
            session = self.sessions.read(req.cookies.get(COOKIE))
            if session is None:
                return Response.redirect("/admin/login") if req.method == "GET" else Response(403, b"session expired")
            if req.method == "POST" and req.form().get("csrf") != session["csrf"]:
                return Response(403, b"bad csrf token")
            return handler(req, session=session, **kw)
        return wrapped

    def agent(self, handler):
        def wrapped(req: Request, **kw):
            if not token_matches(req.bearer(), self.s.admin_token_sha256):
                return Response.json({"error": "unauthorized"}, 401)
            return handler(req, **kw)
        return wrapped

    # -- public ---------------------------------------------------------------------------------
    def healthz(self, req):
        counts = self.store.ping()  # proves the database answers
        specs = self.catalog.specs()
        overrides = self.store.overrides()
        health = self.store.all_health()
        enabled = [s for s in specs if self.catalog.is_enabled(s, overrides)]
        down = [s.id for s in enabled if health.get(s.id) is not None and health[s.id]["state"] in ("down", "blocked")]
        return Response.json({
            "status": "ok",
            "version": __version__,
            "db": "ok",
            "books_indexed": counts["books"],
            "sources": {"enabled": len(enabled), "down": down},
            "mail": "configured" if self.mailer.configured else "not_configured",
        })

    def robots(self, req):
        return Response(200, b"User-agent: *\nDisallow: /admin\nDisallow: /api/\nDisallow: /recherche\n")

    def robots_info(self, req):
        return Response.html(pages.layout("LivresHoshBot", (
            "<h1>LivresHoshBot</h1><p>This robot belongs to a private, non-commercial service that helps one elderly reader "
            "find public-domain and officially free French ebooks. It honours robots.txt, sends at most one request per second "
            "per site, caches results, and only links to your downloads (it never re-hosts books).</p>"
            "<p>To ask us to stop, block <code>LivresHoshBot</code> in robots.txt: the service picks it up within 24 hours.</p>"
        )))

    def css(self, req):
        resp = Response(200, pages.CSS.encode(), "text/css; charset=utf-8")
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp

    def home(self, req):
        return Response.html(pages.home())

    def home_search(self, req):
        q = req.query.get("q", "")
        try:
            result = self.search.search(q, _page(req))
        except QueryError as e:
            return Response.html(pages.home(query=q, message=str(e)))
        return Response.html(pages.home(result, q))

    def home_request(self, req):
        try:
            self.requests.submit(req.form(), None, req.client_ip)
            return Response.html(pages.home(message="Merci ! Votre demande a été envoyée."))
        except RequestError as e:
            return Response.html(pages.home(message=str(e)), e.status)

    def opds_catalog(self, req):
        return Response.json(opds.catalog_feed(self.s.base_url), content_type=opds.OPDS_JSON)

    def opds_search(self, req):
        try:
            result = self.search.search(req.query.get("query", ""), _page(req))
        except QueryError as e:
            return Response.json({"error": str(e)}, 400)
        sources = {s.id: s for s in self.catalog.specs()}
        return Response.json(opds.search_feed(self.s.base_url, result, sources), content_type=opds.OPDS_JSON)

    def download_hosts(self, req):
        hosts = sorted({h for spec, _ in self.catalog.active() for h in spec.allowed_hosts})
        return Response.json({"hosts": hosts})

    def public_status(self, req):
        return Response.json({"version": __version__, "sources": self._source_rows(public=True)})

    def api_request(self, req):
        payload = req.json()
        try:
            rid = self.requests.submit(payload, req.bearer(), req.client_ip)
        except RequestError as e:
            return Response.json({"error": str(e)}, e.status)
        return Response.json({"id": rid, "status": "sent"}, 202)

    # -- admin (owner, browser) -----------------------------------------------------------------
    def login_form(self, req):
        return Response.html(pages.login())

    def login(self, req):
        client = req.client_ip
        if self.throttle.blocked(client):
            return Response.html(pages.login("Trop d’essais. Réessayez dans 15 minutes."), 429)
        if not self.s.admin_password_hash or not verify_password(req.form().get("password", ""), self.s.admin_password_hash):
            self.throttle.fail(client)
            return Response.html(pages.login("Mot de passe incorrect."), 403)
        self.throttle.reset(client)
        cookie, _ = self.sessions.issue()
        resp = Response.redirect("/admin")
        resp.headers["Set-Cookie"] = f"{COOKIE}={cookie}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=43200"
        self.store.audit("owner", "login")
        return resp

    def logout(self, req, session):
        resp = Response.redirect("/admin/login")
        resp.headers["Set-Cookie"] = f"{COOKIE}=; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=0"
        return resp

    def admin_home(self, req, session, flash=""):
        return Response.html(pages.admin_sources(self._source_rows(), session["csrf"], flash or req.query.get("msg", "")))

    def admin_source_action(self, req, session, sid, action):
        ok, message = self._source_action(sid, action, req.form().get("reason", "admin page"), "owner")
        return Response.redirect("/admin?msg=" + urllib.parse.quote(message))

    def admin_add_source(self, req, session):
        f = req.form()
        raw = admin_form_to_spec(f)
        ok, message = self._add_source(raw, "owner")
        return Response.redirect("/admin?msg=" + urllib.parse.quote(message))

    def admin_requests(self, req, session):
        return Response.html(pages.admin_requests(self.store.list_requests(), session["csrf"]))

    def admin_request_status(self, req, session, rid):
        status = req.form().get("status", "")
        if status in ("new", "found", "not_found", "done"):
            self.store.set_request_status(int(rid), status)
            self.store.audit("owner", "request_status", f"{rid} -> {status}")
        return Response.redirect("/admin/requests")

    def admin_events(self, req, session):
        return Response.html(pages.admin_events(self.store.recent_events(), self.store.mail_stats(), session["csrf"]))

    # -- agent API (bearer token) ---------------------------------------------------------------
    def api_sources(self, req):
        return Response.json({"sources": self._source_rows()})

    def api_source_action(self, req, sid, action):
        body = req.json() or {}
        ok, message = self._source_action(sid, action, str(body.get("reason", ""))[:300] or "agent API", "agent")
        status = 200 if ok else (404 if message.startswith("unknown source") else 409)
        return Response.json({"ok": ok, "message": message, "source": self._row_for(sid)}, status)

    def api_add_source(self, req):
        raw = req.json()
        if not isinstance(raw, dict):
            return Response.json({"ok": False, "message": "JSON object expected"}, 400)
        ok, message = self._add_source(raw, "agent")
        return Response.json({"ok": ok, "message": message}, 201 if ok else 422)

    def api_validate(self, req):
        raw = req.json()
        if not isinstance(raw, dict):
            return Response.json({"ok": False, "errors": ["JSON object expected"]}, 400)
        raw = runtime_defaults(raw)
        errors = validate(raw)
        if errors:
            return Response.json({"ok": False, "errors": errors}, 422)
        result = self.monitor.run_check(spec_from_dict(raw, origin="admin"))
        return Response.json({"ok": result.ok, "errors": [], "check": result.__dict__}, 200 if result.ok else 422)

    def api_delete_source(self, req, sid):
        ok, message = self._source_action(sid, "delete", "agent API", "agent")
        return Response.json({"ok": ok, "message": message}, 200 if ok else 409)

    def api_requests(self, req):
        return Response.json({"requests": [dict(r) for r in self.store.list_requests()]})

    # -- shared operations ----------------------------------------------------------------------
    def _source_action(self, sid: str, action: str, reason: str, actor: str) -> tuple[bool, str]:
        spec = self.catalog.get(sid)
        if spec is None:
            return False, f"unknown source '{sid}'"
        if action == "enable":
            self.store.set_override(sid, True, reason, actor)
            if (self.store.health(sid) or {"state": None})["state"] == HealthState.BLOCKED.value:
                self.store.save_health(sid, HealthState.UNKNOWN, 0, "re-enabled, waiting for a check", 0, False)
        elif action == "disable":
            self.store.set_override(sid, False, reason, actor)
        elif action == "reset":
            self.store.clear_override(sid)
        elif action == "check":
            result = self.monitor.check(spec)
            self.store.audit(actor, "check", f"{sid}: {result.detail}")
            return result.ok, f"{spec.name} : {'OK' if result.ok else 'ÉCHEC'} — {result.detail}"
        elif action == "harvest":
            if Mode.HARVEST not in spec.modes:
                return False, f"{sid} is not a harvested source"
            ok, detail = self.monitor.harvest(spec)
            self.store.audit(actor, "harvest", f"{sid}: {detail}")
            return ok, f"{spec.name} : {detail}"
        elif action == "delete":
            if spec.origin != "admin":
                return False, f"{sid} comes from the repository: remove sources/{sid}.json with a PR (AGENTS.md P3)"
            self.store.delete_admin_source(sid)
        self.search.clear_cache()
        self.store.audit(actor, action, f"{sid}: {reason}")
        return True, f"{spec.name} : {action} OK"

    def _add_source(self, raw: dict, actor: str) -> tuple[bool, str]:
        if raw.get("kind") not in ("opds1", "opds2"):
            return False, "only OPDS sources can be added at runtime; other kinds need code (AGENTS.md P1b)"
        raw = runtime_defaults(raw)
        errors = validate(raw)
        if errors:
            return False, "; ".join(errors)
        if raw["id"] in self.catalog.repo_specs:
            return False, f"'{raw['id']}' already exists in the repository"
        spec = spec_from_dict(raw, origin="admin")
        result = self.monitor.run_check(spec)
        if not result.ok:
            return False, f"not added: the test failed — {result.detail}"
        self.store.save_admin_source(raw)
        self.monitor.record(spec, result)
        self.search.clear_cache()
        self.store.audit(actor, "add_source", f"{raw['id']}: {result.detail}")
        return True, f"{raw['name']} ajoutée — {result.detail}"

    def _row_for(self, sid):
        return next((r for r in self._source_rows() if r["id"] == sid), None)

    def _source_rows(self, public: bool = False) -> list[dict]:
        overrides = self.store.overrides()
        health = self.store.all_health()
        rows = []
        for spec in self.catalog.specs():
            h = health.get(spec.id)
            row = {
                "id": spec.id,
                "name": spec.name,
                "enabled": self.catalog.is_enabled(spec, overrides),
                "state": h["state"] if h else HealthState.UNKNOWN.value,
                "last_check": h["last_check"] if h else None,
                "last_ok": h["last_ok"] if h else None,
                "detail": h["detail"] if h else "",
                "kind": spec.kind,
                "modes": [m.value for m in spec.modes],
                "origin": spec.origin,
                "status": spec.status,
                "homepage": spec.homepage,
            }
            if not public:
                row.update({
                    "legal": spec.legal,
                    "override": overrides.get(spec.id),
                    "failures": h["failures"] if h else 0,
                    "books": self.store.count_books(spec.id) if Mode.HARVEST in spec.modes else 0,
                    "allowed_hosts": list(spec.allowed_hosts),
                })
            rows.append(row)
        return rows


def runtime_defaults(raw: dict) -> dict:
    """Sources added at runtime are live-search OPDS candidates unless said otherwise."""
    return {"modes": ["search"], "priority": 60, "status": "candidate", **raw}


def admin_form_to_spec(f: dict) -> dict:
    url = f.get("url", "").strip()
    kind = f.get("kind", "opds1")
    host = urllib.parse.urlsplit(url).hostname or ""
    config = {"search_url": url} if "{searchTerms}" in url or "{?query}" in url else {"root_url": url}
    return {
        "id": f.get("id", "").strip(),
        "name": f.get("name", "").strip(),
        "kind": kind if kind in KINDS else "opds1",
        "homepage": f"https://{host}/" if host else url,
        "legal": f.get("legal", "").strip(),
        "modes": ["search"],
        "config": config,
        "allowed_hosts": [host] if host else [],
        "canary": {"query": f.get("canary", "").strip() or "Victor Hugo"},
    }


def _page(req: Request) -> int:
    try:
        return max(1, min(50, int(req.query.get("page", "1"))))
    except ValueError:
        return 1


def parse_cookies(header: str | None) -> dict[str, str]:
    jar = SimpleCookie()
    try:
        jar.load(header or "")
    except Exception:
        return {}
    return {k: v.value for k, v in jar.items()}
