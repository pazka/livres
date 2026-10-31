"""Owner e-mail: every message goes through a persistent outbox, so nothing is lost when SMTP is down
(retried by the scheduler, visible in the admin page)."""

from __future__ import annotations

import json
import logging
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .config import Settings
from .store import Store

log = logging.getLogger("livres.mail")


class SmtpTransport:
    def __init__(self, settings: Settings):
        self.s = settings

    def send(self, msg: EmailMessage) -> None:
        s = self.s
        context = ssl.create_default_context()
        if s.smtp_tls == "ssl":
            client = smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=context)
        else:
            client = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20)
        with client:
            if s.smtp_tls == "starttls":
                client.starttls(context=context)
            if s.smtp_user:
                client.login(s.smtp_user, s.smtp_password)
            client.send_message(msg)


class Mailer:
    def __init__(self, settings: Settings, store: Store, transport=None):
        self.settings = settings
        self.store = store
        self.transport = transport or SmtpTransport(settings)
        self._flush_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return self.settings.mail_configured

    def notify_owner(self, subject: str, body: str, background: bool = True) -> int | None:
        if not self.settings.owner_email:
            log.warning(json.dumps({"event": "mail_skipped", "reason": "LIVRES_OWNER_EMAIL is empty", "subject": subject}))
            return None
        mail_id = self.store.queue_mail(self.settings.owner_email, subject, body)
        if background:
            threading.Thread(target=self.flush, name="mail-flush", daemon=True).start()
        else:
            self.flush()
        return mail_id

    def flush(self) -> int:
        """Send what is pending. Returns how many were sent."""
        if not self.configured:
            return 0
        sent = 0
        with self._flush_lock:
            for row in self.store.pending_mail():
                msg = EmailMessage()
                msg["From"] = self.settings.mail_from
                msg["To"] = row["to_addr"]
                msg["Subject"] = row["subject"]
                msg["Date"] = formatdate(localtime=True)
                msg["Message-ID"] = make_msgid(domain="livres.hosh.it")
                msg.set_content(row["body"])
                try:
                    self.transport.send(msg)
                except (smtplib.SMTPException, OSError) as e:
                    self.store.mark_mail(row["id"], f"{type(e).__name__}: {e}")
                    log.warning(json.dumps({"event": "mail_failed", "id": row["id"], "error": type(e).__name__}))
                    break  # SMTP is unhappy: retry later instead of hammering it
                self.store.mark_mail(row["id"], None)
                sent += 1
        return sent
