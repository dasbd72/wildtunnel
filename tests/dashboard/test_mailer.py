from __future__ import annotations

import smtplib
from typing import Any, ClassVar, Self

import pytest

from wildtunnel.dashboard.mailer import SmtpConfig, SmtpMailer


class _FakeSmtp:
    """Records what SmtpMailer would have sent, instead of a real connection."""

    instances: ClassVar[list[_FakeSmtp]] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.login_calls: list[tuple[str, str]] = []
        self.sent_messages: list[Any] = []
        self.started_tls = False
        _FakeSmtp.instances.append(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.login_calls.append((username, password))

    def send_message(self, message: Any) -> None:
        self.sent_messages.append(message)


@pytest.fixture(autouse=True)
def _reset_fake_instances() -> None:
    _FakeSmtp.instances = []


def test_send_connects_to_configured_host_and_port_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    mailer = SmtpMailer(SmtpConfig(host="smtp.example.com", port=587))

    mailer.send(to="user@example.com", subject="Reset", body="code: abc123")

    (smtp,) = _FakeSmtp.instances
    assert smtp.host == "smtp.example.com"
    assert smtp.port == 587
    assert smtp.login_calls == []
    (message,) = smtp.sent_messages
    assert message["To"] == "user@example.com"
    assert message["Subject"] == "Reset"
    assert message.get_content().strip() == "code: abc123"


def test_send_authenticates_when_credentials_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smtplib, "SMTP", _FakeSmtp)
    mailer = SmtpMailer(
        SmtpConfig(
            host="smtp.example.com",
            port=587,
            username="relay-user",
            password="relay-pass",
        )
    )

    mailer.send(to="user@example.com", subject="Reset", body="code: abc123")

    (smtp,) = _FakeSmtp.instances
    assert smtp.started_tls is True
    assert smtp.login_calls == [("relay-user", "relay-pass")]
