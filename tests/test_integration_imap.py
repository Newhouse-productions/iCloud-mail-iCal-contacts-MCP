"""Integration tests against a real IMAP server (skipped unless configured).

Point them at a disposable test server, never a real mailbox:

    ICLOUD_TEST_IMAP=127.0.0.1:10143:password python -m pytest tests/test_integration_imap.py

The server must accept any username with that password over plain IMAP (e.g. a
local Dovecot with a static passdb). Each test logs in as a fresh random user,
so every run starts with empty mailboxes.
"""

import os
import uuid
from email.message import EmailMessage
from pathlib import Path

import pytest
from conftest import configure
from fakes import forwarded_email_message, make_message, nested_attachment_message
from imapclient import IMAPClient
from mcp.server.mcpserver.exceptions import ToolError

from icloud_mail_mcp import imap as imap_module
from icloud_mail_mcp import mail

CONFIG = os.getenv("ICLOUD_TEST_IMAP")
pytestmark = pytest.mark.skipif(not CONFIG, reason="set ICLOUD_TEST_IMAP=host:port:password to run")


@pytest.fixture
def imap(monkeypatch):
    host, port, password = CONFIG.split(":", 2)
    user = f"t{uuid.uuid4().hex[:10]}"

    def connect(settings=None):
        return IMAPClient(host, port=int(port), ssl=False, timeout=10)

    monkeypatch.setattr(imap_module, "new_client", connect)
    configure(imap_user=user, app_password=password)

    admin = connect()
    admin.login(user, password)
    folders = {name for _, _, name in admin.list_folders()}
    for name in ("Drafts", "Sent Messages", "Archive"):
        if name not in folders:
            admin.create_folder(name)

    def add(raw: bytes, folder: str = "INBOX", flags=()) -> int:
        admin.append(folder, raw, flags=flags)
        admin.select_folder(folder)
        return max(admin.search(["ALL"]))

    yield add, admin
    admin.logout()


def _single_part_pdf() -> bytes:
    msg = EmailMessage()
    msg["From"] = "scanner@example.com"
    msg["Subject"] = "Scan"
    msg.set_content(b"%PDF scan", maintype="application", subtype="pdf", disposition="attachment", filename="scan.pdf")
    return msg.as_bytes()


def _utf8_message() -> bytes:
    msg = EmailMessage()
    msg["From"] = "zoë@example.com"
    msg["Subject"] = "Grüße aus Köln"
    msg.set_content("Grüße — café, über alles", charset="utf-8")
    msg.add_attachment(b"resume", maintype="application", subtype="pdf", filename="résumé très long.pdf")
    msg.add_attachment(b"a", maintype="image", subtype="png", filename="photo.png")
    msg.add_attachment(b"b", maintype="image", subtype="png", filename="photo.png")
    return msg.as_bytes()


def _html_only() -> bytes:
    msg = EmailMessage()
    msg["From"] = "news@example.com"
    msg["Subject"] = "Newsletter"
    msg.set_content("<html><style>p{color:red}</style><p>Big&nbsp;news</p><script>x()</script></html>", subtype="html")
    return msg.as_bytes()


def test_list_search_and_unread(imap):
    add, _ = imap
    add(make_message(1, "Old"), flags=[b"\\Seen"])
    add(make_message(2, "Invoice", attach=b"%PDF"))
    add(_utf8_message())
    result = mail.recent_emails()
    assert result["total"] == 3 and result["messages"][0]["subject"] == "Grüße aus Köln"
    assert [m["unread"] for m in result["messages"]] == [True, True, False]
    assert mail.find_emails(subject_has="Köln")["total"] == 1
    assert mail.find_emails(subject_has="Invoice", unread_only=True)["total"] == 1
    assert mail.unread_summary() == [{"folder": "INBOX", "unread": 2, "total": 3}]


def test_read_does_not_mark_seen(imap):
    add, admin = imap
    uid = add(make_message(1, "Hello"))
    assert mail.open_email(uid)["body"] == "Body of message 1"
    admin.select_folder("INBOX")
    assert b"\\Seen" not in admin.get_flags([uid])[uid]


def test_open_email_kinds(imap):
    add, _ = imap
    nested = mail.open_email(add(nested_attachment_message()))
    assert nested["body"] == "See attached"
    assert [a["filename"] for a in nested["attachments"]] == ["doc.pdf"]

    fwd = mail.open_email(add(forwarded_email_message()))
    assert fwd["body"] == "Forwarding this"
    assert fwd["attachments"][0]["filename"] == "Original_ Q3_plan.eml"

    scan = mail.open_email(add(_single_part_pdf()))
    assert scan["body"] == "" and scan["attachments"][0]["filename"] == "scan.pdf"

    utf8 = mail.open_email(add(_utf8_message()))
    assert utf8["body"] == "Grüße — café, über alles"
    assert [a["filename"] for a in utf8["attachments"]] == ["résumé très long.pdf", "photo.png", "photo.png"]

    html = mail.open_email(add(_html_only()))
    assert html["body"] == "Big\xa0news"


def test_get_attachment_kinds(imap):
    add, _ = imap
    assert Path(mail.get_attachment(add(nested_attachment_message()), "doc.pdf")["path"]).read_bytes() == b"%PDF nested"
    eml = Path(mail.get_attachment(add(forwarded_email_message()), "Original_ Q3_plan.eml")["path"]).read_bytes()
    assert b"Subject: Original: Q3/plan" in eml and b"Inner body" in eml
    assert Path(mail.get_attachment(add(_single_part_pdf()), "scan.pdf")["path"]).read_bytes() == b"%PDF scan"

    uid = add(_utf8_message())
    saved = Path(mail.get_attachment(uid, "résumé très long.pdf")["path"])
    assert saved.name == "résumé très long.pdf" and saved.read_bytes() == b"resume"
    with pytest.raises(ToolError, match="Pass index"):
        mail.get_attachment(uid, "photo.png")
    assert Path(mail.get_attachment(uid, "photo.png", index=3)["path"]).read_bytes() == b"b"


def test_attachment_cap_uses_part_size(imap):
    add, _ = imap
    msg = EmailMessage()
    msg["Subject"] = "Mixed sizes"
    msg.set_content("see attached")
    msg.add_attachment(b"%PDF small", maintype="application", subtype="pdf", filename="invoice.pdf")
    msg.add_attachment(os.urandom(3 * 1024 * 1024), maintype="video", subtype="mp4", filename="clip.mp4")
    uid = add(msg.as_bytes())
    configure(max_attachment_mb=1)
    assert Path(mail.get_attachment(uid, "invoice.pdf")["path"]).read_bytes() == b"%PDF small"
    with pytest.raises(ToolError, match="over the ICLOUD_MAX_ATTACHMENT_MB"):
        mail.get_attachment(uid, "clip.mp4")


def test_long_body_is_fetched_partially(imap):
    add, _ = imap
    msg = EmailMessage()
    msg["Subject"] = "Huge"
    msg.set_content("word " * 400_000)  # ~2 MB of text
    uid = add(msg.as_bytes())
    result = mail.open_email(uid, max_chars=100)
    assert result["truncated"] is True and len(result["body"]) == 100


def test_fallback_without_bodystructure(imap, monkeypatch):
    add, _ = imap

    def broken(*args, **kwargs):
        raise IndexError("odd server")

    monkeypatch.setattr(imap_module.mime, "structure_parts", broken)
    uid = add(nested_attachment_message())
    assert mail.open_email(uid)["attachments"][0]["filename"] == "doc.pdf"
    assert Path(mail.get_attachment(uid, "doc.pdf")["path"]).read_bytes() == b"%PDF nested"


def test_mark_move_archive(imap):
    add, admin = imap
    a, b = add(make_message(1, "A")), add(make_message(2, "B"))
    result = mail.mark_emails([a, 999], read=True, flagged=True)
    assert result["not_found"] == [999]
    admin.select_folder("INBOX")
    assert {b"\\Seen", b"\\Flagged"} <= set(admin.get_flags([a])[a])

    assert mail.archive_emails([a])["to"] == "Archive"
    assert mail.move_emails([b], "archive")["to"] == "Archive"  # case-insensitive
    assert mail.recent_emails()["total"] == 0
    assert mail.recent_emails(folder="Archive")["total"] == 2
    archived = [m["id"] for m in mail.recent_emails(folder="Archive")["messages"]]
    moved_back = mail.move_emails(archived, "inbox", folder="Archive")
    assert moved_back["to"] == "INBOX" and mail.recent_emails()["total"] == 2


def test_drafts_and_reply_threading(imap):
    add, admin = imap
    uid = add(make_message(7, "Plans", sender="Alice <alice@example.com>", msgid="<plans@example.com>"))
    mail.draft_email("bob@example.com", "Hi", "Hello")
    reply = mail.reply_to_email(uid, "Sounds good", reply_all=True)
    assert reply["to"] == "alice@example.com" and reply["cc"] == "bob@example.com"

    drafts = mail.recent_emails(folder="Drafts")["messages"]
    assert [d["subject"] for d in drafts] == ["Re: Plans", "Hi"]
    admin.select_folder("Drafts")
    assert b"\\Draft" in admin.get_flags([drafts[0]["id"]])[drafts[0]["id"]]
    headers = mail.open_email(drafts[0]["id"], folder="Drafts")
    assert "> Body of message 7" in headers["body"]
    raw = admin.fetch([drafts[0]["id"]], ["BODY.PEEK[HEADER]"])[drafts[0]["id"]][b"BODY[HEADER]"]
    assert b"In-Reply-To: <plans@example.com>" in raw and b"References: <plans@example.com>" in raw


def test_errors_from_real_server(imap):
    with pytest.raises(ToolError, match="not found"):
        mail.open_email(12345)
    with pytest.raises(ToolError, match="does not exist"):
        mail.move_emails([1], "Nope")
    with pytest.raises(ToolError, match="iCloud IMAP error"):
        mail.recent_emails(folder="Missing Folder")
