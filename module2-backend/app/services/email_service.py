"""Outgoing email over SMTP, configured entirely from the environment.

    SMTP_HOST       mail server host; email is disabled while this is unset
    SMTP_PORT       default 587
    SMTP_USER       login user (optional, e.g. for a local catcher like Mailpit)
    SMTP_PASSWORD   login password / app password (optional)
    SMTP_FROM       sender address, default "SAIV <no-reply@saiv.local>"
    SMTP_STARTTLS   "1" (default) to upgrade the connection with STARTTLS;
                    set "0" for a plain local server such as Mailpit
    SMTP_SSL        "1" to connect with implicit TLS instead (usually port 465)

Nothing is hardcoded: the same code sends through Mailpit for a demo or
through a real provider (NTU, Gmail app password, ...) in production.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def email_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST", "").strip())


def send_email(to_address: str, subject: str, body: str) -> bool:
    """Send one plain-text email. Returns False (and logs why) on any failure;
    callers treat email as best-effort and never surface the reason to the
    requester."""
    host = os.environ.get("SMTP_HOST", "").strip()
    if not host:
        logger.warning("SMTP_HOST is not set; email to %s not sent", to_address)
        return False
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", "SAIV <no-reply@saiv.local>")

    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
        if not 1 <= port <= 65535:
            raise ValueError("Invalid SMTP port")
        implicit_tls = _flag("SMTP_SSL", "0")
        start_tls = _flag("SMTP_STARTTLS", "1")
        if user and not (implicit_tls or start_tls):
            raise ValueError("SMTP authentication requires TLS")
        message = EmailMessage()
        message["From"] = sender
        message["To"] = to_address
        message["Subject"] = subject
        message.set_content(body)
        if _flag("SMTP_SSL", "0"):
            server = smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=10)
        with server:
            if not _flag("SMTP_SSL", "0") and _flag("SMTP_STARTTLS", "1"):
                server.starttls(context=ssl.create_default_context())
            if user:
                server.login(user, password)
            server.send_message(message)
        return True
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        logger.error("Could not send email to %s: %s", to_address, type(exc).__name__)
        return False
