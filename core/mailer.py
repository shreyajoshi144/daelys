"""Outgoing email for the `notify` action. Locally: the Mailpit sandbox, never a real inbox."""

import asyncio
import smtplib
from email.message import EmailMessage

from core.config import get_settings
from core.errors import NotConfigured

TIMEOUT_SECONDS = 10


def _send(message: EmailMessage, host: str, port: int) -> None:
    with smtplib.SMTP(host, port, timeout=TIMEOUT_SECONDS) as smtp:
        smtp.send_message(message)


async def send_email(to: list[str], subject: str, body: str, message_id: str) -> None:
    """`message_id` comes from the proposal, so a resend after a crash is recognisable as the same mail."""
    settings = get_settings()
    if not settings.smtp_host:
        raise NotConfigured("SMTP_HOST is not set (run Mailpit locally: `make up` starts it)")
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message["Message-ID"] = message_id
    message.set_content(body)
    await asyncio.to_thread(_send, message, settings.smtp_host, settings.smtp_port)
