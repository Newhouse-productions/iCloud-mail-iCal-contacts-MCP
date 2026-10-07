"""Test doubles: a fake IMAP server and message builders.

The fake builds BODYSTRUCTURE responses itself, independently of the code under
test, so the two can't share a misunderstanding. tests/test_integration_imap.py
checks the real thing against Dovecot.
"""

from __future__ import annotations

import asyncio
import json
from email.message import EmailMessage

from icloud_mail_mcp import mime


def make_message(
    uid: int,
    subject: str,
    sender: str = "alice@example.com",
    attach: bytes | None = None,
    html: str | None = None,
    msgid: str | None = None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "me@icloud.com"
    msg["Cc"] = "bob@example.com, me@icloud.com"
    msg["Subject"] = subject
    msg["Date"] = "Mon, 05 Oct 2026 10:00:00 +0000"
    msg["Message-ID"] = msgid or f"<msg{uid}@example.com>"
    msg.set_content(f"Body of message {uid}")
    if html:
        msg.add_alternative(html, subtype="html")
    if attach is not None:
        msg.add_attachment(attach, maintype="application", subtype="pdf", filename="invoice.pdf")
    return msg.as_bytes()


def _params(part, header: str):
    """BODYSTRUCTURE-style flat parameter tuple for a header, or None."""
    params = part.get_params(header=header) or []
    out = []
    for key, value in params[1:]:
        if isinstance(value, tuple):  # RFC 2231 value: send it encoded, as servers do
            from urllib.parse import quote

            out += [f"{key}*".encode(), f"{value[0] or 'utf-8'}''{quote(value[2])}".encode()]
        else:
            out += [key.encode(), value.encode()]
    return tuple(out) or None


def _bodystructure(part):
    """A BODYSTRUCTURE like a real server's, built independently of server.py."""
    if part.is_multipart() and part.get_content_maintype() == "multipart":
        children = [_bodystructure(p) for p in part.get_payload()]
        return (children, part.get_content_subtype().encode(), None, None, None, None)
    maintype, subtype = part.get_content_maintype().encode(), part.get_content_subtype().encode()
    disposition = None
    if part.get("Content-Disposition"):
        disposition = (part.get_content_disposition().encode(), _params(part, "content-disposition"))
    encoding = str(part.get("Content-Transfer-Encoding", "7bit")).encode()
    raw = _section_bytes(part)
    base = (maintype, subtype, _params(part, "content-type"), None, None, encoding, len(raw))
    if part.get_content_type() == "message/rfc822":
        inner = part.get_payload(0)
        envelope = (None, str(inner.get("Subject", "")).encode(), None, None, None, None, None, None, None, None)
        return base + (envelope, _bodystructure(inner), raw.count(b"\n"), None, disposition, None, None)
    if maintype == b"text":
        return base + (raw.count(b"\n"), None, disposition, None, None)
    return base + (None, disposition, None, None)


def _section_bytes(part) -> bytes:
    if part.get_content_type() == "message/rfc822":
        return part.get_payload(0).as_bytes()
    payload = part.get_payload()
    return payload.encode("utf-8", "surrogateescape") if isinstance(payload, str) else b""


def _find_section(msg, section: str):
    part = msg
    for n in section.split("."):
        if part.is_multipart() and part.get_content_maintype() == "multipart":
            part = part.get_payload()[int(n) - 1]
        elif n != "1":
            raise KeyError(section)
    return part


class _FakeSocket:
    def __init__(self):
        self.timeout = 30.0

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value


class FakeIMAP:
    def __init__(self, messages: dict[int, bytes], capabilities=("MOVE", "UIDPLUS")):
        self.messages = messages
        self.flags = {uid: set() for uid in messages}
        self.capabilities = set(capabilities)
        self.calls: list = []
        self.appended: list = []
        self.selected = None
        self._socket = _FakeSocket()

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def logout(self):
        self.calls.append(("logout",))

    def noop(self):
        self.calls.append(("noop",))

    def socket(self):
        return self._socket

    def unselect_folder(self):
        self.calls.append(("unselect",))
        self.selected = None

    def list_folders(self):
        return [
            ((b"\\HasNoChildren",), b"/", "INBOX"),
            ((b"\\HasNoChildren", b"\\Drafts"), b"/", "Drafts"),
            ((b"\\HasNoChildren", b"\\Sent"), b"/", "Sent Messages"),
            ((b"\\HasNoChildren", b"\\Archive"), b"/", "Archive"),
            ((b"\\Noselect",), b"/", "[Folders]"),
        ]

    def select_folder(self, name, readonly=False):
        self.selected = (name, readonly)

    def search(self, criteria, charset=None):
        self.calls.append(("search", list(criteria), charset))
        uids = list(self.messages)
        if criteria and criteria[0] == "UID":
            wanted = {int(x) for x in criteria[1].split(",")}
            return [u for u in uids if u in wanted]
        if "UNSEEN" in criteria:
            uids = [u for u in uids if b"\\Seen" not in self.flags[u]]
        return uids

    def fetch(self, uids, items):
        self.calls.append(("fetch", list(uids), list(items)))
        assert "RFC822" not in items and "BODY.PEEK[]" not in items, "must not download whole messages"
        out = {}
        for uid in uids:
            if uid not in self.messages:  # real servers just omit unknown UIDs
                continue
            raw = self.messages[uid]
            data = {b"FLAGS": tuple(self.flags[uid]), b"RFC822.SIZE": len(raw)}
            header = raw.split(b"\n\n", 1)[0] + b"\n\n"
            for item in items:
                if item.startswith("BODY.PEEK[HEADER.FIELDS"):
                    data[b"BODY[HEADER.FIELDS (FROM TO SUBJECT DATE)]"] = header
                elif item == "BODY.PEEK[HEADER]":
                    data[b"BODY[HEADER]"] = header
                elif item == "BODY.PEEK[]":
                    data[b"BODY[]"] = raw
                elif item == "BODYSTRUCTURE":
                    data[b"BODYSTRUCTURE"] = _bodystructure(mime.parse(raw))
                elif item.startswith("BODY.PEEK["):
                    section, _, partial = item[len("BODY.PEEK[") :].partition("]")
                    chunk = _section_bytes(_find_section(mime.parse(raw), section))
                    key = f"BODY[{section}]"
                    if partial:  # "<0.N>"
                        chunk = chunk[: int(partial.strip("<>").split(".")[1])]
                        key += "<0>"
                    data[key.encode()] = chunk
            out[uid] = data
        return out

    def folder_status(self, name, items):
        if name == "INBOX":
            return {b"MESSAGES": 3, b"UNSEEN": 2}
        return {b"MESSAGES": 1, b"UNSEEN": 0}

    def append(self, folder, data, flags=()):
        self.appended.append((folder, data, flags))

    def add_flags(self, uids, flags):
        for u in uids:
            self.flags[u].update(flags)

    def remove_flags(self, uids, flags):
        for u in uids:
            self.flags[u].difference_update(flags)

    def has_capability(self, cap):
        return cap in self.capabilities

    def move(self, uids, folder):
        self.calls.append(("move", list(uids), folder))

    def copy(self, uids, folder):
        self.calls.append(("copy", list(uids), folder))

    def delete_messages(self, uids):
        self.calls.append(("delete", list(uids)))

    def uid_expunge(self, uids):
        self.calls.append(("uid_expunge", list(uids)))

    def expunge(self):
        self.calls.append(("expunge",))


def nested_attachment_message() -> bytes:
    """Apple Mail layout: alternative > [plain, mixed > [html, pdf]]."""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    mixed = MIMEMultipart("mixed")
    mixed.attach(MIMEText("<p>See attached</p>", "html"))
    pdf = MIMEApplication(b"%PDF nested", "pdf")
    pdf.add_header("Content-Disposition", "inline", filename="doc.pdf")
    mixed.attach(pdf)
    outer = MIMEMultipart("alternative")
    outer["From"] = "alice@example.com"
    outer["Subject"] = "Nested"
    outer.attach(MIMEText("See attached", "plain"))
    outer.attach(mixed)
    return outer.as_bytes()


def forwarded_email_message() -> bytes:
    inner = EmailMessage()
    inner["From"] = "carol@example.com"
    inner["Subject"] = "Original: Q3/plan"
    inner.set_content("Inner body")
    outer = EmailMessage()
    outer["From"] = "alice@example.com"
    outer["Subject"] = "Fwd"
    outer.set_content("Forwarding this")
    outer.add_attachment(inner)
    return outer.as_bytes()


def call_tool(name: str, arguments: dict | None = None):
    """Call a tool through the MCP server, the way Claude does, and check that the
    structured result carries every key the text result does (no undeclared keys)."""
    from icloud_mail_mcp import app

    app.load_tools()
    result = asyncio.run(app.mcp.call_tool(name, arguments or {}))
    if result.is_error:
        raise AssertionError(result.content[0].text)
    structured = result.structured_content
    texts = [json.loads(c.text) for c in result.content]
    if set(structured) == {"result"} and isinstance(structured["result"], list):
        assert structured["result"] == texts, f"{name}: structured result drops keys"
        return structured["result"]
    assert [structured] == texts, f"{name}: structured result drops keys"
    return structured
