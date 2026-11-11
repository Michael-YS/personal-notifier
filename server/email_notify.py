"""SMTP delivery for configured broadcast subscribers."""

import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.errors import MessageError
from email.headerregistry import Address
from email.message import EmailMessage
from urllib.parse import urlsplit


def mailbox(value):
    address = Address(addr_spec=value)
    if not address.username or not address.domain:
        raise ValueError("Expected a complete email address")
    return address.addr_spec


@dataclass(frozen=True)
class EmailSender:
    host: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)
    recipients: tuple[str, ...] = field(repr=False)

    @classmethod
    def from_env(cls):
        address = os.getenv("SMTP_SERVER_ADDRESS_PORT", "").strip()
        username = os.getenv("SMPT_USERNAME", "").strip()
        password = os.getenv("SMTP_PASSWORD", "")
        subscribers = os.getenv("SUBSCRIBER_EMAIL", "").strip()
        if not any((address, username, password, subscribers)):
            return None
        if not all((address, username, password, subscribers)):
            raise ValueError("Set all four SMTP notification settings together")

        try:
            endpoint = urlsplit(f"//{address}")
            host, port = endpoint.hostname, endpoint.port
            if (
                not host
                or not port
                or endpoint.username is not None
                or endpoint.netloc != address
                or any(character.isspace() for character in address)
            ):
                raise ValueError
        except ValueError:
            raise ValueError("SMTP_SERVER_ADDRESS_PORT must be HOST:PORT") from None

        try:
            username = mailbox(username)
        except (ValueError, MessageError):
            raise ValueError("SMPT_USERNAME must be a sender email address") from None
        try:
            if not (subscribers.startswith("[") and subscribers.endswith("]")):
                raise ValueError
            recipients = tuple(
                dict.fromkeys(mailbox(value.strip()) for value in subscribers[1:-1].split(","))
            )
        except (ValueError, MessageError):
            raise ValueError(
                "SUBSCRIBER_EMAIL must be a nonempty [address, address] list"
            ) from None
        return cls(host, port, username, password, recipients)

    def send(self, title, body):
        message = EmailMessage()
        message["From"] = self.username
        message["To"] = "undisclosed-recipients:;"
        message["Subject"] = " ".join(title.splitlines())
        message.set_content(body)

        refused = None
        error = "email_error"
        try:
            context = ssl.create_default_context()
            connection = (
                smtplib.SMTP_SSL(self.host, self.port, timeout=15, context=context)
                if self.port == 465
                else smtplib.SMTP(self.host, self.port, timeout=15)
            )
            with connection as smtp:
                if self.port != 465:
                    smtp.starttls(context=context)
                smtp.login(self.username, self.password)
                refused = smtp.send_message(message, to_addrs=self.recipients)
        except smtplib.SMTPRecipientsRefused:
            error = "email_rejected"
        except (OSError, UnicodeError):
            pass  # The result of DATA still stands if only QUIT failed.

        if refused is None:
            return [
                {"channel": "email", "status": "failed", "error": error} for _ in self.recipients
            ]
        return [
            {"channel": "email", "status": "failed", "error": "email_rejected"}
            if address in refused
            else {"channel": "email", "status": "accepted"}
            for address in self.recipients
        ]
