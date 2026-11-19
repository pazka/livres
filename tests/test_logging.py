"""Logging standard (hosh-management AGENTS.md section 11): one JSON object per line on stdout with ts, level,
service, version, event, msg; startup and shutdown lines; requests logged only when they matter; req_id echoed;
one error line with `err` and the route template on an unhandled exception; job lines with dur_ms and counts."""
import hashlib
import io
import json
import logging
import os
import re
import sys
import threading
import time
import unittest
from unittest import mock

from helpers import TempDir, book
from test_web import PASSWORD, Server

from livres import logs as logs_mod
from livres.main import Scheduler, run_health_checks, serve
from livres.web import app as app_mod
from livres.web.app import Response, route_template
from livres.web.server import make_server

TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
REQUIRED = {"ts", "level", "service", "version", "event", "msg"}


class LogCapture:
    """Installs the app's real logging (checked to write to stdout), then reads its JSON lines from a buffer."""

    def __init__(self, test: unittest.TestCase):
        self.buf = io.StringIO()
        root = logging.getLogger()
        saved = (root.handlers[:], root.level)
        logging.disable(logging.NOTSET)  # helpers.py silences logging for the other tests

        def restore():
            root.handlers[:] = saved[0]
            root.setLevel(saved[1])
            logging.disable(logging.CRITICAL)

        test.addCleanup(restore)
        self.install()

    def install(self):
        logs_mod.setup_logging()
        (handler,) = logging.getLogger().handlers
        assert handler.stream is sys.stdout, "log lines go to stdout"
        handler.setStream(self.buf)

    def read(self) -> list[dict]:
        lines = self.buf.getvalue().splitlines()
        self.buf.seek(0)
        self.buf.truncate()
        return [json.loads(line) for line in lines if line.strip()]


def check_format(test, rec):
    test.assertLessEqual(REQUIRED, rec.keys(), rec)
    test.assertRegex(rec["ts"], TS)
    test.assertIn(rec["level"], ("debug", "info", "warn", "error"))
    test.assertEqual(rec["service"], "livres")
    test.assertEqual(rec["version"], logs_mod.__version__)
    test.assertRegex(rec["event"], r"^[a-z][a-z0-9_]*$")


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TempDir()
        self.tmp = self._tmp.__enter__()
        self.addCleanup(self._tmp.__exit__, None, None, None)
        self.srv = Server(self.tmp)
        self.addCleanup(self.srv.close)
        self.logs = LogCapture(self)

    def http_lines(self):
        return [r for r in self.logs.read() if r["event"] == "http"]

    def test_startup_and_shutdown_lines(self):
        env = {"LIVRES_SMTP_PASSWORD": "must-not-appear", "LOG_LEVEL": "info"}
        server = make_server(self.srv.sv.app, "127.0.0.1", 0)
        self.addCleanup(server.server_close)
        with mock.patch.dict(os.environ, env):
            threading.Timer(0.3, server.shutdown).start()
            serve(self.srv.sv, server)  # returns once the server is shut down
        recs = self.logs.read()
        for r in recs:
            check_format(self, r)
        start = next(r for r in recs if r["event"] == "startup")
        self.assertEqual(start["level"], "info")
        self.assertEqual(start["port"], server.server_port)
        self.assertIs(start["data_dir_writable"], True)
        self.assertIn("LIVRES_SMTP_PASSWORD", start["config"])
        self.assertNotIn("must-not-appear", json.dumps(recs))
        self.assertGreaterEqual(len(start["sources"]), 3)
        self.assertEqual([r["event"] for r in recs][-1], "shutdown")

    def test_startup_line_reports_an_unwritable_data_dir(self):
        """A read-only or missing /data gives data_dir_writable false; serve still runs and shuts down cleanly."""
        server = make_server(self.srv.sv.app, "127.0.0.1", 0)
        self.addCleanup(server.server_close)
        with mock.patch("pathlib.Path.write_text", side_effect=PermissionError("read-only file system")):
            threading.Timer(0.3, server.shutdown).start()
            serve(self.srv.sv, server)
        recs = self.logs.read()
        for r in recs:
            check_format(self, r)
        events = [r["event"] for r in recs]
        self.assertIn("startup", events)
        self.assertEqual(events[-1], "shutdown")
        start = next(r for r in recs if r["event"] == "startup")
        self.assertIs(start["data_dir_writable"], False)

    def test_unhandled_error_logs_one_error_line_with_err(self):
        app = self.srv.sv.app

        def boom(req, n):
            raise RuntimeError("kaboom")

        pattern = r"/boom/(?P<n>\d+)"
        app._compiled.append(("GET", re.compile(pattern + r"\Z"), boom, route_template(pattern)))
        self.logs.read()
        status, headers, _ = self.srv.request("GET", "/boom/7?secret=x", headers={"X-Request-Id": "abc-123"})
        self.assertEqual(status, 500)
        self.assertEqual(headers["x-request-id"], "abc-123")
        errors = [r for r in self.logs.read() if r["level"] == "error"]
        self.assertEqual(len(errors), 1, errors)
        e = errors[0]
        check_format(self, e)
        self.assertEqual((e["event"], e["status"], e["method"]), ("http", 500, "GET"))
        self.assertEqual(e["route"], "/boom/{n}", "the route template, never the raw path")
        self.assertNotIn("secret", json.dumps(e))
        self.assertNotIn("/boom/7", json.dumps(e))
        self.assertEqual(e["req_id"], "abc-123")
        self.assertIsInstance(e["dur_ms"], int)
        self.assertEqual((e["err"]["type"], e["err"]["message"]), ("RuntimeError", "kaboom"))
        self.assertIn("in boom", e["err"]["stack"])
        self.assertTrue(e["err"]["stack"].rstrip().endswith("RuntimeError: kaboom"))

    def test_requests_logged_only_when_they_matter(self):
        self.logs.read()
        self.assertEqual(self.srv.request("GET", "/healthz")[0], 200)
        self.assertEqual(self.srv.request("GET", "/opds/v2/catalog.json")[0], 200)
        self.assertEqual(self.srv.request("GET", "/nope")[0], 404)
        self.assertEqual(self.http_lines(), [], "2xx, /healthz and public 4xx stay at debug")

        self.assertEqual(self.srv.request("POST", "/admin/login", "password=nope")[0], 403)
        self.assertEqual(self.srv.request("GET", "/api/v1/admin/sources")[0], 401)
        recs = self.http_lines()
        self.assertEqual([(r["level"], r["route"], r["status"]) for r in recs],
                         [("warn", "/admin/login", 403), ("warn", "/api/v1/admin/sources", 401)])
        for r in recs:
            check_format(self, r)
            self.assertNotIn("path", r)
            self.assertNotIn("client", r)
            self.assertNotIn("nope", json.dumps(r))

    def test_route_template_replaces_the_ids(self):
        self.srv.request("POST", "/admin/sources/gutenberg/disable", "csrf=x")  # no session: 403
        (rec,) = self.http_lines()
        self.assertEqual((rec["route"], rec["status"]), ("/admin/sources/{sid}/{action}", 403))

    def test_request_id_is_echoed_or_generated(self):
        given = self.srv.request("GET", "/healthz", headers={"X-Request-Id": "req-42"})[1]
        self.assertEqual(given["x-request-id"], "req-42")
        made = self.srv.request("GET", "/healthz", headers={"X-Request-Id": "bad id with spaces"})[1]
        self.assertRegex(made["x-request-id"], r"^[0-9a-f]{32}$")

    def test_request_id_is_capped_at_64_characters(self):
        kept = "a" * 64
        self.assertEqual(self.srv.request("GET", "/healthz", headers={"X-Request-Id": kept})[1]["x-request-id"], kept)
        made = self.srv.request("GET", "/healthz", headers={"X-Request-Id": "a" * 65})[1]["x-request-id"]
        self.assertRegex(made, r"^[0-9a-f]{32}$", "a 65-character X-Request-Id is replaced by a generated one")

    def test_log_level_debug_logs_every_request(self):
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "debug"}):
            self.logs.install()
        self.logs.read()
        self.srv.request("GET", "/healthz")
        recs = self.http_lines()
        self.assertEqual([(r["level"], r["route"], r["status"]) for r in recs], [("debug", "/healthz", 200)])

    def test_log_level_warn_drops_info_lines(self):
        with mock.patch.dict(os.environ, {"LOG_LEVEL": "warn"}):
            self.logs.install()
        logging.getLogger("livres").info(json.dumps({"event": "startup"}))
        logging.getLogger("livres").warning(json.dumps({"event": "mail_failed"}))
        self.assertEqual([r["event"] for r in self.logs.read()], ["mail_failed"])

    def test_slow_request_is_logged_at_warn(self):
        app = self.srv.sv.app
        app._compiled.append(("GET", re.compile(r"/slow\Z"), lambda req: Response(200, b"ok"), "/slow"))
        self.logs.read()
        with mock.patch.object(app_mod, "SLOW_MS", -1):
            self.assertEqual(self.srv.request("GET", "/slow")[0], 200)
        (rec,) = self.http_lines()
        self.assertEqual((rec["level"], rec["route"], rec["status"]), ("warn", "/slow", 200))

    def test_slow_threshold_is_one_second(self):
        self.assertEqual(app_mod.access_level("/x", 200, 1001), logging.WARNING)
        self.assertEqual(app_mod.access_level("/x", 200, 999), logging.DEBUG)

    def test_admin_login_still_works_and_logs_nothing_secret(self):
        self.logs.read()
        status, headers, _ = self.srv.request("POST", "/admin/login", "password=" + PASSWORD.replace(" ", "+"))
        self.assertEqual(status, 303)
        self.assertNotIn(PASSWORD, json.dumps(self.logs.read()))

    def test_health_check_job_lines(self):
        self.logs.read()
        results = run_health_checks(self.srv.sv.monitor)
        recs = [r for r in self.logs.read() if r["event"].startswith("health_check_")]
        self.assertEqual([r["event"] for r in recs], ["health_check_start", "health_check_done"])
        done = recs[1]
        check_format(self, done)
        self.assertEqual(done["sources"], len(results))
        self.assertEqual(done["ok"], sum(1 for r in results if r.ok))
        self.assertIsInstance(done["dur_ms"], int)

    def test_source_check_lines(self):
        self.logs.read()
        results = run_health_checks(self.srv.sv.monitor)
        checks = [r for r in self.logs.read() if r["event"] == "source_check"]
        self.assertEqual(len(checks), len(results))
        self.assertGreaterEqual(len(checks), 1)
        for rec in checks:
            check_format(self, rec)
            self.assertIsInstance(rec["source"], str)
            self.assertIsInstance(rec["ok"], bool)
            self.assertIs(type(rec["dur_ms"]), int)
        self.assertEqual(sorted((r["source"], r["ok"]) for r in checks),
                         sorted((r.source_id, r.ok) for r in results))

    def test_harvest_job_lines_and_failure(self):
        spec = next(s for s in self.srv.sv.catalog.specs() if "harvest" in [m.value for m in s.modes])
        self.srv.sv.catalog.http.routes.clear()  # every fetch fails: the harvest reports it, never crashes
        self.logs.read()
        ok, _ = self.srv.sv.monitor.harvest(spec)
        self.assertFalse(ok)
        recs = [r for r in self.logs.read() if r["event"].startswith("harvest")]
        self.assertEqual([r["event"] for r in recs], ["harvest_start", "harvest_failed"])
        self.assertEqual({r["source"] for r in recs}, {spec.id})
        self.assertEqual(recs[1]["level"], "warn")
        self.assertIsInstance(recs[1]["dur_ms"], int)

    def test_successful_harvest_logs_end_line_with_books_and_dur_ms(self):
        spec = next(s for s in self.srv.sv.catalog.specs() if "harvest" in [m.value for m in s.modes])
        books = [book(f"{spec.id}:{i}", f"Livre {i}", source_id=spec.id) for i in range(3)]
        adapter = mock.Mock()
        adapter.harvest.return_value = books
        with mock.patch.object(self.srv.sv.catalog, "adapter", return_value=adapter):
            self.logs.read()
            ok, detail = self.srv.sv.monitor.harvest(spec)
        self.assertTrue(ok, detail)
        stored = self.srv.sv.monitor.store.count_books(spec.id)
        self.assertEqual(stored, 3)
        recs = [r for r in self.logs.read() if r["event"].startswith("harvest")]
        self.assertEqual([r["event"] for r in recs], ["harvest_start", "harvest"])
        end = recs[1]
        check_format(self, end)
        self.assertEqual((end["level"], end["source"]), ("info", spec.id))
        self.assertIs(type(end["books"]), int)
        self.assertEqual(end["books"], stored)
        self.assertIs(type(end["dur_ms"]), int)

    def test_canary_crash_logs_one_error_line(self):
        """An adapter bug in the canary (not a SourceError) is reported as a failure and logged, never raised."""
        monitor = self.srv.sv.monitor
        spec = next(iter(self.srv.sv.catalog.specs()))
        boom = mock.Mock()
        boom.search.side_effect = RuntimeError("adapter bug")
        with mock.patch.object(self.srv.sv.catalog, "adapter", return_value=boom), \
                mock.patch.object(monitor, "_index_canary", side_effect=RuntimeError("adapter bug")):
            self.logs.read()
            result = monitor.check(spec)
        self.assertFalse(result.ok)
        self.assertIn("bug: RuntimeError", result.detail)
        crashes = [r for r in self.logs.read() if r["event"] == "canary_crashed"]
        self.assertEqual(len(crashes), 1)
        rec = crashes[0]
        check_format(self, rec)
        self.assertEqual((rec["level"], rec["source"], rec["err"]["type"]), ("error", spec.id, "RuntimeError"))

    def test_harvest_crash_logs_one_error_line_with_dur_ms(self):
        """An adapter bug during a harvest returns (False, 'bug: ...') with a harvest_crashed line, never raises."""
        spec = next(s for s in self.srv.sv.catalog.specs() if "harvest" in [m.value for m in s.modes])
        boom = mock.Mock()
        boom.harvest.side_effect = RuntimeError("adapter bug")
        with mock.patch.object(self.srv.sv.catalog, "adapter", return_value=boom):
            self.logs.read()
            ok, detail = self.srv.sv.monitor.harvest(spec)
        self.assertFalse(ok)
        self.assertTrue(detail.startswith("bug: RuntimeError"), detail)
        recs = [r for r in self.logs.read() if r["event"].startswith("harvest")]
        self.assertEqual([r["event"] for r in recs], ["harvest_start", "harvest_crashed"])
        rec = recs[1]
        check_format(self, rec)
        self.assertEqual((rec["level"], rec["source"], rec["err"]["type"]), ("error", spec.id, "RuntimeError"))
        self.assertIsInstance(rec["dur_ms"], int)

    def test_scheduler_tick_failure_logs_and_keeps_running(self):
        """A tick that raises is logged as scheduler_failed and the loop runs the next tick."""

        class NoWait(threading.Event):
            def wait(self, timeout=None):
                return self.is_set()

        stop = NoWait()
        calls = []

        def harvest_due():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("tick bug")
            stop.set()  # second tick: the loop survived the first one
            return []

        with mock.patch.object(self.srv.sv.monitor, "harvest_due", side_effect=harvest_due), \
                mock.patch.object(self.srv.sv.mailer, "flush"):
            self.logs.read()
            Scheduler(self.srv.sv, stop).run()
        self.assertEqual(len(calls), 2)
        recs = [r for r in self.logs.read() if r["event"] == "scheduler_failed"]
        self.assertEqual(len(recs), 1)
        check_format(self, recs[0])
        self.assertEqual((recs[0]["level"], recs[0]["err"]["type"]), ("error", "RuntimeError"))

    def test_plain_text_and_exception_lines(self):
        log = logging.getLogger("livres.test")
        log.warning("plain words")
        try:
            raise ValueError("bad")
        except ValueError:
            log.exception(json.dumps({"event": "adapter_crashed", "source": "x"}))
        plain, crash = self.logs.read()
        for r in (plain, crash):
            check_format(self, r)
        self.assertEqual((plain["event"], plain["msg"], plain["level"]), ("log", "plain words", "warn"))
        self.assertEqual((crash["event"], crash["level"], crash["err"]["type"]), ("adapter_crashed", "error", "ValueError"))
        self.assertNotIn("err", plain)

    # -- AGENTS.md section 11: no request body, no query string in the logs ----------------------
    def test_book_request_title_never_logged_when_mail_is_skipped(self):
        """With LIVRES_OWNER_EMAIL empty the request is still accepted; mail_skipped names no subject or title."""
        nomail = os.path.join(self.tmp, "nomail")
        os.makedirs(nomail)
        srv = Server(nomail, owner_email="")
        self.addCleanup(srv.close)
        self.logs.read()
        status, _ = srv.json("POST", "/api/v1/requests", {"title": "BODYTITLE", "author": "BODYAUTHOR"})
        self.assertEqual(status, 202)
        recs = self.logs.read()
        skipped = [r for r in recs if r["event"] == "mail_skipped"]
        self.assertEqual(len(skipped), 1, recs)
        check_format(self, skipped[0])
        self.assertEqual(skipped[0]["level"], "warn")
        self.assertNotIn("subject", skipped[0])
        raw = json.dumps(recs, ensure_ascii=False)
        self.assertNotIn("BODYTITLE", raw)
        self.assertNotIn("BODYAUTHOR", raw)
        # the requester stays anonymous in the logs: no IP, no pseudonymous client key (its sha256 prefix)
        (line,) = [r for r in recs if r["event"] == "book_request"]
        check_format(self, line)
        self.assertNotIn("client", line)
        self.assertNotIn("127.0.0.1", raw)
        self.assertNotIn(hashlib.sha256(b"ip:127.0.0.1").hexdigest()[:8], raw)

    def test_search_words_never_logged_when_a_source_fails(self):
        """An upstream 5xx during a live search: search_partial carries the error kind, never the query."""
        from livres.http import FetchError
        from test_web import EG_SEARCH

        url = EG_SEARCH.format("USERQUERY")
        self.srv.http.routes[url] = FetchError("http", f"HTTP 500 for {url}")
        self.logs.read()
        status, _ = self.srv.json("GET", "/opds/v2/search?query=USERQUERY")
        self.assertEqual(status, 200)
        self.assertIn(url, self.srv.http.calls, "the failing source was really asked")
        recs = self.logs.read()
        partial = [r for r in recs if r["event"] == "search_partial"]
        self.assertEqual(len(partial), 1, recs)
        check_format(self, partial[0])
        self.assertEqual(partial[0]["errors"]["ebooksgratuits"], "http")
        self.assertNotIn("USERQUERY", json.dumps(recs, ensure_ascii=False))

    def test_url_query_strings_are_scrubbed_from_every_line(self):
        """Fields, msg and err (message and stack) keep scheme, host and path, never the query or fragment."""
        log = logging.getLogger("livres.test")
        url = "https://up.example/opds/feed.php?mode=search&query=SECRETWORDS#frag"
        log.warning(json.dumps({"event": "harvest_failed", "source": "x", "error": f"http: HTTP 500 for {url}"}))
        try:
            raise RuntimeError(f"no fake route for {url}")
        except RuntimeError:
            log.exception(json.dumps({"event": "adapter_crashed", "source": "x"}))
        log.info(f"plain {url} words")
        recs = self.logs.read()
        self.assertEqual(len(recs), 3)
        raw = json.dumps(recs, ensure_ascii=False)
        self.assertNotIn("SECRETWORDS", raw)
        self.assertNotIn("frag", raw)
        self.assertEqual(recs[0]["error"], "http: HTTP 500 for https://up.example/opds/feed.php?…")
        self.assertEqual(recs[1]["err"]["message"], "no fake route for https://up.example/opds/feed.php?…")
        self.assertIn("https://up.example/opds/feed.php?…", recs[1]["err"]["stack"])
        self.assertEqual(recs[2]["msg"], "plain https://up.example/opds/feed.php?… words")
        self.assertEqual(logs_mod.scrub_urls("see https://a.example/p and http://b.example/q?x=1."),
                         "see https://a.example/p and http://b.example/q?…")

    def test_too_large_body_goes_through_the_request_log_and_id(self):
        """A body over MAX_BODY is refused unread (413) with X-Request-Id and the auth-route warn line."""
        self.logs.read()
        status, headers, body = self.srv.request("POST", "/admin/login", "password=" + "x" * 70_000,
                                                 headers={"X-Request-Id": "qa-413"})
        self.assertEqual((status, body), (413, b"request too large"))
        self.assertEqual(headers["x-request-id"], "qa-413")
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(headers["connection"], "close", "the unread body is never parsed as a request")
        (rec,) = self.http_lines()
        check_format(self, rec)
        self.assertEqual((rec["level"], rec["method"], rec["route"], rec["status"], rec["req_id"]),
                         ("warn", "POST", "/admin/login", 413, "qa-413"))
        self.assertNotIn("xxxx", json.dumps(rec))
        # a public route: same 413 and header, logged at debug only (nothing at info)
        status, headers, _ = self.srv.request("POST", "/api/v1/requests", "t=" + "y" * 70_000)
        self.assertEqual(status, 413)
        self.assertRegex(headers["x-request-id"], r"^[0-9a-f]{32}$")
        self.assertEqual(self.http_lines(), [])
        # the server still answers normally afterwards
        self.assertEqual(self.srv.request("GET", "/healthz")[0], 200)


if __name__ == "__main__":
    unittest.main()
