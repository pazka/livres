"""Standard-library HTTP server adapter (ThreadingHTTPServer) around App."""

from __future__ import annotations

import json
import logging
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .app import MAX_BODY, App, Request, Response, client_ip, parse_cookies

log = logging.getLogger("livres.web")


def _content_length(value: str | None) -> int | None:
    """The body length, 0 when absent, None when the header is not a plain non-negative integer."""
    value = (value or "0").strip()
    return int(value) if value.isascii() and value.isdigit() else None


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "livres"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def _serve(self):
            try:
                parts = urllib.parse.urlsplit(self.path)
                length = _content_length(self.headers.get("Content-Length"))
                bad_length = length is None
                too_large = not bad_length and length > MAX_BODY  # refused unread, answered through App.handle
                body = self.rfile.read(length) if length and not too_large else b""
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
                resp = app.handle(req, too_large=too_large, bad_length=bad_length)
                if too_large or bad_length:  # the unread body must not be parsed as the next request
                    resp.headers["Connection"] = "close"
                    self.close_connection = True
                self._send(resp)
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


class Server(ThreadingHTTPServer):
    """Errors raised outside App.handle (socket writes, request parsing) end in one JSON line, never in
    socketserver's default multi-line stderr traceback with the peer address."""

    daemon_threads = True

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError)):  # reset, broken pipe, aborted, timed out
            log.debug(json.dumps({"event": "client_disconnected", "msg": "client went away before the answer",
                                  "error": type(exc).__name__}))
        else:
            log.error(json.dumps({"event": "http_crashed", "msg": "unhandled error outside the request handler"}),
                      exc_info=True)


def make_server(app: App, host: str, port: int) -> ThreadingHTTPServer:
    return Server((host, port), make_handler(app))
