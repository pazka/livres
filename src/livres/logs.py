"""Logging standard (hosh-management AGENTS.md section 11): one JSON object per line on stdout.

Every line has ts (UTC ISO 8601 with ms), level (debug|info|warn|error), service, version, event, msg;
then the line's own fields; `err` {type, message, stack} only when an exception is attached.
Callers log either a JSON object (`log.info(json.dumps({"event": ..., ...}))`) or plain text (event "log").
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import traceback
from datetime import datetime, timezone

from . import __version__

SERVICE = "livres"
# A URL's query string or fragment can hold what a user typed (a search query, a title): AGENTS.md section 11
# keeps it out of the logs. Every string of every line goes through scrub_urls, `err` included.
_URL_QUERY = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s?#\"'<>]*)[?#][^\s\"'<>]*")


def scrub_urls(text: str) -> str:
    """Drop the query string and fragment of every URL in `text` (scheme, host and path are kept)."""
    return _URL_QUERY.sub(r"\1?…", text)


def _scrub(value):
    if isinstance(value, str):
        return scrub_urls(value)
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING, "warning": logging.WARNING,
          "error": logging.ERROR}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        try:
            payload = json.loads(msg) if msg.startswith("{") else {"msg": msg}
        except ValueError:
            payload = {"msg": msg}
        if not isinstance(payload, dict):
            payload = {"msg": msg}
        level = {"warning": "warn", "critical": "error"}.get(record.levelname.lower(), record.levelname.lower())
        ts = datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")
        out = {"ts": ts.replace("+00:00", "Z"), "level": level, "service": SERVICE, "version": __version__,
               "logger": record.name, **payload}
        out.setdefault("event", "log")
        out.setdefault("msg", out["event"])
        if record.exc_info and record.exc_info[0] is not None:
            etype, exc, tb = record.exc_info
            out["err"] = {"type": etype.__name__, "message": str(exc)[:500],
                          "stack": "".join(traceback.format_exception(etype, exc, tb))[-4000:]}
        return json.dumps(_scrub(out), ensure_ascii=False)


def log_level() -> int:
    """LOG_LEVEL env (debug|info|warn|error), default info."""
    return LEVELS.get(os.environ.get("LOG_LEVEL", "info").strip().lower(), logging.INFO)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=log_level(), handlers=[handler], force=True)
