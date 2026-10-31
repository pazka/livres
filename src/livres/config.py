"""Settings, read only from the environment (box contract rule 6). Every default fits the hosh.it box."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(env: dict, name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    return int(raw) if raw else default


def _list(env: dict, name: str, default: str = "") -> tuple[str, ...]:
    raw = env.get(name, default)
    return tuple(x.strip() for x in raw.split(",") if x.strip())


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("/data")
    sources_dir: Path = Path("/app/sources")
    base_url: str = "https://livres.hosh.it"
    host: str = "0.0.0.0"
    port: int = 8000
    trusted_proxies: tuple = field(default_factory=tuple)

    # owner access
    admin_password_hash: str = ""  # scrypt$... (scripts/set-secrets.sh)
    admin_token_sha256: str = ""  # sha256 hex of the agent/API token
    client_tokens: tuple[str, ...] = ()  # optional allowlist of app install tokens (empty = any)

    # mail
    owner_email: str = ""
    mail_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_tls: str = "starttls"  # starttls | ssl | none

    # behaviour
    user_agent: str = "LivresHoshBot/0.1 (+https://livres.hosh.it/robots-info)"
    http_timeout: float = 10.0
    search_timeout: float = 12.0
    min_host_interval: float = 1.0  # seconds between two requests to the same host
    cache_ttl: int = 600
    health_interval: int = 6 * 3600
    harvest_interval: int = 7 * 24 * 3600
    fail_threshold: int = 2
    requests_per_hour: int = 5
    requests_per_day_total: int = 50
    scheduler: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "livres.sqlite3"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / ".secrets"

    @property
    def mail_configured(self) -> bool:
        return bool(self.smtp_host and self.owner_email and self.mail_from)

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        env = dict(os.environ if env is None else env)
        proxies = _list(env, "APP_TRUSTED_PROXIES", "127.0.0.0/8,::1/128,172.16.0.0/12")
        return cls(
            data_dir=Path(env.get("LIVRES_DATA_DIR", "/data")),
            sources_dir=Path(env.get("LIVRES_SOURCES_DIR", "/app/sources")),
            base_url=env.get("APP_BASE_URL", "https://livres.hosh.it").rstrip("/"),
            host=env.get("LIVRES_LISTEN_HOST", "0.0.0.0"),
            port=_int(env, "LIVRES_PORT", 8000),
            trusted_proxies=tuple(ipaddress.ip_network(p, strict=False) for p in proxies),
            admin_password_hash=env.get("LIVRES_ADMIN_PASSWORD_HASH", ""),
            admin_token_sha256=env.get("LIVRES_ADMIN_TOKEN_SHA256", "").lower(),
            client_tokens=_list(env, "LIVRES_CLIENT_TOKENS"),
            owner_email=env.get("LIVRES_OWNER_EMAIL", ""),
            mail_from=env.get("LIVRES_MAIL_FROM", ""),
            smtp_host=env.get("LIVRES_SMTP_HOST", ""),
            smtp_port=_int(env, "LIVRES_SMTP_PORT", 587),
            smtp_user=env.get("LIVRES_SMTP_USER", ""),
            smtp_password=env.get("LIVRES_SMTP_PASSWORD", ""),
            smtp_tls=env.get("LIVRES_SMTP_TLS", "starttls").lower(),
            user_agent=env.get("LIVRES_USER_AGENT", cls.user_agent),
            http_timeout=float(env.get("LIVRES_HTTP_TIMEOUT", "10")),
            search_timeout=float(env.get("LIVRES_SEARCH_TIMEOUT", "12")),
            min_host_interval=float(env.get("LIVRES_MIN_HOST_INTERVAL", "1")),
            cache_ttl=_int(env, "LIVRES_CACHE_TTL", 600),
            health_interval=_int(env, "LIVRES_HEALTH_INTERVAL", 6 * 3600),
            harvest_interval=_int(env, "LIVRES_HARVEST_INTERVAL", 7 * 24 * 3600),
            fail_threshold=_int(env, "LIVRES_FAIL_THRESHOLD", 2),
            requests_per_hour=_int(env, "LIVRES_REQUESTS_PER_HOUR", 5),
            requests_per_day_total=_int(env, "LIVRES_REQUESTS_PER_DAY", 50),
            scheduler=env.get("LIVRES_SCHEDULER", "on").lower() not in ("off", "0", "false"),
        )
