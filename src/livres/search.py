"""Search across every active source: live sources in parallel (bounded by a deadline) + the local index
of harvested catalogues. A slow or broken source never breaks a search: its error is reported apart."""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait

from .catalog import Catalog
from .domain import Book, Mode, SearchPage, fold
from .sources import SourceError

log = logging.getLogger("livres.search")

MAX_QUERY = 200
PER_SOURCE = 40


class QueryError(ValueError):
    """The French message is shown to Mom as is."""


class SearchService:
    def __init__(self, catalog: Catalog, timeout: float = 12.0, cache_ttl: int = 600, workers: int = 8):
        self.catalog = catalog
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="search")
        self._cache: dict[str, tuple[float, list[Book], dict[str, str]]] = {}
        self._lock = threading.Lock()

    def search(self, query: str, page: int = 1, page_size: int = 20) -> SearchPage:
        query = " ".join((query or "").split())
        if len(query) < 2:
            raise QueryError("Écrivez au moins deux lettres du titre ou de l’auteur.")
        if len(query) > MAX_QUERY:
            raise QueryError("La recherche est trop longue : quelques mots du titre ou de l’auteur suffisent.")
        page = max(1, page)
        books, errors = self._results(query)
        start = (page - 1) * page_size
        return SearchPage(query, page, page_size, books[start : start + page_size], len(books) > start + page_size, errors)

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    # -----------------------------------------------------------------------------------------
    def _results(self, query: str) -> tuple[list[Book], dict[str, str]]:
        key = fold(query)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.cache_ttl:
                return hit[1], hit[2]
        found: list[tuple[int, Book]] = []
        errors: dict[str, str] = {}

        live = self.catalog.active(Mode.SEARCH)
        futures = {self._pool.submit(adapter.search, query, PER_SOURCE): spec for spec, adapter in live}
        done, pending = wait(futures, timeout=self.timeout)
        for fut in pending:
            fut.cancel()
            errors[futures[fut].id] = "timeout"
        for fut in done:
            spec = futures[fut]
            try:
                found.extend((spec.priority, b) for b in fut.result())
            except SourceError as e:
                errors[spec.id] = f"{e.kind}: {e}"
            except Exception as e:  # an adapter bug must not take the search down
                errors[spec.id] = f"bug: {type(e).__name__}: {e}"
                log.exception("adapter crashed")

        harvested = {spec.id: spec.priority for spec, _ in self.catalog.active(Mode.HARVEST)}
        if harvested:
            found.extend((harvested[b.source_id], b) for b in self.catalog.store.search_index(query, harvested, 80))

        books = rank(query, found)
        if errors:
            log.info(json.dumps({"event": "search_partial", "errors": errors}))
        with self._lock:
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[key] = (now, books, errors)
        return books, errors


def rank(query: str, found: list[tuple[int, Book]]) -> list[Book]:
    """Dedupe (same title + author surname across sources -> keep the best source) and sort by relevance."""
    terms = fold(query).split()
    best: dict[str, tuple[float, int, Book]] = {}
    for priority, book in found:
        if not book.usable:
            continue
        score = _score(terms, book) - priority / 1000
        key = book.dedupe_key()
        current = best.get(key)
        if current is None or (priority, -score) < (current[1], -current[0]):
            best[key] = (score, priority, book)
    ordered = sorted(best.values(), key=lambda t: (-t[0], t[2].title))
    return [b for _, _, b in ordered]


def _score(terms: list[str], book: Book) -> float:
    title = fold(book.title)
    authors = fold(" ".join(book.authors))
    words = set(title.split()) | set(authors.split())
    score = 0.0
    for t in terms:
        if t in title.split():
            score += 3
        elif t in authors.split():
            score += 2
        elif any(w.startswith(t) for w in words):
            score += 1
    if " ".join(terms) == title:
        score += 5
    if all(t in words for t in terms):
        score += 2
    best_type = min((a.rank for a in book.acquisitions), default=9)
    return score - best_type * 0.1
