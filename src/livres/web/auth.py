"""Owner authentication: scrypt password (admin page), sha256-hashed bearer token (agents / CI),
HMAC-signed session cookies with a CSRF token. Standard library only."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

SESSION_TTL = 12 * 3600
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(digest).decode()
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = encoded.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def token_matches(token: str | None, expected_sha256: str) -> bool:
    if not token or not expected_sha256:
        return False
    return hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), expected_sha256)


def load_or_create_key(secrets_dir: Path) -> bytes:
    """Session signing key, generated once into /data/.secrets (box contract rule 6)."""
    secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = secrets_dir / "session.key"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
    return path.read_bytes()


class Sessions:
    def __init__(self, key: bytes):
        self.key = key

    def issue(self) -> tuple[str, str]:
        csrf = secrets.token_urlsafe(24)
        payload = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + SESSION_TTL, "csrf": csrf}).encode()).decode()
        return f"{payload}.{self._sign(payload)}", csrf

    def read(self, cookie: str | None) -> dict | None:
        if not cookie or "." not in cookie:
            return None
        payload, sig = cookie.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(payload)):
            return None
        try:
            data = json.loads(base64.urlsafe_b64decode(payload.encode()))
        except ValueError:
            return None
        if not isinstance(data, dict) or data.get("exp", 0) < time.time():
            return None
        return data

    def _sign(self, payload: str) -> str:
        return hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()


class LoginThrottle:
    """At most `limit` failed logins per client per window (in memory: a restart resets it, by design)."""

    def __init__(self, limit: int = 5, window: int = 900):
        self.limit, self.window = limit, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, client: str) -> bool:
        with self._lock:
            now = time.time()
            recent = [t for t in self._fails.get(client, []) if now - t < self.window]
            self._fails[client] = recent
            return len(recent) >= self.limit

    def fail(self, client: str) -> None:
        with self._lock:
            self._fails.setdefault(client, []).append(time.time())

    def reset(self, client: str) -> None:
        with self._lock:
            self._fails.pop(client, None)
