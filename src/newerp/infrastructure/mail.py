"""E-mail through SMTP. smtplib is blocking, so the send runs in a worker thread (asyncio.to_thread), like
Task.Run around a synchronous SmtpClient in .NET; EmailMessage refuses line breaks in headers, so a name or an
address cannot add headers of its own."""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from newerp.config import MailSettings

log = logging.getLogger(__name__)
TIMEOUT_SECONDS = 30


class SmtpMailSender:
    def __init__(self, settings: MailSettings) -> None:
        self._settings = settings

    is_configured = True

    async def send(self, to: str, subject: str, text: str) -> None:
        message = EmailMessage()
        message["From"] = self._settings.from_address
        message["To"] = to
        message["Subject"] = subject
        message.set_content(text)
        await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> None:
        s = self._settings
        with smtplib.SMTP(s.host, s.port, timeout=TIMEOUT_SECONDS) as smtp:
            if s.starttls:
                smtp.starttls()
            if s.username:
                smtp.login(s.username, s.password.get_secret_value())
            smtp.send_message(message)


class StubMailSender:
    """Without SMTP settings nothing leaves the server."""

    is_configured = False

    async def send(self, to: str, subject: str, text: str) -> None:
        log.info("e-mail not sent (no SMTP settings): %s", subject)


def mail_sender(settings: MailSettings) -> SmtpMailSender | StubMailSender:
    return SmtpMailSender(settings) if settings.is_configured else StubMailSender()
