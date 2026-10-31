"""PoliteHttpClient against a real local HTTP server: robots.txt, host allowlist, redirects, size cap, throttle."""

import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import helpers  # noqa: F401  (sets sys.path)

from livres.http import FetchError, PoliteHttpClient, host_allowed


class Site:
    def __init__(self, robots: bytes | None = b"User-agent: *\nDisallow: /private\n", robots_status=200):
        site = self
        self.hits = []
        self.robots, self.robots_status = robots, robots_status

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                site.hits.append((time.monotonic(), self.path, self.headers.get("User-Agent"), self.headers.get("Range")))
                if self.path == "/robots.txt":
                    if site.robots is None:
                        self.send_error(site.robots_status)
                        return
                    self._send(200, site.robots)
                elif self.path == "/big":
                    self._send(200, b"x" * 5000)
                elif self.path == "/redirect-away":
                    self.send_response(302)
                    self.send_header("Location", "http://evil.invalid/x")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif self.path == "/missing":
                    self.send_error(404)
                else:
                    self._send(200, b"hello")

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()


HOSTS = ("127.0.0.1",)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.site = Site()
        self.client = PoliteHttpClient("LivresHoshBot/test", timeout=5, min_interval=0)

    def tearDown(self):
        self.site.close()

    def test_fetch_sends_user_agent(self):
        resp = self.client.get(self.site.base + "/ok", allowed_hosts=HOSTS)
        self.assertEqual(resp.body, b"hello")
        self.assertTrue(all(ua == "LivresHoshBot/test" for _, _, ua, _ in self.site.hits))

    def test_robots_disallow_is_honoured_and_never_fetched(self):
        with self.assertRaises(FetchError) as ctx:
            self.client.get(self.site.base + "/private/page", allowed_hosts=HOSTS)
        self.assertEqual(ctx.exception.kind, "robots")
        self.assertNotIn("/private/page", [p for _, p, _, _ in self.site.hits])

    def test_robots_is_cached(self):
        self.client.get(self.site.base + "/a", allowed_hosts=HOSTS)
        self.client.get(self.site.base + "/b", allowed_hosts=HOSTS)
        self.assertEqual([p for _, p, _, _ in self.site.hits].count("/robots.txt"), 1)

    def test_missing_robots_means_allowed(self):
        self.site.robots, self.site.robots_status = None, 404
        self.assertEqual(self.client.get(self.site.base + "/private/x", allowed_hosts=HOSTS).body, b"hello")

    def test_robots_server_error_means_nothing_is_fetched_but_is_not_a_ban(self):
        self.site.robots, self.site.robots_status = None, 503
        with self.assertRaises(FetchError) as ctx:
            self.client.get(self.site.base + "/x", allowed_hosts=HOSTS)
        self.assertEqual(ctx.exception.kind, "http", "a 5xx robots.txt is an outage, not a robots ban")
        self.assertNotIn("/x", [p for _, p, _, _ in self.site.hits])

    def test_unreachable_site_is_a_network_error(self):
        with self.assertRaises(FetchError) as ctx:
            self.client.get("http://127.0.0.1:9/x", allowed_hosts=HOSTS)
        self.assertEqual(ctx.exception.kind, "network")

    def test_undeclared_host_is_refused_before_any_request(self):
        with self.assertRaises(FetchError) as ctx:
            self.client.get(self.site.base + "/x", allowed_hosts=("other.example",))
        self.assertEqual(ctx.exception.kind, "host")
        self.assertEqual(self.site.hits, [])

    def test_redirect_to_undeclared_host_is_refused(self):
        with self.assertRaises(FetchError) as ctx:
            self.client.get(self.site.base + "/redirect-away", allowed_hosts=HOSTS)
        self.assertEqual(ctx.exception.kind, "host")

    def test_size_cap(self):
        with self.assertRaises(FetchError) as ctx:
            self.client.get(self.site.base + "/big", allowed_hosts=HOSTS, max_bytes=1000)
        self.assertEqual(ctx.exception.kind, "too_large")

    def test_probe_uses_range_and_truncates(self):
        resp = self.client.probe(self.site.base + "/big", allowed_hosts=HOSTS)
        self.assertLessEqual(len(resp.body), 4096)
        self.assertEqual(self.site.hits[-1][3], "bytes=0-4095")

    def test_http_errors(self):
        with self.assertRaises(FetchError) as ctx:
            self.client.get(self.site.base + "/missing", allowed_hosts=HOSTS)
        self.assertEqual(ctx.exception.kind, "http")

    def test_throttle_spaces_requests_to_one_host(self):
        client = PoliteHttpClient("LivresHoshBot/test", timeout=5, min_interval=0.3)
        client.get(self.site.base + "/a", allowed_hosts=HOSTS)
        client.get(self.site.base + "/b", allowed_hosts=HOSTS)
        times = [t for t, p, _, _ in self.site.hits if p in ("/a", "/b")]
        self.assertGreaterEqual(times[1] - times[0], 0.2)

    def test_crawl_delay_from_robots_is_honoured(self):
        self.site.robots = b"User-agent: *\nCrawl-delay: 1\nDisallow: /private\n"
        client = PoliteHttpClient("LivresHoshBot/test", timeout=5, min_interval=0)
        client.get(self.site.base + "/a", allowed_hosts=HOSTS)
        client.get(self.site.base + "/b", allowed_hosts=HOSTS)
        times = [t for t, p, _, _ in self.site.hits if p in ("/a", "/b")]
        self.assertGreaterEqual(times[1] - times[0], 0.9)
        self.assertEqual(client.crawl_delay(self.site.base + "/a"), 1.0)

    def test_no_crawl_delay_means_min_interval(self):
        self.client.get(self.site.base + "/a", allowed_hosts=HOSTS)
        self.assertEqual(self.client.crawl_delay(self.site.base + "/a"), 0.0)

    def test_host_allowed(self):
        self.assertTrue(host_allowed("https://www.gutenberg.org/x", ("www.gutenberg.org",)))
        self.assertFalse(host_allowed("https://evil.gutenberg.org.example/x", ("www.gutenberg.org",)))
        self.assertTrue(host_allowed("https://a.b.feedbooks.net/x", ("*.feedbooks.net",)))
        self.assertFalse(host_allowed("https://feedbooks.net.evil/x", ("*.feedbooks.net",)))
        self.assertFalse(host_allowed("file:///etc/passwd", ("",)))


if __name__ == "__main__":
    unittest.main()
