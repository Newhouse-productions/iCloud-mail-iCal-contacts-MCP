"""Every tool's structured result must carry all the keys its text result does.

The MCP SDK drops keys a result type doesn't declare, silently, so a key added to a
tool but not to its TypedDict would vanish for clients that read structured output.
"""

import asyncio

from conftest import configure
from fakes import call_tool

from icloud_mail_mcp import app, compose


def test_every_tool_publishes_an_output_schema():
    tools = asyncio.run(app.load_tools().list_tools())
    assert len(tools) == 19
    assert [t.name for t in tools if not t.output_schema] == []


def test_mail_results_match_their_schemas(fake, monkeypatch):
    call_tool("list_folders")
    call_tool("unread_summary")
    call_tool("recent_emails", {"count": 2})
    call_tool("find_emails", {"subject": "Invoice"})
    call_tool("open_email", {"message_id": 2})
    call_tool("get_attachment", {"message_id": 2, "filename": "invoice.pdf"})
    assert call_tool("mark_emails", {"message_ids": [3, 99], "read": True})["not_found"] == [99]
    fake.capabilities = set()
    assert "note" in call_tool("move_emails", {"message_ids": [1], "to_folder": "Archive"})
    call_tool("archive_emails", {"message_ids": [3]})
    call_tool("draft_email", {"to": "bob@example.com", "subject": "Hi", "body": "Hello"})
    assert call_tool("reply_to_email", {"message_id": 2, "body": "Thanks"})["folder"] == "Drafts"

    configure(allow_send=True)
    monkeypatch.setattr(compose, "smtp_send", lambda msg: {"typo@exmaple.com": (550, b"No such user")})
    sent = call_tool("send_email", {"to": "a@example.com, typo@exmaple.com", "subject": "Hi", "body": "x"})
    assert sent["refused"] and sent["saved_to"] == "Sent Messages"
    replied = call_tool("reply_to_email", {"message_id": 2, "body": "Thanks", "send": True})
    assert replied["status"] == "partially_sent" and "cc" in replied
