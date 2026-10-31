"""SQLite persistence (WAL, one connection per thread). The DB lives in /data and is rebuildable,
except for book requests and the audit trail, which are backed up with the docker volume."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from .domain import Acquisition, Book, HealthState

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS books (
    identifier TEXT PRIMARY KEY, source_id TEXT NOT NULL, title TEXT NOT NULL, authors TEXT NOT NULL,
    description TEXT NOT NULL, cover TEXT, page_url TEXT, acquisitions TEXT NOT NULL, issued TEXT,
    harvested_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS books_source ON books(source_id);
CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
    identifier UNINDEXED, title, authors, description, tokenize = 'unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS harvests (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, started_at REAL NOT NULL, finished_at REAL,
    ok INTEGER, books INTEGER, detail TEXT);
CREATE TABLE IF NOT EXISTS source_health (
    source_id TEXT PRIMARY KEY, state TEXT NOT NULL, failures INTEGER NOT NULL DEFAULT 0,
    last_check REAL, last_ok REAL, detail TEXT, results INTEGER, alerted_state TEXT);
CREATE TABLE IF NOT EXISTS health_log (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, at REAL NOT NULL, ok INTEGER NOT NULL,
    detail TEXT, results INTEGER, duration_ms INTEGER);
CREATE TABLE IF NOT EXISTS overrides (
    source_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, reason TEXT, actor TEXT, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS admin_sources (id TEXT PRIMARY KEY, spec TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY, created_at REAL NOT NULL, title TEXT NOT NULL, author TEXT, message TEXT,
    contact TEXT, client TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY, created_at REAL NOT NULL, to_addr TEXT NOT NULL, subject TEXT NOT NULL,
    body TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, sent_at REAL, last_error TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._local = threading.local()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = self.conn()
        db.executescript(SCHEMA)  # idempotent (IF NOT EXISTS); executescript manages its own transaction
        db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))

    # -- plumbing -------------------------------------------------------------------------------
    def conn(self) -> sqlite3.Connection:
        db = getattr(self._local, "db", None)
        if db is None:
            db = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=10000")
            db.execute("PRAGMA foreign_keys=ON")
            self._local.db = db
        return db

    def close_thread(self) -> None:
        """Close this thread's connection (HTTP request threads are short-lived)."""
        db = getattr(self._local, "db", None)
        if db is not None:
            db.close()
            self._local.db = None

    class _Tx:
        def __init__(self, db):
            self.db = db

        def __enter__(self):
            self.db.execute("BEGIN IMMEDIATE")
            return self.db

        def __exit__(self, exc_type, *_):
            self.db.execute("ROLLBACK" if exc_type else "COMMIT")

    def tx(self):
        return Store._Tx(self.conn())

    def ping(self) -> dict:
        db = self.conn()
        return {
            "books": db.execute("SELECT count(*) FROM books").fetchone()[0],
            "requests": db.execute("SELECT count(*) FROM requests").fetchone()[0],
        }

    # -- harvested index ------------------------------------------------------------------------
    def replace_source_books(self, source_id: str, books: Iterable[Book]) -> int:
        now = time.time()
        count = 0
        with self.tx() as db:
            db.execute("DELETE FROM books_fts WHERE identifier IN (SELECT identifier FROM books WHERE source_id=?)", (source_id,))
            db.execute("DELETE FROM books WHERE source_id=?", (source_id,))
            for b in books:
                db.execute(
                    "INSERT OR REPLACE INTO books VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        b.identifier, source_id, b.title, json.dumps(list(b.authors), ensure_ascii=False),
                        b.description, b.cover, b.page_url,
                        json.dumps([[a.href, a.media_type, a.size] for a in b.acquisitions]), b.issued, now,
                    ),
                )
                db.execute(
                    "INSERT INTO books_fts(identifier,title,authors,description) VALUES (?,?,?,?)",
                    (b.identifier, b.title, " ".join(b.authors), b.description),
                )
                count += 1
        return count

    def search_index(self, query: str, source_ids: Iterable[str], limit: int = 60) -> list[Book]:
        ids = list(source_ids)
        terms = [t for t in "".join(c if c.isalnum() else " " for c in query).split() if t]
        if not terms or not ids:
            return []
        marks = ",".join("?" * len(ids))
        sql = f"""SELECT b.* FROM books_fts f JOIN books b ON b.identifier = f.identifier
                  WHERE books_fts MATCH ? AND b.source_id IN ({marks})
                  ORDER BY bm25(books_fts, 0, 10.0, 5.0, 1.0) LIMIT ?"""
        # every term must match; if nothing does (a typo, an extra word), any term may match
        for joiner in (" ", " OR "):
            match = joiner.join(f'"{t}"*' for t in terms)  # quoted: user input is never FTS syntax
            rows = self.conn().execute(sql, (match, *ids, limit)).fetchall()
            if rows or len(terms) == 1:
                break
        return [_book(r) for r in rows]

    def list_books(self, source_id: str, limit: int = 500, offset: int = 0) -> list[Book]:
        """Everything indexed for a source, for audits (agent API)."""
        rows = self.conn().execute(
            "SELECT * FROM books WHERE source_id=? ORDER BY title, identifier LIMIT ? OFFSET ?", (source_id, limit, offset)
        ).fetchall()
        return [_book(r) for r in rows]

    def count_books(self, source_id: str) -> int:
        return self.conn().execute("SELECT count(*) FROM books WHERE source_id=?", (source_id,)).fetchone()[0]

    def start_harvest(self, source_id: str) -> int:
        with self.tx() as db:
            return db.execute("INSERT INTO harvests(source_id, started_at) VALUES (?,?)", (source_id, time.time())).lastrowid

    def finish_harvest(self, harvest_id: int, ok: bool, books: int, detail: str) -> None:
        with self.tx() as db:
            db.execute(
                "UPDATE harvests SET finished_at=?, ok=?, books=?, detail=? WHERE id=?",
                (time.time(), int(ok), books, detail[:500], harvest_id),
            )

    def last_harvest(self, source_id: str, ok_only: bool = False):
        sql = "SELECT * FROM harvests WHERE source_id=? AND finished_at IS NOT NULL"
        if ok_only:
            sql += " AND ok=1"
        return self.conn().execute(sql + " ORDER BY id DESC LIMIT 1", (source_id,)).fetchone()

    # -- health ---------------------------------------------------------------------------------
    def health(self, source_id: str):
        return self.conn().execute("SELECT * FROM source_health WHERE source_id=?", (source_id,)).fetchone()

    def all_health(self) -> dict[str, sqlite3.Row]:
        return {r["source_id"]: r for r in self.conn().execute("SELECT * FROM source_health")}

    def save_health(self, source_id, state: HealthState, failures, detail, results, ok_now: bool, alerted_state=None):
        now = time.time()
        with self.tx() as db:
            prev = db.execute("SELECT last_ok, alerted_state FROM source_health WHERE source_id=?", (source_id,)).fetchone()
            last_ok = now if ok_now else (prev["last_ok"] if prev else None)
            alerted = alerted_state if alerted_state is not None else (prev["alerted_state"] if prev else None)
            db.execute(
                "INSERT OR REPLACE INTO source_health VALUES (?,?,?,?,?,?,?,?)",
                (source_id, state.value, failures, now, last_ok, detail[:500], results, alerted),
            )

    def log_check(self, source_id, ok, detail, results, duration_ms):
        with self.tx() as db:
            db.execute(
                "INSERT INTO health_log(source_id, at, ok, detail, results, duration_ms) VALUES (?,?,?,?,?,?)",
                (source_id, time.time(), int(ok), detail[:500], results, duration_ms),
            )
            db.execute("DELETE FROM health_log WHERE at < ?", (time.time() - 90 * 86400,))

    def recent_checks(self, source_id: str, limit: int = 10):
        return self.conn().execute(
            "SELECT * FROM health_log WHERE source_id=? ORDER BY id DESC LIMIT ?", (source_id, limit)
        ).fetchall()

    # -- overrides & admin-added sources ----------------------------------------------------------
    def overrides(self) -> dict[str, bool]:
        return {r["source_id"]: bool(r["enabled"]) for r in self.conn().execute("SELECT * FROM overrides")}

    def set_override(self, source_id: str, enabled: bool, reason: str, actor: str) -> None:
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO overrides VALUES (?,?,?,?,?)", (source_id, int(enabled), reason[:300], actor, time.time()))

    def clear_override(self, source_id: str) -> None:
        with self.tx() as db:
            db.execute("DELETE FROM overrides WHERE source_id=?", (source_id,))

    def admin_sources(self) -> list[dict]:
        return [json.loads(r["spec"]) for r in self.conn().execute("SELECT spec FROM admin_sources ORDER BY id")]

    def save_admin_source(self, raw: dict) -> None:
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO admin_sources VALUES (?,?,?)", (raw["id"], json.dumps(raw, ensure_ascii=False), time.time()))

    def delete_admin_source(self, source_id: str) -> bool:
        with self.tx() as db:
            n = db.execute("DELETE FROM admin_sources WHERE id=?", (source_id,)).rowcount
            db.execute("DELETE FROM overrides WHERE source_id=?", (source_id,))
            db.execute("DELETE FROM source_health WHERE source_id=?", (source_id,))
            return n > 0

    # -- requests, outbox, audit ----------------------------------------------------------------
    def add_request(self, title, author, message, contact, client) -> int:
        with self.tx() as db:
            return db.execute(
                "INSERT INTO requests(created_at,title,author,message,contact,client,status) VALUES (?,?,?,?,?,?, 'new')",
                (time.time(), title, author, message, contact, client),
            ).lastrowid

    def count_requests(self, since: float, client: str | None = None) -> int:
        if client is None:
            return self.conn().execute("SELECT count(*) FROM requests WHERE created_at>=?", (since,)).fetchone()[0]
        return self.conn().execute(
            "SELECT count(*) FROM requests WHERE created_at>=? AND client=?", (since, client)
        ).fetchone()[0]

    def list_requests(self, limit=100):
        return self.conn().execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def set_request_status(self, request_id: int, status: str) -> bool:
        with self.tx() as db:
            return db.execute("UPDATE requests SET status=? WHERE id=?", (status, request_id)).rowcount > 0

    def queue_mail(self, to_addr, subject, body) -> int:
        with self.tx() as db:
            return db.execute(
                "INSERT INTO outbox(created_at,to_addr,subject,body) VALUES (?,?,?,?)", (time.time(), to_addr, subject, body)
            ).lastrowid

    def pending_mail(self, max_attempts=8):
        return self.conn().execute(
            "SELECT * FROM outbox WHERE sent_at IS NULL AND attempts < ? ORDER BY id LIMIT 20", (max_attempts,)
        ).fetchall()

    def retry_failed_mail(self) -> int:
        """Give unsent messages a fresh set of attempts (called at start-up, e.g. after the owner fixed SMTP)."""
        with self.tx() as db:
            return db.execute("UPDATE outbox SET attempts=0 WHERE sent_at IS NULL AND attempts > 0").rowcount

    def mark_mail(self, mail_id, error: str | None):
        with self.tx() as db:
            if error is None:
                db.execute("UPDATE outbox SET sent_at=?, attempts=attempts+1, last_error=NULL WHERE id=?", (time.time(), mail_id))
            else:
                db.execute("UPDATE outbox SET attempts=attempts+1, last_error=? WHERE id=?", (error[:300], mail_id))

    def mail_stats(self) -> dict:
        db = self.conn()
        return {
            "pending": db.execute("SELECT count(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0],
            "sent": db.execute("SELECT count(*) FROM outbox WHERE sent_at IS NOT NULL").fetchone()[0],
        }

    def audit(self, actor: str, action: str, detail: str = "") -> None:
        with self.tx() as db:
            db.execute("INSERT INTO events(at,actor,action,detail) VALUES (?,?,?,?)", (time.time(), actor, action, detail[:500]))

    def recent_events(self, limit=50):
        return self.conn().execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def _book(row) -> Book:
    return Book(
        identifier=row["identifier"],
        source_id=row["source_id"],
        title=row["title"],
        authors=tuple(json.loads(row["authors"])),
        description=row["description"],
        cover=row["cover"],
        page_url=row["page_url"],
        acquisitions=tuple(Acquisition(h, t, s) for h, t, s in json.loads(row["acquisitions"])),
        issued=row["issued"],
    )
