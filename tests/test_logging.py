"""Logging standard (hosh-management AGENTS.md section 11): one JSON object per line on stdout with ts, level,
service, version, event, msg; startup and shutdown lines; requests logged only when they matter; req_id echoed;
one error line with `err` and the route template on an unhandled exception; job lines with dur_ms and counts."""
import io
import json
import logging
import os
import re
import sys
import threading
import unittest
from unittest import mock

from helpers import TempDir
from test_web import PASSWORD, Server

from livres import logs as logs_mod
from livres.main import run_health_checks, serve
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


if __name__ == "__main__":
    unittest.main()
