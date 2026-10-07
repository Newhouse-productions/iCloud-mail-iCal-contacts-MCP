"""iCloud IMAP: one shared connection, message listing, and part-by-part message access."""

from __future__ import annotations

import atexit
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Protocol

from imapclient import IMAPClient, SocketTimeout
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError, LoginError
from mcp.server.mcpserver.exceptions import ToolError

from . import config, mime
from .schemas import MessagePage, MessageSummary

log = logging.getLogger("icloud-mail")

IMAP_HOST = "imap.mail.me.com"
IMAP_PORT = 993
# Bytes of a text body to download; anything longer is truncated anyway.
BODY_FETCH_BYTES = 512 * 1024
FULL_DOWNLOAD_LIMIT = 50 * 1024 * 1024
SUMMARY_FIELDS = "BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)]"
NOT_FOUND = "Message {id} not found. IDs are per mailbox; check the mailbox name."
# Most messages one mark/move/archive call may change, so one instruction can't sweep a mailbox.
MAX_IDS_PER_CALL = 50


def new_client(settings: config.Settings) -> IMAPClient:
    """Connect (not log in). Tests replace this to point at a fake or local server."""
    timeout = SocketTimeout(connect=settings.connect_timeout, read=settings.timeout)
    return IMAPClient(IMAP_HOST, port=IMAP_PORT, ssl=True, timeout=timeout)


class _Conn:
    """A logged-in client and when it was last used."""

    def __init__(self, client: IMAPClient, account: tuple[str, str]):
        self.client, self.account = client, account
        self.last_used = time.monotonic()


class Pool:
    """A few logged-in IMAP connections, reused across tool calls.

    Logging in costs a TLS handshake and an authentication round trip, and frequent
    logins can trip Apple's rate limits, so connections are kept open. Tools run on
    worker threads; each call borrows one connection for its duration, so a long
    attachment download doesn't hold up other mail tools.
    """

    MAX_CONNECTIONS = 3
    # A connection unused for longer than this is checked with NOOP before reuse.
    IDLE_CHECK_SECONDS = 15
    # ...and the check gives up quickly, so a dead socket (e.g. after sleep) can't stall a call.
    CHECK_TIMEOUT_SECONDS = 5

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._idle: list[_Conn] = []
        self._open = 0  # idle + borrowed

    @contextmanager
    def connection(self) -> Iterator[IMAPClient]:
        settings = config.current()
        if not settings.imap_user:
            raise ToolError("ICLOUD_EMAIL is not set. Run `icloud-mail-mcp --setup`.")
        account = (settings.imap_user, IMAP_HOST)
        conn = self._borrow(settings, account)
        healthy = True
        started = time.monotonic()
        try:
            yield conn.client
        except ToolError:
            raise
        except (IMAPClientAbortError, OSError) as e:
            healthy = False  # the connection itself broke; replace it next time
            raise ToolError(f"Lost the connection to iCloud IMAP ({e}). Try again.") from e
        except IMAPClientError as e:
            raise ToolError(f"iCloud IMAP error: {e}") from e  # a NO/BAD reply: the connection is fine
        finally:
            log.debug("IMAP work took %.0f ms", (time.monotonic() - started) * 1000)
            self._give_back(conn, healthy)

    def _borrow(self, settings: config.Settings, account: tuple[str, str]) -> _Conn:
        while True:
            with self._cond:
                while not self._idle and self._open >= self.MAX_CONNECTIONS:
                    self._cond.wait()
                conn = self._idle.pop() if self._idle else None
                if conn is None:
                    self._open += 1  # reserve a slot, then connect outside the lock
            if conn is None:
                try:
                    return _Conn(_login(settings), account)
                except BaseException:
                    self._release_slot()
                    raise
            if conn.account == account and self._usable(conn):
                return conn
            _logout(conn.client)
            self._release_slot()

    def _usable(self, conn: _Conn) -> bool:
        if time.monotonic() - conn.last_used < self.IDLE_CHECK_SECONDS:
            return True
        sock = conn.client.socket()
        previous = sock.gettimeout()
        try:
            sock.settimeout(self.CHECK_TIMEOUT_SECONDS)
            conn.client.noop()
            return True
        except (IMAPClientError, OSError):
            log.debug("IMAP connection went stale; reconnecting")
            return False
        finally:
            try:
                sock.settimeout(previous)
            except OSError:
                pass

    def _give_back(self, conn: _Conn, healthy: bool) -> None:
        if not healthy:
            _logout(conn.client)
            self._release_slot()
            return
        conn.last_used = time.monotonic()
        with self._cond:
            self._idle.append(conn)
            self._cond.notify()

    def _release_slot(self) -> None:
        with self._cond:
            self._open -= 1
            self._cond.notify()

    def close(self) -> None:
        """Log out idle connections. Borrowed ones are closed when returned."""
        with self._cond:
            idle, self._idle = self._idle, []
            self._open -= len(idle)
            self._cond.notify_all()
        for conn in idle:
            _logout(conn.client)


def _login(settings: config.Settings) -> IMAPClient:
    started = time.monotonic()
    try:
        client = new_client(settings)
    except OSError as e:
        raise ToolError(f"Could not reach iCloud IMAP ({IMAP_HOST}): {e}") from e
    try:
        client.login(settings.imap_user, config.password(settings))
    except LoginError as e:
        _logout(client)
        raise ToolError(
            "iCloud login failed. Check ICLOUD_IMAP_USER (usually the part of your "
            "address before @icloud.com) and the app-specific password."
        ) from e
    except ToolError:
        _logout(client)
        raise
    except (IMAPClientError, OSError) as e:
        _logout(client)
        raise ToolError(f"iCloud IMAP error: {e}") from e
    log.debug("IMAP login took %.0f ms", (time.monotonic() - started) * 1000)
    return client


def unselect(client: IMAPClient) -> None:
    """Leave no mailbox selected, before STATUS (which RFC 3501 says not to send for the
    selected mailbox). Done only where needed, not after every call: it's a round trip.
    Never CLOSE instead: that would expunge messages marked deleted."""
    try:
        if client.has_capability("UNSELECT"):
            client.unselect_folder()
    except IMAPClientAbortError:
        raise
    except IMAPClientError:
        pass  # nothing was selected


def _logout(client: IMAPClient, timeout: float = 5) -> None:
    try:
        client.socket().settimeout(timeout)
    except Exception:
        pass
    try:
        client.logout()
    except Exception:
        pass


POOL = Pool()
atexit.register(POOL.close)  # idle connections only, with a short timeout: never blocks exit


def connection():
    """A logged-in connection from the pool, as a context manager."""
    return POOL.connection()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def fetch_value(data: dict, prefix: bytes) -> Any:
    """Find a FETCH response item by key prefix (servers vary the exact key text)."""
    for key, val in data.items():
        if isinstance(key, bytes) and key.upper().startswith(prefix):
            return val
    return None


def flag_names(flags) -> list[str]:
    return [f.decode() if isinstance(f, bytes) else str(f) for f in flags]


def search_date(value: str | None, field: str) -> str | None:
    """YYYY-MM-DD -> the DD-Mon-YYYY form IMAP SEARCH expects."""
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").strftime("%d-%b-%Y")
    except ValueError as e:
        raise ToolError(f"{field} must be a date in YYYY-MM-DD format, got {value!r}.") from e


def clamp(count: int, low: int = 1, high: int = 100) -> int:
    return min(max(low, count), high)


def find_folder(client, flag: bytes, names: tuple[str, ...]) -> str | None:
    """Find a special-use folder by flag (e.g. \\Drafts), then by common names."""
    folders = client.list_folders()
    for flags, _, name in folders:
        if flag in flags:
            return name
    lowered = {name.lower(): name for _, _, name in folders}
    for candidate in names:
        if candidate.lower() in lowered:
            return lowered[candidate.lower()]
    return None


def resolve_mailbox(client, name: str) -> str:
    """The server's exact name for a mailbox the user typed (INBOX and case differences allowed)."""
    folders = [(flags, n) for flags, _, n in client.list_folders()]
    matches = [(flags, n) for flags, n in folders if n == name]
    if not matches:
        matches = [(flags, n) for flags, n in folders if n.lower() == name.lower()]
    if len(matches) != 1:
        raise ToolError(f"Mailbox {name!r} does not exist. Use list_folders to see names.")
    flags, resolved = matches[0]
    if b"\\Noselect" in flags or b"\\NonExistent" in flags:
        raise ToolError(f"{resolved!r} is a folder group, not a mailbox that can hold messages.")
    return resolved


def existing(client, message_ids: list[int], mailbox: str) -> tuple[list[int], list[int]]:
    """Split IDs into those present in the selected mailbox and those missing."""
    if not message_ids:
        raise ToolError("message_ids is empty.")
    if len(message_ids) > MAX_IDS_PER_CALL:
        raise ToolError(f"At most {MAX_IDS_PER_CALL} messages per call; split the request.")
    found = set(client.search(["UID", ",".join(str(i) for i in message_ids)]))
    present = [i for i in message_ids if i in found]
    missing = [i for i in message_ids if i not in found]
    if not present:
        raise ToolError(
            f"None of the IDs {message_ids} exist in {mailbox!r}. IDs are per mailbox; pass the mailbox they came from."
        )
    return present, missing


def move(client, message_ids: list[int], target: str) -> str | None:
    """Move messages; returns a note if originals could not be removed safely."""
    if client.has_capability("MOVE"):
        client.move(message_ids, target)
        return None
    client.copy(message_ids, target)
    client.delete_messages(message_ids)
    if client.has_capability("UIDPLUS"):
        client.uid_expunge(message_ids)
        return None
    # A plain EXPUNGE would also purge every other message already marked deleted.
    return "Copied; the originals are marked deleted and will disappear when the mailbox is next expunged."


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def header_text(header, name: str) -> str:
    """A header as text, or "" when it's missing."""
    value = header[name]
    return "" if value is None else str(value)


def summaries(client, uids: list[int]) -> list[MessageSummary]:
    """Header-only summaries: never downloads bodies or attachments."""
    if not uids:
        return []
    fetched = client.fetch(uids, [SUMMARY_FIELDS, "RFC822.SIZE", "FLAGS"])
    results: list[MessageSummary] = []
    for message_uid in uids:
        data = fetched.get(message_uid)
        if not data:
            continue
        header = mime.parse(fetch_value(data, b"BODY[HEADER") or b"")
        flags = data.get(b"FLAGS", ())
        results.append(
            {
                "id": message_uid,
                "from": header_text(header, "From"),
                "to": header_text(header, "To"),
                "subject": header_text(header, "Subject"),
                "date": header_text(header, "Date"),
                "size_kb": round(data.get(b"RFC822.SIZE", 0) / 1024, 1),
                "unread": b"\\Seen" not in flags,
                "flagged": b"\\Flagged" in flags,
            }
        )
    return results


def page(client, criteria: list, offset: int, count: int) -> MessagePage:
    charset = "UTF-8" if any(isinstance(c, str) and not c.isascii() for c in criteria) else None
    uids = sorted(client.search(criteria, charset=charset), reverse=True)  # newest first
    offset = max(0, offset)
    chosen = uids[offset : offset + clamp(count)]
    return {"total": len(uids), "offset": offset, "returned": len(chosen), "messages": summaries(client, chosen)}


# ---------------------------------------------------------------------------
# Opening one message
# ---------------------------------------------------------------------------


class OpenedMessage(Protocol):
    """A message's headers and leaf parts, with each part downloaded only when read."""

    headers: EmailMessage
    parts: list[mime.Part]

    def read(self, part: mime.Part, limit: int | None = None) -> bytes:
        """The part's raw (still transfer-encoded) bytes, optionally only the first `limit`."""
        ...


class StructuredMessage:
    """Fetches parts one at a time by section number, using the server's BODYSTRUCTURE."""

    def __init__(self, client, message_id: int, headers: EmailMessage, parts: list[mime.Part]):
        self._client, self._id = client, message_id
        self.headers, self.parts = headers, parts

    def read(self, part: mime.Part, limit: int | None = None) -> bytes:
        item = f"BODY.PEEK[{part.section}]" + (f"<0.{limit}>" if limit else "")
        got = self._client.fetch([self._id], [item]).get(self._id, {})
        return fetch_value(got, f"BODY[{part.section}]".encode()) or b""


class DownloadedMessage:
    """Fallback for a server whose BODYSTRUCTURE can't be understood: one full download."""

    def __init__(self, headers: EmailMessage, msg: EmailMessage):
        self.headers = headers
        self.parts, self._raw = mime.email_parts(msg)

    def read(self, part: mime.Part, limit: int | None = None) -> bytes:
        data = self._raw[part.section]
        return data[:limit] if limit else data


def fetch_full(client, message_id: int) -> EmailMessage:
    fetched = client.fetch([message_id], ["BODY.PEEK[]"])
    raw = fetch_value(fetched.get(message_id, {}), b"BODY[]")
    if not raw:
        raise ToolError(NOT_FOUND.format(id=message_id))
    return mime.parse(raw)


def open_message(client, message_id: int) -> OpenedMessage:
    """Open a message in the selected mailbox without downloading its parts yet."""
    fetched = client.fetch([message_id], ["BODY.PEEK[HEADER]", "BODYSTRUCTURE", "RFC822.SIZE"])
    data = fetched.get(message_id)
    if not data:
        raise ToolError(NOT_FOUND.format(id=message_id))
    headers = mime.parse(fetch_value(data, b"BODY[HEADER]") or b"")
    try:
        return StructuredMessage(client, message_id, headers, mime.structure_parts(data[b"BODYSTRUCTURE"]))
    except Exception as e:  # unexpected structure from this server
        log.warning("Could not read BODYSTRUCTURE for message %s (%s); downloading it in full", message_id, e)
        size = data.get(b"RFC822.SIZE", 0)
        if size > FULL_DOWNLOAD_LIMIT:
            raise ToolError(f"Message is {size // (1024 * 1024)} MB and its structure could not be read.") from e
        return DownloadedMessage(headers, fetch_full(client, message_id))


def read_body(message: OpenedMessage, max_chars: int) -> tuple[str, bool]:
    """Body text (plain, else converted HTML) and whether it was cut short."""
    part = mime.pick_body(message.parts)
    if part is None:
        return "", False
    want = max(BODY_FETCH_BYTES, max_chars * 8)
    if part.size <= want:
        return mime.part_text(part, message.read(part)), False
    return mime.part_text(part, message.read(part, want)), True


def attachments(message: OpenedMessage) -> list[mime.Part]:
    return [p for p in message.parts if p.is_attachment]
