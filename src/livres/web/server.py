"""Standard-library HTTP server adapter (ThreadingHTTPServer) around App."""

from __future__ import annotations

import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .app import MAX_BODY, App, Request, Response, client_ip, parse_cookies


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "livres"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def _serve(self):
            parts = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                self._send(Response(413, b"request too large"))
                return
            body = self.rfile.read(length) if length else b""
            headers = {k.lower(): v for k, v in self.headers.items()}
            req = Request(
                method=self.command,
                path=parts.path or "/",
                query={k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()},
                headers=headers,
                body=body,
                client_ip=client_ip(self.client_address[0], headers.get("x-forwarded-for"), app.s.trusted_proxies),
                cookies=parse_cookies(headers.get("cookie")),
            )
            try:
                self._send(app.handle(req))
            finally:
                app.store.close_thread()

        def _send(self, resp: Response):
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
            for k, v in resp.headers.items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(resp.body)

        do_GET = do_POST = do_HEAD = do_DELETE = do_PUT = do_PATCH = _serve

        def log_message(self, format, *args):  # access log is written by App.handle as JSON
            pass

    return Handler


def make_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), make_handler(app))
    server.daemon_threads = True
    return server
