from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol


class Mailer(Protocol):
    """Port for sending outbound email.

    Tests use a fake/test-double implementation that just records what
    would have been sent — never a real SMTP connection.
    """

    def send(self, *, to: str, subject: str, body: str) -> None: ...


@dataclass(frozen=True)
class SmtpConfig:
    """Generic SMTP relay settings — no vendor-specific email service/SDK."""

    host: str
    port: int
    username: str | None = None
    password: str | None = None
    sender: str | None = None


class SmtpMailer:
    """`Mailer` backed by the standard library's `smtplib`.

    Deliberately generic: any SMTP relay works, not a particular vendor's
    API (e.g. no AWS SES).
    """

    def __init__(self, config: SmtpConfig) -> None:
        self._config = config

    def send(self, *, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = (
            self._config.sender or self._config.username or "noreply@wildtunnel"
        )
        message["To"] = to
        message.set_content(body)

        with smtplib.SMTP(self._config.host, self._config.port) as smtp:
            if self._config.username and self._config.password:
                smtp.starttls()
                smtp.login(self._config.username, self._config.password)
            smtp.send_message(message)
