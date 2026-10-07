"""Mail tools against a fake IMAP server, so no iCloud account is needed."""

from email.message import EmailMessage
from pathlib import Path

import pytest
from conftest import configure
from fakes import forwarded_email_message, make_message, nested_attachment_message
from mcp.server.mcpserver.exceptions import ToolError

from icloud_mail_mcp import app, compose, config, mail, mime


def test_recent_emails_is_header_only_newest_first(fake):
    result = mail.recent_emails(limit=2)
    assert result["total"] == 3
    assert [m["id"] for m in result["messages"]] == [3, 2]
    assert result["messages"][0]["subject"] == "Newest"
    assert result["messages"][0]["unread"] is True
    assert fake.selected == ("INBOX", True)


def test_paging_and_unread_only(fake):
    assert [m["id"] for m in mail.recent_emails(limit=2, offset=2)["messages"]] == [1]
    assert mail.recent_emails(unread_only=True)["total"] == 2


def test_search_builds_criteria(fake):
    mail.find_emails(sender="alice", start_date="2026-10-01", unread_only=True)
    search = [c for c in fake.calls if c[0] == "search"][-1]
    assert search[1] == ["FROM", "alice", "SINCE", "01-Oct-2026", "UNSEEN"]
    assert search[2] is None


def test_search_non_ascii_uses_utf8(fake):
    mail.find_emails(subject_has="café")
    assert [c for c in fake.calls if c[0] == "search"][-1][2] == "UTF-8"


def test_bad_date_is_an_error(fake):
    with pytest.raises(ToolError, match="YYYY-MM-DD"):
        mail.find_emails(start_date="last week")


def test_open_email_marks_untrusted_and_lists_attachments(fake):
    result = mail.open_email(2)
    assert result["body"] == "Body of message 2"
    assert "untrusted" in result["security_note"]
    assert result["attachments"][0]["filename"] == "invoice.pdf"


def test_open_email_truncates(fake):
    fake.messages[1] = make_message(1, "Long").replace(b"Body of message 1", b"x" * 5000)
    result = mail.open_email(1, max_chars=1000)
    assert len(result["body"]) == 1000 and result["truncated"] is True


def test_missing_message(fake):
    with pytest.raises(ToolError, match="not found"):
        mail.open_email(99)


def test_list_folders_decodes_flags(fake):
    drafts = next(f for f in mail.list_folders() if f["name"] == "Drafts")
    assert "\\Drafts" in drafts["flags"]


def test_unread_summary(fake):
    assert mail.unread_summary() == [{"folder": "INBOX", "unread": 2, "total": 3}]


def test_draft_email_uses_drafts_flag(fake):
    result = mail.draft_email("bob@example.com", "Hi", "Hello")
    assert result["folder"] == "Drafts"
    folder, data, flags = fake.appended[0]
    assert folder == "Drafts" and b"\\Draft" in flags and b"Message-ID" in data


def test_send_disabled_by_default(fake):
    with pytest.raises(ToolError, match="disabled"):
        mail.send_email("bob@example.com", "Hi", "Hello")


def test_send_allowlist(fake, monkeypatch):
    sent = []
    configure(allow_send=True)
    configure(send_allowlist=("@example.com",))
    monkeypatch.setattr(compose, "smtp_send", sent.append)
    with pytest.raises(ToolError, match="evil@attacker.test"):
        mail.send_email("bob@example.com", "Hi", "Hello", bcc="evil@attacker.test")
    result = mail.send_email("bob@example.com", "Hi", "Hello")
    assert result["status"] == "sent" and result["saved_to"] == "Sent Messages"
    assert len(sent) == 1


def test_reply_threads_and_saves_draft(fake):
    result = mail.reply_to_email(2, "Thanks!", reply_all=True)
    assert result["status"] == "draft_created"
    assert result["to"] == "alice@example.com" and result["cc"] == "bob@example.com"
    msg = mime.parse(fake.appended[0][1])
    assert msg["Subject"] == "Re: Invoice"
    assert msg["In-Reply-To"] == "<msg2@example.com>"
    assert "> Body of message 2" in msg.get_body(("plain",)).get_content()


def test_reply_send_respects_guardrail(fake):
    with pytest.raises(ToolError, match="disabled"):
        mail.reply_to_email(2, "Thanks!", send=True)


def test_mark_and_move(fake):
    mail.mark_emails([3], read=True, flagged=True)
    assert {b"\\Seen", b"\\Flagged"} <= fake.flags[3]
    assert fake.selected == ("INBOX", False)
    mail.archive_emails([3])
    assert ("move", [3], "Archive") in fake.calls


def test_move_fallback_without_move_capability(fake):
    fake.capabilities = {"UIDPLUS"}
    mail.move_emails([1], "Archive")
    assert ("copy", [1], "Archive") in fake.calls and ("uid_expunge", [1]) in fake.calls


def test_move_to_unknown_folder(fake):
    with pytest.raises(ToolError, match="does not exist"):
        mail.move_emails([1], "Nope")


def test_get_attachment_saves_safely(fake, tmp_path):
    first = mail.get_attachment(2, "invoice.pdf")
    second = mail.get_attachment(2, "invoice.pdf")
    assert Path(first["path"]).read_bytes() == b"%PDF-1.4 fake"
    assert Path(second["path"]).name == "invoice (1).pdf"
    with pytest.raises(ToolError, match="No attachment"):
        mail.get_attachment(2, "../../etc/passwd")


def test_login_failure_is_friendly(fake, monkeypatch):
    from imapclient.exceptions import LoginError

    def bad_login(user, password):
        raise LoginError("AUTHENTICATIONFAILED")

    monkeypatch.setattr(fake, "login", bad_login)
    with pytest.raises(ToolError, match="login failed"):
        mail.list_folders()
    assert ("logout",) in fake.calls


def test_tools_registered_with_annotations():
    import asyncio

    tools = {t.name: t for t in asyncio.run(app.load_tools().list_tools())}
    assert {"recent_emails", "reply_to_email", "send_email", "get_attachment", "archive_emails"} <= set(tools)
    assert tools["open_email"].annotations.read_only_hint is True
    assert tools["send_email"].annotations.destructive_hint is True
    assert tools["reply_to_email"].annotations.destructive_hint is True
    assert tools["draft_email"].annotations.idempotent_hint is False


def test_nested_attachments_listed_and_saved(fake):
    fake.messages[4] = nested_attachment_message()
    fake.flags[4] = set()
    assert [a["filename"] for a in mail.open_email(4)["attachments"]] == ["doc.pdf"]
    saved = mail.get_attachment(4, "doc.pdf")
    assert Path(saved["path"]).read_bytes() == b"%PDF nested"


def test_attached_email_saved_as_eml(fake):
    fake.messages[5] = forwarded_email_message()
    fake.flags[5] = set()
    attachments = mail.open_email(5)["attachments"]
    assert attachments[0]["filename"] == "Original_ Q3_plan.eml" and attachments[0]["size_kb"] > 0
    saved = Path(mail.get_attachment(5, "Original_ Q3_plan.eml")["path"])
    assert b"Inner body" in saved.read_bytes()


def test_attachment_size_checked_before_download(fake, monkeypatch):
    configure(max_attachment_mb=0)
    with pytest.raises(ToolError, match="over the ICLOUD_MAX_ATTACHMENT_MB"):
        mail.get_attachment(2, "invoice.pdf")
    fetched = [item for c in fake.calls if c[0] == "fetch" for item in c[2]]
    assert fetched == ["BODY.PEEK[HEADER]", "BODYSTRUCTURE", "RFC822.SIZE"]  # the part itself never fetched


def test_read_fetches_only_the_text_part(fake):
    mail.open_email(2)
    fetched = [item for c in fake.calls if c[0] == "fetch" for item in c[2]]
    assert fetched == ["BODY.PEEK[HEADER]", "BODYSTRUCTURE", "RFC822.SIZE", "BODY.PEEK[1]"]


def test_single_part_attachment_and_duplicate_names(fake):
    scan = EmailMessage()
    scan["Subject"] = "Scan"
    scan.set_content(b"%PDF scan", maintype="application", subtype="pdf", disposition="attachment", filename="scan.pdf")
    fake.messages[7], fake.flags[7] = scan.as_bytes(), set()
    assert mail.open_email(7)["attachments"][0]["filename"] == "scan.pdf"
    assert Path(mail.get_attachment(7, "scan.pdf")["path"]).read_bytes() == b"%PDF scan"

    dup = EmailMessage()
    dup["Subject"] = "Photos"
    dup.set_content("two photos")
    dup.add_attachment(b"one", maintype="image", subtype="png", filename="photo.png")
    dup.add_attachment(b"two", maintype="image", subtype="png", filename="photo.png")
    fake.messages[8], fake.flags[8] = dup.as_bytes(), set()
    assert [a["index"] for a in mail.open_email(8)["attachments"]] == [1, 2]
    with pytest.raises(ToolError, match="Pass index"):
        mail.get_attachment(8, "photo.png")
    assert Path(mail.get_attachment(8, index=2)["path"]).read_bytes() == b"two"
    with pytest.raises(ToolError, match="out of range"):
        mail.get_attachment(8, index=3)


def test_move_resolves_mailbox_names(fake):
    assert mail.move_emails([1], "archive")["to"] == "Archive"
    with pytest.raises(ToolError, match="folder group"):
        mail.move_emails([1], "[Folders]")


def test_reply_all_keeps_author_when_reply_to_differs(fake):
    msg = EmailMessage()
    msg["From"] = "bob@corp.com"
    msg["Reply-To"] = "team-list@corp.com"
    msg["To"] = "me@icloud.com"
    msg["Cc"] = "carol@corp.com"
    msg["Subject"] = "Team"
    msg.set_content("Thoughts?")
    fake.messages[9], fake.flags[9] = msg.as_bytes(), set()
    result = mail.reply_to_email(9, "Agreed", reply_all=True)
    assert result["to"] == "team-list@corp.com"
    assert result["cc"] == "bob@corp.com, carol@corp.com"


def test_partially_refused_send_is_reported(fake, monkeypatch):
    configure(allow_send=True)
    monkeypatch.setattr(compose, "smtp_send", lambda msg: {"typo@exmaple.com": (550, b"No such user")})
    result = mail.send_email("alice@example.com, typo@exmaple.com", "Hi", "Hello")
    assert result["status"] == "partially_sent"
    assert result["refused"] == {"typo@exmaple.com": "550 No such user"}


def test_move_fallback_without_uidplus_never_expunges_everything(fake):
    fake.capabilities = set()
    result = mail.move_emails([1], "Archive")
    assert ("expunge",) not in fake.calls
    assert "marked deleted" in result["note"]


def test_missing_ids_are_reported(fake):
    result = mail.mark_emails([3, 42], read=True)
    assert result["ids"] == [3] and result["not_found"] == [42]
    with pytest.raises(ToolError, match="None of the IDs"):
        mail.archive_emails([41, 42])


def test_reply_never_addresses_yourself(fake, monkeypatch):
    configure(aliases=("hello@mydomain.com",))
    sent = EmailMessage()
    sent["From"] = "Me <me@icloud.com>"
    sent["To"] = "dave@example.com"
    sent["Cc"] = "me@me.com, hello@mydomain.com, erin@example.com"
    sent["Subject"] = "Plans"
    sent["Message-ID"] = "<sent1@icloud.com>"
    sent.set_content("Shall we meet?")
    fake.messages[6] = sent.as_bytes()
    fake.flags[6] = set()
    result = mail.reply_to_email(6, "Following up", reply_all=True)
    assert result["to"] == "dave@example.com"
    assert result["cc"] == "erin@example.com"


def test_multiline_header_is_a_friendly_error(fake):
    with pytest.raises(ToolError, match="single line"):
        mail.draft_email("bob@example.com", "Hello\nWorld", "Body")


def test_open_email_honours_small_max_chars(fake):
    assert mail.open_email(2, max_chars=4)["body"] == "Body"


def test_smtp_uses_connect_timeout(monkeypatch):
    events = []

    class FakeSock:
        def settimeout(self, t):
            events.append(("read_timeout", t))

    class FakeSMTP:
        def __init__(self, timeout):
            events.append(("connect_timeout", timeout))
            self.sock = FakeSock()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def connect(self, host, port):
            events.append(("connect", host, port))

        def ehlo(self):
            pass

        def starttls(self, context=None):
            events.append(("tls_verify", context.verify_mode, context.check_hostname))

        def login(self, user, pw):
            pass

        def send_message(self, msg):
            events.append(("sent",))
            return {}

    monkeypatch.setattr(compose.smtplib, "SMTP", FakeSMTP)
    compose.smtp_send(EmailMessage())
    import ssl

    assert events[:4] == [
        ("connect_timeout", config.current().connect_timeout),
        ("connect", "smtp.mail.me.com", 587),
        ("read_timeout", config.current().timeout),
        ("tls_verify", ssl.CERT_REQUIRED, True),
    ]


def test_read_only_mode_blocks_every_change(fake):
    configure(read_only=True, allow_send=True)
    for call in (
        lambda: mail.mark_emails([1], read=True),
        lambda: mail.move_emails([1], "Archive"),
        lambda: mail.archive_emails([1]),
        lambda: mail.draft_email("bob@example.com", "Hi", "Hello"),
        lambda: mail.reply_to_email(2, "Thanks"),
        lambda: mail.send_email("bob@example.com", "Hi", "Hello"),
        lambda: mail.get_attachment(2, "invoice.pdf"),
    ):
        with pytest.raises(ToolError, match="Read-only mode"):
            call()
    assert fake.appended == [] and mail.open_email(2)["body"]  # reading still works


def test_change_calls_are_capped(fake):
    with pytest.raises(ToolError, match="At most 50 messages"):
        mail.archive_emails(list(range(1, 52)))
