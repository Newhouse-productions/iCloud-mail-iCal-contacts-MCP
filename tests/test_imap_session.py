"""The IMAP connection pool: reuse, checks after idle, replacement when broken, limits."""

import threading
import time

import pytest
from fakes import FakeIMAP, make_message
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError, LoginError
from mcp.server.mcpserver.exceptions import ToolError

from icloud_mail_mcp import imap, mail


def logins(fake):
    return sum(1 for c in fake.calls if c[0] == "login")


def age_idle_connections(seconds):
    for conn in imap.POOL._idle:
        conn.last_used -= seconds


def test_one_login_for_many_calls(fake):
    mail.list_folders()
    mail.recent_emails()
    mail.open_email(2)
    assert logins(fake) == 1


def test_idle_connection_is_checked_then_reused(fake):
    mail.list_folders()
    age_idle_connections(imap.Pool.IDLE_CHECK_SECONDS + 1)
    mail.list_folders()
    assert ("noop",) in fake.calls and logins(fake) == 1


def test_idle_check_uses_a_short_timeout(fake, monkeypatch):
    mail.list_folders()
    age_idle_connections(imap.Pool.IDLE_CHECK_SECONDS + 1)
    seen = []
    monkeypatch.setattr(fake, "noop", lambda: seen.append(fake.socket().gettimeout()))
    mail.list_folders()
    assert seen == [imap.Pool.CHECK_TIMEOUT_SECONDS] and fake.socket().gettimeout() == 30.0


def test_stale_connection_reconnects(fake, monkeypatch):
    mail.list_folders()
    age_idle_connections(imap.Pool.IDLE_CHECK_SECONDS + 1)

    def dead():
        raise IMAPClientAbortError("socket error: EOF")

    monkeypatch.setattr(fake, "noop", dead)
    mail.list_folders()
    assert logins(fake) == 2


def test_broken_connection_is_replaced(fake, monkeypatch):
    mail.list_folders()
    healthy = fake.list_folders
    failing = [True]

    def flaky():
        if failing[0]:
            raise IMAPClientAbortError("connection reset")
        return healthy()

    monkeypatch.setattr(fake, "list_folders", flaky)
    with pytest.raises(ToolError, match="Lost the connection"):
        mail.list_folders()
    failing[0] = False  # the server is healthy again
    mail.list_folders()
    assert logins(fake) == 2


def test_an_ordinary_error_keeps_the_connection(fake, monkeypatch):
    def no_such_folder(name, readonly=False):
        raise IMAPClientError("select failed: Mailbox doesn't exist: Archve")

    mail.list_folders()
    original = fake.select_folder
    monkeypatch.setattr(fake, "select_folder", no_such_folder)
    with pytest.raises(ToolError, match="iCloud IMAP error"):
        mail.recent_emails(folder="Archve")
    monkeypatch.setattr(fake, "select_folder", original)
    mail.recent_emails()
    assert logins(fake) == 1 and ("logout",) not in fake.calls


def test_unselect_only_before_status(fake):
    fake.capabilities.add("UNSELECT")
    mail.recent_emails()
    mail.open_email(2)
    assert ("unselect",) not in fake.calls  # no extra round trip on ordinary calls
    mail.unread_summary()
    assert ("unselect",) in fake.calls and fake.selected is None


def test_failed_login_logs_out_and_is_friendly(fake, monkeypatch):
    def bad_login(user, password):
        raise LoginError("AUTHENTICATIONFAILED")

    monkeypatch.setattr(fake, "login", bad_login)
    with pytest.raises(ToolError, match="login failed"):
        mail.list_folders()
    assert ("logout",) in fake.calls
    assert imap.POOL._open == 0  # the reserved slot was released


def test_concurrent_calls_use_at_most_max_connections(monkeypatch):
    servers = []

    def new_server(settings):
        server = FakeIMAP({1: make_message(1, "Hi")})
        slow = server.search
        server.search = lambda *a, **k: (time.sleep(0.05), slow(*a, **k))[1]
        servers.append(server)
        return server

    monkeypatch.setattr(imap, "new_client", new_server)
    errors = []

    def work():
        try:
            mail.recent_emails()
        except Exception as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=work) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert 1 <= len(servers) <= imap.Pool.MAX_CONNECTIONS


def test_close_logs_out_idle_connections(fake):
    mail.list_folders()
    imap.POOL.close()
    assert ("logout",) in fake.calls and imap.POOL._open == 0
