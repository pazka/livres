"""CLI used by CI, agents and hosh-deploy (POST_DEPLOY smoke)."""

import contextlib
import io
import json
import unittest

from helpers import SOURCES, TempDir
from test_web import Server, routes_for

from livres import cli
from livres.web.auth import verify_password


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(list(argv))
    return code, out.getvalue()


class CliTests(unittest.TestCase):
    def test_validate_repo_sources(self):
        code, out = run("validate", "--sources", str(SOURCES))
        self.assertEqual(code, 0, out)
        self.assertIn("ebooksgratuits", out)

    def test_validate_reports_invalid_spec(self):
        with TempDir() as tmp:
            with open(f"{tmp}/bad.json", "w") as f:
                json.dump({"id": "bad"}, f)
            code, out = run("validate", "--sources", tmp)
        self.assertEqual(code, 1)
        self.assertIn("INVALID", out)

    def test_smoke_passes_against_a_running_service(self):
        with TempDir() as tmp:
            srv = Server(tmp, routes_for("hugo"))
            try:
                code, out = run("smoke", f"http://127.0.0.1:{srv.port}")
            finally:
                srv.close()
        self.assertEqual(code, 0, out)
        self.assertIn("OPDS catalogue", out)

    def test_smoke_fails_when_service_is_down(self):
        code, out = run("smoke", "http://127.0.0.1:9")
        self.assertEqual(code, 1)

    def test_new_token_and_hash_password(self):
        code, out = run("new-token")
        data = json.loads(out)
        self.assertEqual(len(data["sha256"]), 64)
        import sys
        old = sys.stdin
        sys.stdin = io.StringIO("a long enough password\n")
        try:
            code, out = run("hash-password")
        finally:
            sys.stdin = old
        self.assertEqual(code, 0)
        self.assertTrue(verify_password("a long enough password", out.strip()))


if __name__ == "__main__":
    unittest.main()
