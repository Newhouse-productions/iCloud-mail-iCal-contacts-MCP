"""Shared fixtures. Every test gets its own settings and a clean connection and caches,
and never reads the developer's real settings file or keychain."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeIMAP, make_message  # noqa: E402

from icloud_mail_mcp import config, dav, imap  # noqa: E402
from icloud_mail_mcp.config import Settings  # noqa: E402


def _reset_shared_state() -> None:
    imap.POOL.close()
    dav.HTTP.close()
    dav.HOME_CACHE.invalidate()
    try:
        from icloud_mail_mcp import calendars, contacts

        calendars.CALENDARS_CACHE.invalidate()
        contacts.CONTACTS_CACHE.invalidate()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def settings(monkeypatch, tmp_path) -> Settings:
    for key in list(os.environ):
        if key.startswith("ICLOUD_"):
            monkeypatch.delenv(key)
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda service, account: None)
    test_settings = Settings(
        email="me@icloud.com",
        imap_user="me",
        apple_id="me@icloud.com",
        app_password="app-pw",
        attachment_dir=tmp_path / "att",
        config_file=tmp_path / "cfg" / ".env",
    )
    config.use(test_settings)
    _reset_shared_state()
    yield test_settings
    _reset_shared_state()
    config.use(None)


def configure(**changes) -> Settings:
    """Change some settings for the rest of the current test."""
    updated = config.current().with_(**changes)
    config.use(updated)
    return updated


@pytest.fixture
def fake(monkeypatch) -> FakeIMAP:
    server = FakeIMAP(
        {
            1: make_message(1, "Old"),
            2: make_message(2, "Invoice", attach=b"%PDF-1.4 fake"),
            3: make_message(
                3, "Newest", html="<style>.x{color:red}</style><p>Hi&nbsp;there</p><script>evil()</script>"
            ),
        }
    )
    server.flags[1].add(b"\\Seen")
    monkeypatch.setattr(imap, "new_client", lambda settings: server)
    return server
