"""End-to-end over a real HTTP server: public API, OPDS, requests, admin page (login, CSRF), agent API."""

import hashlib
import http.client
import json
import threading
import unittest
import urllib.parse
from dataclasses import replace

from helpers import FakeHttp, FakeTransport, TempDir, fixture, make_settings

from livres.main import build
from livres.web.app import client_ip
from livres.web.auth import hash_password
from livres.web.server import make_server

EG_SEARCH = "https://www.ebooksgratuits.com/opds/feed.php?mode=search&query={}"
PASSWORD = "correct horse battery staple"
TOKEN = "agent-token-for-tests"


def routes_for(*queries):
    return {EG_SEARCH.format(urllib.parse.quote(q)): fixture("ebooksgratuits/search.xml") for q in queries}


class Server:
    def __init__(self, tmp, routes=None, **settings):
        s = make_settings(
            tmp,
            admin_password_hash=hash_password(PASSWORD),
            admin_token_sha256=hashlib.sha256(TOKEN.encode()).hexdigest(),
            **settings,
        )
        self.http = FakeHttp(routes or {})
        self.transport = FakeTransport()
        self.sv = build(s, http=self.http, mail_transport=self.transport)
        orig = self.sv.mailer.notify_owner
        self.sv.mailer.notify_owner = lambda subject, body, background=False: orig(subject, body, background=False)
        self.server = make_server(self.sv.app, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_port

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
            headers = {"Content-Type": "application/json", **(headers or {})}
        elif isinstance(body, str):
            body = body.encode()
            headers = {"Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data

    def json(self, method, path, body=None, headers=None):
        status, h, data = self.request(method, path, body, headers)
        return status, json.loads(data) if data else None

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class PublicApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.srv = Server(self._tmp.__enter__(), routes_for("maupassant", "Maupassant"))

    def tearDown(self):
        self.srv.close()
        self._tmp.__exit__(None, None, None)

    def test_healthz_proves_capability(self):
        status, body = self.srv.json("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["db"], "ok")
        self.assertEqual(body["mail"], "configured")
        self.assertGreaterEqual(body["sources"]["enabled"], 3)

    def test_head_works_like_get(self):
        status, headers, data = self.srv.request("HEAD", "/healthz")
        self.assertEqual((status, data), (200, b""))
        self.assertGreater(int(headers["content-length"]), 0)

    def test_opds_catalog(self):
        status, headers, data = self.srv.request("GET", "/opds/v2/catalog.json")
        self.assertEqual(status, 200)
        self.assertTrue(headers["content-type"].startswith("application/opds+json"))
        feed = json.loads(data)
        search = [l for l in feed["links"] if l["rel"] == "search"][0]
        self.assertEqual(search["href"], "https://livres.test/opds/v2/search{?query}")
        self.assertTrue(search["templated"])

    def test_opds_search_returns_publications_from_sources(self):
        status, feed = self.srv.json("GET", "/opds/v2/search?query=maupassant")
        self.assertEqual(status, 200)
        titles = [p["metadata"]["title"] for p in feed["publications"]]
        self.assertIn("Bel-Ami", titles)
        bel = next(p for p in feed["publications"] if p["metadata"]["title"] == "Bel-Ami")
        self.assertEqual(bel["links"][0]["type"], "application/epub+zip")
        self.assertEqual(bel["links"][0]["properties"], {"size": 412345})
        self.assertEqual(bel["metadata"]["belongsTo"]["collection"][0]["identifier"], "ebooksgratuits")

    def test_opds_search_bad_query_is_french_400(self):
        status, body = self.srv.json("GET", "/opds/v2/search?query=a")
        self.assertEqual(status, 400)
        self.assertIn("deux lettres", body["error"])

    def test_download_hosts_lists_enabled_sources_only(self):
        status, body = self.srv.json("GET", "/api/v1/download-hosts")
        self.assertEqual(status, 200)
        self.assertIn("www.ebooksgratuits.com", body["hosts"])
        self.assertIn("www.gutenberg.org", body["hosts"])
        self.assertNotIn("gallica.bnf.fr", body["hosts"], "candidate sources are disabled")

    def test_public_status_has_no_admin_details(self):
        status, body = self.srv.json("GET", "/api/v1/status")
        self.assertEqual(status, 200)
        self.assertNotIn("allowed_hosts", body["sources"][0])
        self.assertIn("state", body["sources"][0])

    def test_book_request_round_trip(self):
        status, body = self.srv.json("POST", "/api/v1/requests", {"title": "Le Petit Prince", "contact": "m@example.fr"}, {"Authorization": "Bearer install-1"})
        self.assertEqual((status, body["status"]), (202, "sent"))
        self.assertEqual(len(self.srv.transport.sent), 1)
        status, body = self.srv.json("POST", "/api/v1/requests", {"author": "x"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "Indiquez le titre du livre que vous cherchez.")

    def test_security_headers_dotfiles_and_unknown_routes(self):
        status, headers, _ = self.srv.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'none'", headers["content-security-policy"])
        self.assertEqual(headers["x-content-type-options"], "nosniff")
        self.assertEqual(self.srv.request("GET", "/.env")[0], 404)
        self.assertEqual(self.srv.request("GET", "/.git/config")[0], 404)
        self.assertEqual(self.srv.request("GET", "/nope")[0], 404)
        self.assertEqual(self.srv.request("DELETE", "/healthz")[0], 405)

    def test_body_size_limit(self):
        status, _, _ = self.srv.request("POST", "/api/v1/requests", b"x" * (70 * 1024), {"Content-Type": "application/json"})
        self.assertEqual(status, 413)

    def test_html_search_page(self):
        status, _, data = self.srv.request("GET", "/recherche?q=maupassant")
        self.assertEqual(status, 200)
        self.assertIn("Bel-Ami", data.decode())

    def test_robots_txt_keeps_crawlers_off_search_and_admin(self):
        _, _, data = self.srv.request("GET", "/robots.txt")
        self.assertIn(b"Disallow: /admin", data)
        self.assertIn(b"Disallow: /recherche", data)


class AdminTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.srv = Server(self._tmp.__enter__(), routes_for("Maupassant"))

    def tearDown(self):
        self.srv.close()
        self._tmp.__exit__(None, None, None)

    def login(self):
        status, headers, _ = self.srv.request("POST", "/admin/login", "password=" + urllib.parse.quote(PASSWORD))
        self.assertEqual(status, 303)
        cookie = headers["set-cookie"].split(";", 1)[0]
        self.assertIn("HttpOnly", headers["set-cookie"])
        self.assertIn("SameSite=Strict", headers["set-cookie"])
        _, _, page = self.srv.request("GET", "/admin", headers={"Cookie": cookie})
        csrf = page.decode().split('name="csrf" value="', 1)[1].split('"', 1)[0]
        return cookie, csrf

    def test_admin_requires_login(self):
        status, headers, _ = self.srv.request("GET", "/admin")
        self.assertEqual((status, headers["location"]), (303, "/admin/login"))
        self.assertEqual(self.srv.request("POST", "/admin/sources/gutenberg/disable", "csrf=x")[0], 403)

    def test_wrong_password_and_throttle(self):
        for _ in range(5):
            self.assertEqual(self.srv.request("POST", "/admin/login", "password=nope")[0], 403)
        self.assertEqual(self.srv.request("POST", "/admin/login", "password=" + urllib.parse.quote(PASSWORD))[0], 429)

    def test_disable_enable_with_csrf(self):
        cookie, csrf = self.login()
        status, _, _ = self.srv.request("POST", "/admin/sources/ebooksgratuits/disable", "csrf=wrong", {"Cookie": cookie})
        self.assertEqual(status, 403)
        status, headers, _ = self.srv.request("POST", "/admin/sources/ebooksgratuits/disable", f"csrf={csrf}", {"Cookie": cookie})
        self.assertEqual(status, 303)
        self.assertEqual(self.srv.sv.store.overrides(), {"ebooksgratuits": False})
        _, feed = self.srv.json("GET", "/opds/v2/search?query=Maupassant")
        self.assertEqual(feed["publications"], [])
        self.srv.request("POST", "/admin/sources/ebooksgratuits/reset", f"csrf={csrf}", {"Cookie": cookie})
        self.assertEqual(self.srv.sv.store.overrides(), {})

    def test_tampered_cookie_is_rejected(self):
        cookie, _ = self.login()
        self.assertEqual(self.srv.request("GET", "/admin", headers={"Cookie": cookie[:-2] + "00"})[0], 303)

    def test_requests_page(self):
        self.srv.json("POST", "/api/v1/requests", {"title": "Germinal"})
        cookie, csrf = self.login()
        _, _, page = self.srv.request("GET", "/admin/requests", headers={"Cookie": cookie})
        self.assertIn("Germinal", page.decode())
        self.srv.request("POST", "/admin/requests/1/status", f"csrf={csrf}&status=found", {"Cookie": cookie})
        self.assertEqual(self.srv.sv.store.list_requests()[0]["status"], "found")


class AgentApiTests(unittest.TestCase):
    AUTH = {"Authorization": "Bearer " + TOKEN}

    def setUp(self):
        self._tmp = TempDir()
        routes = routes_for("Maupassant")
        routes["https://opds.example/search?q=Victor%20Hugo"] = fixture("ebooksgratuits/search.xml").replace(b"www.ebooksgratuits.com", b"opds.example")
        self.srv = Server(self._tmp.__enter__(), routes)

    def tearDown(self):
        self.srv.close()
        self._tmp.__exit__(None, None, None)

    def test_token_required(self):
        self.assertEqual(self.srv.json("GET", "/api/v1/admin/sources")[0], 401)
        self.assertEqual(self.srv.json("GET", "/api/v1/admin/sources", headers={"Authorization": "Bearer nope"})[0], 401)

    def test_list_check_disable(self):
        status, body = self.srv.json("GET", "/api/v1/admin/sources", headers=self.AUTH)
        self.assertEqual(status, 200)
        ids = {s["id"] for s in body["sources"]}
        self.assertTrue({"ebooksgratuits", "gutenberg", "wikisource"} <= ids)
        status, body = self.srv.json("POST", "/api/v1/admin/sources/ebooksgratuits/check", {}, self.AUTH)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["source"]["state"], "ok")
        status, body = self.srv.json("POST", "/api/v1/admin/sources/ebooksgratuits/disable", {"reason": "site moved"}, self.AUTH)
        self.assertEqual((status, body["source"]["enabled"]), (200, False))
        events = self.srv.sv.store.recent_events()
        self.assertEqual((events[0]["actor"], events[0]["action"]), ("agent", "disable"))
        self.assertEqual(self.srv.json("POST", "/api/v1/admin/sources/nope/disable", {}, self.AUTH)[0], 404)

    def test_audit_lists_every_indexed_book_of_a_source(self):
        from helpers import book
        store = self.srv.sv.store
        store.replace_source_books("gutenberg", [book(f"gutenberg:{i}", f"Livre {i:03d}", source_id="gutenberg") for i in range(503)])
        self.assertEqual(self.srv.json("GET", "/api/v1/admin/sources/gutenberg/books")[0], 401)
        status, body = self.srv.json("GET", "/api/v1/admin/sources/gutenberg/books", headers=self.AUTH)
        self.assertEqual((status, body["total"], len(body["books"])), (200, 503, 500))
        self.assertEqual(body["books"][0]["title"], "Livre 000")
        status, body = self.srv.json("GET", "/api/v1/admin/sources/gutenberg/books?offset=500", headers=self.AUTH)
        self.assertEqual([b["title"] for b in body["books"]], ["Livre 500", "Livre 501", "Livre 502"])
        self.assertEqual(self.srv.json("GET", "/api/v1/admin/sources/nope/books", headers=self.AUTH)[0], 404)

    def test_add_validate_and_delete_runtime_opds_source(self):
        raw = {
            "id": "exemple", "name": "Exemple", "kind": "opds1", "homepage": "https://opds.example/",
            "legal": "Catalogue officiel gratuit du domaine public, sans DRM.",
            "config": {"search_url": "https://opds.example/search?q={searchTerms}"},
            "allowed_hosts": ["opds.example"], "canary": {"query": "Victor Hugo"},
        }
        status, body = self.srv.json("POST", "/api/v1/admin/validate", raw, self.AUTH)
        self.assertEqual(status, 200, body)
        status, body = self.srv.json("POST", "/api/v1/admin/sources", raw, self.AUTH)
        self.assertEqual(status, 201, body)
        self.assertIn("exemple", [s["id"] for s in self.srv.json("GET", "/api/v1/admin/sources", headers=self.AUTH)[1]["sources"]])
        self.assertEqual(self.srv.json("DELETE", "/api/v1/admin/sources/exemple", headers=self.AUTH)[0], 200)
        self.assertEqual(self.srv.json("DELETE", "/api/v1/admin/sources/gutenberg", headers=self.AUTH)[0], 409, "repo sources are removed by PR")

    def test_adding_a_failing_source_is_refused(self):
        raw = {
            "id": "broken", "name": "Broken", "kind": "opds1", "homepage": "https://down.example/",
            "legal": "Catalogue officiel gratuit du domaine public, sans DRM.",
            "config": {"search_url": "https://down.example/search?q={searchTerms}"},
            "allowed_hosts": ["down.example"], "canary": {"query": "Victor Hugo"},
        }
        status, body = self.srv.json("POST", "/api/v1/admin/sources", raw, self.AUTH)
        self.assertEqual(status, 422)
        self.assertIn("test failed", body["message"])

    def test_only_opds_kinds_at_runtime(self):
        status, body = self.srv.json("POST", "/api/v1/admin/sources", {"id": "x", "kind": "gutenberg_csv"}, self.AUTH)
        self.assertEqual(status, 422)
        self.assertIn("only OPDS", body["message"])


class ClientIpTests(unittest.TestCase):
    def test_xff_trusted_only_from_proxies(self):
        trusted = make_settings("/tmp").trusted_proxies
        self.assertEqual(client_ip("172.18.0.1", "203.0.113.9", trusted), "203.0.113.9")
        self.assertEqual(client_ip("203.0.113.50", "1.1.1.1", trusted), "203.0.113.50")
        self.assertEqual(client_ip("127.0.0.1", "6.6.6.6, 203.0.113.9, 172.18.0.1", trusted), "203.0.113.9")


if __name__ == "__main__":
    unittest.main()
