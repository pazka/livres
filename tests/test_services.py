"""Search, health monitor (state machine + alerts), harvest guard, book requests, mail outbox."""

import smtplib
import time
import unittest

from helpers import FakeHttp, FakeTransport, TempDir, book, fixture, make_settings, spec

from livres.bookrequests import RequestError, RequestService, parse_request
from livres.catalog import Catalog
from livres.domain import Canary, HealthState, Mode
from livres.health import HealthMonitor
from livres.http import FetchError, Response
from livres.mail import Mailer
from livres.search import QueryError, SearchService, rank
from livres.sources.base import SourceAdapter, SourceError
from livres.store import Store


class StubAdapter(SourceAdapter):
    def __init__(self, spec, books=(), error=None, delay=0.0, harvest_books=None):
        super().__init__(spec, FakeHttp())
        self.books, self.error, self.delay, self.harvest_books = list(books), error, delay, harvest_books
        self.calls = 0

    def search(self, query, limit=20):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.books[:limit]

    def harvest(self):
        if self.error:
            raise self.error
        yield from (self.harvest_books or [])


class World:
    """A full service graph on a temp dir, with stub adapters injected per source id."""

    def __init__(self, tmp, specs, adapters, mail_fail=None, threshold=2):
        self.settings = make_settings(tmp)
        self.store = Store(self.settings.db_path)
        self.catalog = Catalog(specs, self.store, FakeHttp())
        self.catalog.adapter = lambda s: adapters[s.id]
        self.transport = FakeTransport(mail_fail)
        self.mailer = Mailer(self.settings, self.store, self.transport)
        original = self.mailer.notify_owner
        self.mailer.notify_owner = lambda subject, body, background=False: original(subject, body, background=False)
        self.monitor = HealthMonitor(self.catalog, self.mailer, "https://livres.test", threshold)
        self.search = SearchService(self.catalog, timeout=0.5, cache_ttl=60)


class SearchTests(unittest.TestCase):
    def test_merges_sources_dedupes_and_ranks(self):
        a, b = spec("a", priority=10), spec("b", priority=20)
        adapters = {
            "a": StubAdapter(a, [book("a:1", "Les Misérables", source_id="a"), book("a:2", "Bug-Jargal", source_id="a")]),
            "b": StubAdapter(b, [book("b:9", "Les misérables", source_id="b"), book("b:3", "Les Contemplations", source_id="b")]),
        }
        with TempDir() as tmp:
            w = World(tmp, [a, b], adapters)
            page = w.search.search("les misérables")
        self.assertEqual(page.books[0].identifier, "a:1", "duplicate kept from the higher-priority source")
        self.assertNotIn("b:9", [x.identifier for x in page.books])
        self.assertEqual(page.errors, {})

    def test_broken_and_slow_sources_do_not_break_search(self):
        ok, broken, slow, crashing = spec("ok"), spec("broken"), spec("slow"), spec("crash")
        adapters = {
            "ok": StubAdapter(ok, [book("ok:1", "Notre-Dame de Paris", source_id="ok")]),
            "broken": StubAdapter(broken, error=SourceError("http", "HTTP 500")),
            "slow": StubAdapter(slow, [book("slow:1")], delay=2),
            "crash": StubAdapter(crashing, error=ZeroDivisionError("boom")),
        }
        with TempDir() as tmp:
            w = World(tmp, [ok, broken, slow, crashing], adapters)
            started = time.monotonic()
            page = w.search.search("notre dame")
            self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual([b.identifier for b in page.books], ["ok:1"])
        self.assertEqual(set(page.errors), {"broken", "slow", "crash"})
        self.assertEqual(page.errors["slow"], "timeout")

    def test_disabled_and_blocked_sources_are_skipped(self):
        on, off, blocked = spec("on"), spec("off", enabled=False), spec("blocked")
        adapters = {k: StubAdapter(s, [book(f"{k}:1", f"Livre {k}", source_id=k)]) for k, s in (("on", on), ("off", off), ("blocked", blocked))}
        with TempDir() as tmp:
            w = World(tmp, [on, off, blocked], adapters)
            w.store.save_health("blocked", HealthState.BLOCKED, 1, "robots", 0, False)
            page = w.search.search("livre")
        self.assertEqual([b.source_id for b in page.books], ["on"])
        self.assertEqual(adapters["off"].calls + adapters["blocked"].calls, 0)

    def test_results_are_cached(self):
        s = spec("s")
        adapter = StubAdapter(s, [book("s:1")])
        with TempDir() as tmp:
            w = World(tmp, [s], {"s": adapter})
            w.search.search("hugo")
            w.search.search("Hugo ")
            self.assertEqual(adapter.calls, 1)
            w.search.clear_cache()
            w.search.search("hugo")
            self.assertEqual(adapter.calls, 2)

    def test_paging(self):
        s = spec("s")
        books = [book(f"s:{i}", f"Hugo volume {i}", source_id="s") for i in range(25)]
        with TempDir() as tmp:
            w = World(tmp, [s], {"s": StubAdapter(s, books)})
            p1, p2 = w.search.search("hugo", 1), w.search.search("hugo", 2)
        self.assertEqual((len(p1.books), p1.has_next), (20, True))
        self.assertEqual((len(p2.books), p2.has_next), (5, False))

    def test_query_validation_messages_are_french(self):
        with TempDir() as tmp:
            w = World(tmp, [], {})
            with self.assertRaises(QueryError) as ctx:
                w.search.search(" a ")
            self.assertIn("deux lettres", str(ctx.exception))
            with self.assertRaises(QueryError):
                w.search.search("x" * 201)

    def test_harvested_index_is_searched(self):
        s = spec("g", kind="gutenberg_csv", modes=(Mode.HARVEST,), config={})
        adapter = StubAdapter(s, harvest_books=[book("g:1", "Les Misérables", source_id="g"), book("g:2", "Le Rouge et le Noir", ("Stendhal",), source_id="g")])
        with TempDir() as tmp:
            w = World(tmp, [s], {"g": adapter})
            self.assertEqual(w.monitor.harvest(s), (True, "2 books"))
            page = w.search.search("miserables")  # accents folded by FTS
        self.assertEqual([b.identifier for b in page.books], ["g:1"])

    def test_rank_prefers_exact_title_and_unusable_books_are_dropped(self):
        found = [(50, book("x:1", "Hugo et ses amis", ("Someone",))), (50, book("x:2", "Les Misérables")), (50, book("x:3", "Les Misérables 2", types=("application/zip",)))]
        ranked = rank("les misérables", found)
        self.assertEqual([b.identifier for b in ranked], ["x:2", "x:1"])


class HealthTests(unittest.TestCase):
    def test_state_machine_alerts_once_and_on_recovery(self):
        s = spec("s", name="Source S")
        adapter = StubAdapter(s, error=SourceError("http", "HTTP 503"))
        with TempDir() as tmp:
            w = World(tmp, [s], {"s": adapter})
            self.assertEqual(w.monitor.record(s, w.monitor.run_check(s)), HealthState.DEGRADED)
            self.assertEqual(len(w.transport.sent), 0, "one failure is a hiccup, not an alert")
            self.assertEqual(w.monitor.record(s, w.monitor.run_check(s)), HealthState.DOWN)
            self.assertEqual(len(w.transport.sent), 1)
            self.assertIn("Source en panne : Source S", w.transport.sent[0]["Subject"])
            self.assertIn("HTTP 503", w.transport.sent[0].get_content())
            w.monitor.record(s, w.monitor.run_check(s))
            self.assertEqual(len(w.transport.sent), 1, "no repeated alert while it stays down")
            adapter.error, adapter.books = None, [book("s:1", source_id="s")]
            self.assertEqual(w.monitor.record(s, w.monitor.run_check(s)), HealthState.OK)
            self.assertEqual(len(w.transport.sent), 2)
            self.assertIn("rétablie", w.transport.sent[1]["Subject"])
            self.assertEqual(w.store.health("s")["failures"], 0)

    def test_robots_block_is_its_own_state(self):
        s = spec("s")
        with TempDir() as tmp:
            w = World(tmp, [s], {"s": StubAdapter(s, error=SourceError("robots", "disallowed"))})
            self.assertTrue(w.monitor.check(s).blocked)
            self.assertEqual(w.store.health("s")["state"], "blocked")
            self.assertIn("robots", w.transport.sent[0]["Subject"])

    def test_too_few_results_fails(self):
        s = spec("s", canary=Canary("hugo", min_results=3))
        with TempDir() as tmp:
            w = World(tmp, [s], {"s": StubAdapter(s, [book("s:1", source_id="s")])})
            result = w.monitor.run_check(s)
        self.assertFalse(result.ok)
        self.assertIn("expected ≥ 3", result.detail)

    def test_download_probe_detects_html_instead_of_book(self):
        s = spec("s")
        adapter = StubAdapter(s, [book("s:1", source_id="s")])
        href = book("s:1").acquisitions[0].href
        adapter.http = FakeHttp({"PROBE " + href: Response(href, 200, {"content-type": "text/html"}, b"<html>")})
        with TempDir() as tmp:
            w = World(tmp, [s], {"s": adapter})
            result = w.monitor.run_check(s)
        self.assertFalse(result.ok)
        self.assertIn("web page instead of a book", result.detail)

    def test_download_probe_skipped_when_robots_forbids(self):
        s = spec("s")
        adapter = StubAdapter(s, [book("s:1", source_id="s")])
        href = book("s:1").acquisitions[0].href
        adapter.http = FakeHttp({"PROBE " + href: FetchError("robots", "no")})
        with TempDir() as tmp:
            w = World(tmp, [s], {"s": adapter})
            result = w.monitor.run_check(s)
        self.assertTrue(result.ok)
        self.assertIn("not probed", result.detail)

    def test_harvest_source_canary_needs_a_recent_harvest(self):
        s = spec("g", kind="gutenberg_csv", modes=(Mode.HARVEST,), config={})
        adapter = StubAdapter(s, harvest_books=[book("g:1", "Les Misérables", source_id="g")])
        with TempDir() as tmp:
            w = World(tmp, [s], {"g": adapter})
            self.assertFalse(w.monitor.run_check(s).ok)
            w.monitor.harvest(s)
            self.assertTrue(w.monitor.run_check(s).ok)

    def test_harvest_shrink_guard_keeps_the_old_index(self):
        s = spec("g", kind="gutenberg_csv", modes=(Mode.HARVEST,), config={})
        adapter = StubAdapter(s, harvest_books=[book(f"g:{i}", f"Livre {i}", source_id="g") for i in range(200)])
        with TempDir() as tmp:
            w = World(tmp, [s], {"g": adapter})
            self.assertTrue(w.monitor.harvest(s)[0])
            adapter.harvest_books = adapter.harvest_books[:10]
            ok, detail = w.monitor.harvest(s)
            self.assertFalse(ok)
            self.assertIn("shrank", detail)
            self.assertEqual(w.store.count_books("g"), 200)

    def test_harvest_due_respects_interval(self):
        s = spec("g", kind="gutenberg_csv", modes=(Mode.HARVEST,), config={})
        adapter = StubAdapter(s, harvest_books=[book("g:1", source_id="g")])
        with TempDir() as tmp:
            w = World(tmp, [s], {"g": adapter})
            self.assertEqual(len(w.monitor.harvest_due()), 1)
            self.assertEqual(w.monitor.harvest_due(), [])


class RequestTests(unittest.TestCase):
    def service(self, w, **kw):
        return RequestService(w.store, w.mailer, "https://livres.test", **kw)

    def test_request_is_stored_and_mailed_to_owner(self):
        with TempDir() as tmp:
            w = World(tmp, [], {})
            rid = self.service(w).submit({"title": "Le Petit Prince", "author": "Saint-Exupéry", "contact": "maman@example.fr"}, "tok", "1.2.3.4")
            self.assertEqual(rid, 1)
            msg = w.transport.sent[0]
            self.assertEqual(msg["To"], "owner@example.org")
            self.assertIn("Le Petit Prince", msg["Subject"])
            self.assertIn("maman@example.fr", msg.get_content())
            self.assertEqual(w.store.list_requests()[0]["title"], "Le Petit Prince")

    def test_validation_messages_are_full_french_sentences(self):
        for payload, expected in [
            (None, "n’a pas pu être lue"),
            ({}, "Indiquez le titre"),
            ({"title": "x" * 201}, "trop long"),
            ({"title": "Ok", "contact": "pas-un-mail"}, "adresse e-mail"),
            ({"title": 12}, "doit être du texte"),
        ]:
            with self.assertRaises(RequestError) as ctx:
                parse_request(payload)
            self.assertIn(expected, str(ctx.exception))
            self.assertTrue(str(ctx.exception).endswith("."))

    def test_rate_limit_per_client_and_global(self):
        with TempDir() as tmp:
            w = World(tmp, [], {})
            svc = self.service(w, per_hour=2, per_day_total=3)
            svc.submit({"title": "Un"}, "a", "1.1.1.1")
            svc.submit({"title": "Deux"}, "a", "1.1.1.1")
            with self.assertRaises(RequestError) as ctx:
                svc.submit({"title": "Trois"}, "a", "1.1.1.1")
            self.assertEqual(ctx.exception.status, 429)
            svc.submit({"title": "Trois"}, "b", "1.1.1.1")
            with self.assertRaises(RequestError) as ctx:
                svc.submit({"title": "Quatre"}, "c", "2.2.2.2")
            self.assertIn("demain", str(ctx.exception))

    def test_client_token_allowlist(self):
        with TempDir() as tmp:
            w = World(tmp, [], {})
            svc = self.service(w, client_tokens=("good",))
            with self.assertRaises(RequestError) as ctx:
                svc.submit({"title": "Livre"}, "bad", "1.1.1.1")
            self.assertEqual(ctx.exception.status, 401)
            self.assertEqual(svc.submit({"title": "Livre"}, "good", "1.1.1.1"), 1)

    def test_client_id_never_contains_raw_ip(self):
        with TempDir() as tmp:
            w = World(tmp, [], {})
            self.assertNotIn("1.2.3.4", self.service(w).client_id(None, "1.2.3.4"))


class MailTests(unittest.TestCase):
    def test_smtp_failure_keeps_mail_in_outbox_and_retries(self):
        with TempDir() as tmp:
            w = World(tmp, [], {}, mail_fail=smtplib.SMTPServerDisconnected("down"))
            w.mailer.notify_owner("Sujet", "Corps")
            self.assertEqual(w.store.mail_stats(), {"pending": 1, "sent": 0})
            w.transport.fail = None
            self.assertEqual(w.mailer.flush(), 1)
            self.assertEqual(w.store.mail_stats(), {"pending": 0, "sent": 1})

    def test_unconfigured_mail_is_queued_not_lost(self):
        with TempDir() as tmp:
            w = World(tmp, [], {})
            w.mailer.settings = make_settings(tmp, smtp_host="")
            w.mailer.notify_owner("Sujet", "Corps")
            self.assertEqual(w.store.mail_stats()["pending"], 1)
            self.assertEqual(w.transport.sent, [])


    def test_mail_that_exhausted_its_attempts_is_retried_after_a_restart(self):
        with TempDir() as tmp:
            w = World(tmp, [], {}, mail_fail=smtplib.SMTPAuthenticationError(535, b"bad user"))
            w.mailer.notify_owner("Sujet", "Corps")
            for _ in range(10):
                w.mailer.flush()
            self.assertEqual(w.store.pending_mail(), [], "gave up after max attempts")
            w.transport.fail = None  # owner fixed the SMTP user, then redeployed
            self.assertEqual(w.store.retry_failed_mail(), 1)
            self.assertEqual(w.mailer.flush(), 1)
            self.assertEqual(w.store.mail_stats(), {"pending": 0, "sent": 1})


class StoreTests(unittest.TestCase):
    def test_fts_handles_quotes_and_operators_safely(self):
        with TempDir() as tmp:
            store = Store(make_settings(tmp).db_path)
            store.replace_source_books("g", [book("g:1", "L'Assommoir", ("Émile Zola",), source_id="g")])
            self.assertEqual(len(store.search_index('assommoir" OR *', ["g"])), 1)
            self.assertEqual(len(store.search_index("zola", ["g"])), 1)
            self.assertEqual(len(store.search_index("emile", ["g"])), 1)
            self.assertEqual(store.search_index("()", ["g"]), [])

    def test_admin_sources_and_overrides_round_trip(self):
        with TempDir() as tmp:
            store = Store(make_settings(tmp).db_path)
            store.set_override("a", False, "test", "agent")
            self.assertEqual(store.overrides(), {"a": False})
            store.save_admin_source({"id": "x", "name": "X"})
            self.assertEqual(store.admin_sources(), [{"id": "x", "name": "X"}])
            self.assertTrue(store.delete_admin_source("x"))
            self.assertEqual(store.admin_sources(), [])


if __name__ == "__main__":
    unittest.main()
