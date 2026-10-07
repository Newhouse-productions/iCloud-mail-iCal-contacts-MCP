"""Writing email: building messages, send guardrails, SMTP, drafts, reply addressing."""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime, getaddresses, make_msgid

from mcp.server.mcpserver.exceptions import ToolError

from . import config, imap, mime
from .schemas import SendResult

log = logging.getLogger("icloud-mail")

SMTP_HOST = "smtp.mail.me.com"
SMTP_PORT = 587


def addresses(*fields: str | None) -> list[str]:
    """Bare, lower-cased email addresses from one or more address headers."""
    return [addr.lower() for _, addr in getaddresses([f for f in fields if f]) if addr]


def build_email(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> EmailMessage:
    """Construct an EmailMessage ready to send or save as a draft."""
    settings = config.current()
    if not settings.email:
        raise ToolError("ICLOUD_EMAIL is not set. Run `icloud-mail-mcp --setup`.")
    if not addresses(to):
        raise ToolError("At least one valid recipient address is required in 'to'.")
    msg = EmailMessage()
    headers = {
        "From": settings.email,
        "To": to,
        "Subject": subject,
        "Cc": cc,
        "Bcc": bcc,
        "Date": format_datetime(datetime.now().astimezone()),
        "Message-ID": make_msgid(domain=settings.email.rsplit("@", 1)[-1]),
        "In-Reply-To": in_reply_to,
        "References": references,
    }
    for name, value in headers.items():
        if not value:
            continue
        try:
            msg[name] = value
        except ValueError as e:
            raise ToolError(f"Invalid {name}: header values must be a single line ({e}).") from e
    msg.set_content(body)
    return msg


def _allowed(address: str, allowlist: tuple[str, ...]) -> bool:
    return any(address.endswith(e) if e.startswith("@") else address == e for e in allowlist)


def check_send_allowed(msg: EmailMessage) -> None:
    settings = config.current()
    if not settings.allow_send:
        raise ToolError(
            "Sending is disabled (ICLOUD_ALLOW_SEND is not true). "
            "Use draft_email or reply_to_email to save a draft instead."
        )
    if settings.send_allowlist:
        recipients = addresses(msg.get("To"), msg.get("Cc"), msg.get("Bcc"))
        blocked = [r for r in recipients if not _allowed(r, settings.send_allowlist)]
        if blocked:
            raise ToolError(f"Recipients not in ICLOUD_SEND_ALLOWLIST: {', '.join(blocked)}. Save a draft instead.")


def smtp_send(msg: EmailMessage) -> dict:
    """Send via iCloud SMTP; returns refused recipients. smtplib strips Bcc before sending."""
    settings = config.current()
    try:
        with smtplib.SMTP(timeout=settings.connect_timeout) as smtp:
            smtp.connect(SMTP_HOST, SMTP_PORT)
            if smtp.sock is not None:  # connect() always sets it; this narrows the type
                smtp.sock.settimeout(settings.timeout)
            smtp.ehlo()
            # Without an explicit context, smtplib does not verify the server certificate.
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(settings.email, config.password(settings))
            return smtp.send_message(msg) or {}
    except smtplib.SMTPAuthenticationError as e:
        raise ToolError("iCloud SMTP login failed. Check ICLOUD_EMAIL and the app-specific password.") from e
    except (smtplib.SMTPException, OSError) as e:
        raise ToolError(f"iCloud SMTP error: {e}") from e


def save_draft(msg: EmailMessage) -> str:
    with imap.connection() as client:
        folder = imap.find_folder(client, b"\\Drafts", ("Drafts",)) or "Drafts"
        client.append(folder, msg.as_bytes(), flags=[b"\\Draft", b"\\Seen"])
        return folder


def send(msg: EmailMessage) -> SendResult:
    check_send_allowed(msg)
    refused = smtp_send(msg)
    result: SendResult = {"status": "sent", "to": str(msg["To"]), "subject": str(msg["Subject"])}
    if refused:
        result["status"] = "partially_sent"
        result["refused"] = {addr: f"{code} {mime.as_str(reason)}" for addr, (code, reason) in refused.items()}
    if config.current().save_sent:
        try:
            with imap.connection() as client:
                folder = imap.find_folder(client, b"\\Sent", ("Sent Messages", "Sent")) or "Sent Messages"
                client.append(folder, msg.as_bytes(), flags=[b"\\Seen"])
                result["saved_to"] = folder
        except ToolError as e:
            result["warning"] = f"Sent, but could not save a copy to Sent: {e}"
    return result


def reply_subject(subject: str) -> str:
    return subject if re.match(r"^\s*re:", subject, re.IGNORECASE) else f"Re: {subject}"


def quote(headers: EmailMessage, text: str, limit: int = 4000) -> str:
    quoted = "\n".join(f"> {line}" for line in text[:limit].splitlines())
    return f"On {headers.get('Date', '')}, {headers.get('From', '')} wrote:\n{quoted}"


def reply_recipients(original: EmailMessage, me: set[str], reply_all: bool) -> tuple[str, str | None]:
    """(to, cc) for a reply: never yourself; reply-all keeps the author if Reply-To points elsewhere."""
    sender = addresses(original.get("From"))
    if sender and sender[0] in me:
        # Replying to your own sent message: write to its original recipients.
        candidates = addresses(original.get("To"))
    else:
        candidates = addresses(str(original.get("Reply-To") or original.get("From", "")))
    to = ", ".join(a for a in dict.fromkeys(candidates) if a not in me)
    if not to:
        raise ToolError("Could not work out who to reply to (every recipient is one of your own addresses).")
    if not reply_all:
        return to, None
    primary = set(addresses(to))
    others = [
        a
        for a in addresses(original.get("From"), original.get("To"), original.get("Cc"))
        if a not in me and a not in primary
    ]
    return to, ", ".join(dict.fromkeys(others)) or None


def thread_headers(original: EmailMessage) -> tuple[str | None, str | None]:
    """(In-Reply-To, References) so the reply threads under the original."""
    orig_id = str(original.get("Message-ID", "")).strip() or None
    refs = " ".join(r for r in (str(original.get("References", "")).strip(), orig_id or "") if r) or None
    return orig_id, refs
