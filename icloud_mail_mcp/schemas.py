"""Result shapes for every tool.

The MCP SDK publishes these as each tool's output schema, and its structured result
keeps only the keys declared here, so every key a tool returns must be listed.
tests/test_schemas.py checks that. typing_extensions.TypedDict is required: pydantic
rejects typing.TypedDict before Python 3.12.
"""

from __future__ import annotations

from typing_extensions import NotRequired, TypedDict

# --- Mail ---------------------------------------------------------------------


class Folder(TypedDict):
    name: str
    flags: list[str]


class UnreadCount(TypedDict):
    folder: str
    unread: int
    total: int


# "from" is a keyword, so these use the functional syntax.
MessageSummary = TypedDict(
    "MessageSummary",
    {
        "id": int,
        "from": str,
        "to": str,
        "subject": str,
        "date": str,
        "size_kb": float,
        "unread": bool,
        "flagged": bool,
    },
)


class MessagePage(TypedDict):
    total: int
    offset: int
    returned: int
    messages: list[MessageSummary]


MessageHeaders = TypedDict(
    "MessageHeaders",
    {
        "from": str,
        "to": str,
        "cc": str,
        "reply_to": str,
        "subject": str,
        "date": str,
        "message_id": str,
    },
)


class AttachmentInfo(TypedDict):
    index: int
    filename: str
    content_type: str
    size_kb: float


class Message(TypedDict):
    id: int
    folder: str
    headers: MessageHeaders
    security_note: str
    body: str
    truncated: bool
    attachments: list[AttachmentInfo]


class SavedFile(TypedDict):
    status: str
    path: str
    size_kb: float


class MarkResult(TypedDict):
    status: str
    folder: str
    ids: list[int]
    read: bool | None
    flagged: bool | None
    not_found: NotRequired[list[int]]


MoveResult = TypedDict(
    "MoveResult",
    {
        "status": str,
        "from": str,
        "to": str,
        "ids": list[int],
        "note": NotRequired[str],
        "not_found": NotRequired[list[int]],
    },
)


class DraftResult(TypedDict):
    status: str
    folder: str
    to: str
    subject: str


class SendResult(TypedDict):
    status: str  # "sent" or "partially_sent"
    to: str
    subject: str
    saved_to: NotRequired[str]
    warning: NotRequired[str]
    refused: NotRequired[dict[str, str]]


class ReplyResult(TypedDict):
    """A reply is either saved as a draft (folder) or sent (the SendResult keys)."""

    status: str  # "draft_created", "sent" or "partially_sent"
    to: str
    cc: str | None
    subject: str
    folder: NotRequired[str]
    saved_to: NotRequired[str]
    warning: NotRequired[str]
    refused: NotRequired[dict[str, str]]


# --- Calendar -----------------------------------------------------------------


class CalendarInfo(TypedDict):
    name: str
    writable: bool


class Event(TypedDict):
    id: str
    calendar: str
    title: str
    start: str
    end: str
    all_day: bool
    location: str
    notes: str
    recurring: bool


class TimeRange(TypedDict):
    start: str
    end: str


class EventPage(TypedDict):
    range: TimeRange
    time_zone: str
    security_note: str
    total: int
    truncated: bool
    events: list[Event]
    warning: NotRequired[str]


class CreatedEvent(TypedDict):
    status: str
    id: str
    calendar: str
    title: str
    start: str
    end: str
    all_day: bool


class DeletedEvent(TypedDict):
    status: str
    id: str
    calendar: str


# --- Contacts -----------------------------------------------------------------


class EmailAddress(TypedDict):
    address: str
    type: str | None


class PhoneNumber(TypedDict):
    number: str
    type: str | None


class PostalAddress(TypedDict):
    address: str
    type: str | None


class ContactBrief(TypedDict):
    id: str
    name: str
    organization: str
    emails: list[EmailAddress]
    phones: list[PhoneNumber]


class Contact(TypedDict):
    id: str
    name: str
    organization: str
    title: str
    emails: list[EmailAddress]
    phones: list[PhoneNumber]
    addresses: list[PostalAddress]
    birthday: str
    note: str


class ContactDetails(Contact):
    security_note: str


class ContactPage(TypedDict):
    total: int
    contacts: list[ContactBrief]


class CreatedContact(TypedDict):
    status: str
    id: str
    name: str
