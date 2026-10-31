"""'Demander un livre': Mom asks for a book she could not find; the owner gets an e-mail.

Error messages are full French sentences: the app shows them to her as they are."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass

from .mail import Mailer
from .store import Store

log = logging.getLogger("livres.requests")

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class RequestError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class BookRequest:
    title: str
    author: str = ""
    message: str = ""
    contact: str = ""


def _clean(value, limit: int, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RequestError(f"Le champ « {field} » doit être du texte.")
    value = CONTROL.sub("", value).strip()
    if len(value) > limit:
        raise RequestError(f"Le champ « {field} » est trop long ({limit} caractères au plus).")
    return value


def parse_request(payload) -> BookRequest:
    if not isinstance(payload, dict):
        raise RequestError("La demande n’a pas pu être lue. Réessayez dans un moment.")
    title = _clean(payload.get("title"), 200, "titre")
    if len(title) < 2:
        raise RequestError("Indiquez le titre du livre que vous cherchez.")
    contact = _clean(payload.get("contact"), 254, "contact")
    if contact and not EMAIL_RE.match(contact):
        raise RequestError("L’adresse e-mail ne semble pas correcte. Vérifiez-la ou laissez la case vide.")
    return BookRequest(
        title=title,
        author=_clean(payload.get("author"), 200, "auteur"),
        message=_clean(payload.get("message"), 2000, "message"),
        contact=contact,
    )


class RequestService:
    def __init__(self, store: Store, mailer: Mailer, base_url: str, per_hour: int = 5, per_day_total: int = 50, client_tokens: tuple[str, ...] = ()):
        self.store = store
        self.mailer = mailer
        self.base_url = base_url
        self.per_hour = per_hour
        self.per_day_total = per_day_total
        self.client_tokens = {hashlib.sha256(t.encode()).hexdigest() for t in client_tokens}

    def client_id(self, token: str | None, ip: str) -> str:
        """Pseudonymous key for rate limiting: hashed install token, else hashed IP (box rule: no raw IPs)."""
        raw = f"token:{token}" if token else f"ip:{ip}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def submit(self, payload, token: str | None, ip: str) -> int:
        if self.client_tokens and (not token or hashlib.sha256(token.encode()).hexdigest() not in self.client_tokens):
            raise RequestError("Cette application n’est pas reconnue par le service. Prévenez la personne qui l’a installée.", 401)
        req = parse_request(payload)
        client = self.client_id(token, ip)
        now = time.time()
        if self.store.count_requests(now - 3600, client) >= self.per_hour:
            raise RequestError("Vous avez déjà envoyé plusieurs demandes. Merci de patienter une heure avant d’en envoyer une autre.", 429)
        if self.store.count_requests(now - 86400) >= self.per_day_total:
            raise RequestError("Le service reçoit beaucoup de demandes aujourd’hui. Merci de réessayer demain.", 429)
        request_id = self.store.add_request(req.title, req.author, req.message, req.contact, client)
        self.mailer.notify_owner(f"[livres] Demande de livre : {req.title}", self._body(request_id, req))
        log.info(json.dumps({"event": "book_request", "id": request_id, "client": client}))
        return request_id

    def _body(self, request_id: int, req: BookRequest) -> str:
        return f"""Bonjour,

Une demande de livre vient d'arriver depuis Ma liseuse (n° {request_id}).

Titre   : {req.title}
Auteur  : {req.author or "(non précisé)"}
Contact : {req.contact or "(non précisé)"}
Message :
{req.message or "(aucun)"}

Toutes les demandes : {self.base_url}/admin/requests

-- livres.hosh.it
"""
