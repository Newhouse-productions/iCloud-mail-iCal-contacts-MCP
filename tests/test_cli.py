"""The setup wizard and connection check, with prompts, keychain and network faked."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import configure
from mcp.server.mcpserver.exceptions import ToolError

from icloud_mail_mcp import cli, config, mail


@pytest.fixture
def blank(settings):
    """No account configured yet."""
    return configure(email="", imap_user="", apple_id="")


def _fake_run(ran):
    def run(cmd, env, check):
        ran.append((cmd, env))
        return type("R", (), {"returncode": 0})

    return run


def test_setup_writes_settings_and_keychain(blank, monkeypatch, capsys):
    answers = iter(["jane.appleseed@icloud.com", "", ""])  # accept the suggested defaults
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "abcd-efgh-ijkl-mnop")
    stored = {}

    import keyring

    monkeypatch.setattr(keyring, "set_password", lambda service, user, pw: stored.update({(service, user): pw}))
    monkeypatch.setenv("ICLOUD_IMAP_USER", "stale-old-login")  # what load_dotenv put in os.environ before
    ran = []
    monkeypatch.setattr(cli.subprocess, "run", _fake_run(ran))
    with pytest.raises(SystemExit) as done:
        cli.setup()
    assert done.value.code == 0

    text = blank.config_file.read_text()
    assert "ICLOUD_EMAIL=jane.appleseed@icloud.com" in text
    assert "ICLOUD_IMAP_USER=jane.appleseed" in text  # the short form iCloud IMAP expects
    assert "ICLOUD_APPLE_ID=jane.appleseed@icloud.com" in text
    # Filed under the email address, which doesn't change if the mail login is edited later.
    assert stored == {("icloud-mail-mcp", "jane.appleseed@icloud.com"): "abcd-efgh-ijkl-mnop"}
    assert "abcd" not in text  # the password never goes in the file
    if sys.platform != "win32":
        assert blank.config_file.stat().st_mode & 0o777 == 0o600
    cmd, env = ran[0]
    assert cmd[-2:] == ["icloud_mail_mcp", "--check"]
    assert env["ICLOUD_IMAP_USER"] == "jane.appleseed"  # the check tests the new settings
    out = capsys.readouterr().out
    assert "claude mcp add" in out and '"mcpServers"' in out


def test_setup_keeps_other_settings(blank, monkeypatch):
    blank.config_file.parent.mkdir(parents=True)
    blank.config_file.write_text("ICLOUD_ALLOW_SEND=true\nICLOUD_EMAIL=old@icloud.com\n")
    answers = iter(["new@example.com", "", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: "")
    monkeypatch.setattr(cli.subprocess, "run", _fake_run([]))
    with pytest.raises(SystemExit):
        cli.setup()
    text = blank.config_file.read_text()
    assert "ICLOUD_ALLOW_SEND=true" in text and "ICLOUD_EMAIL=new@example.com" in text
    assert "old@icloud.com" not in text
    assert "ICLOUD_IMAP_USER=new@example.com" in text  # custom domains log in with the full address


def test_check_reports_each_service(monkeypatch, capsys):
    from icloud_mail_mcp import calendars, contacts

    monkeypatch.setattr(mail, "list_folders", lambda: [{"name": "INBOX", "flags": []}])
    monkeypatch.setattr(mail, "unread_summary", lambda: [{"folder": "INBOX", "unread": 3, "total": 9}])
    monkeypatch.setattr(calendars, "list_calendars", lambda: [{"name": "Home", "writable": True}])

    def no_contacts():
        raise ToolError("iCloud Contacts login failed.")

    monkeypatch.setattr(contacts, "_client", no_contacts)
    with pytest.raises(SystemExit) as done:
        cli.check()
    assert done.value.code == 1
    out = capsys.readouterr().out
    assert "OK    Mail" in out and "3 unread" in out
    assert "OK    Calendar: 1 calendars (Home)" in out
    assert "FAIL  Contacts: iCloud Contacts login failed." in out


def test_launch_command_installed_and_checkout(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda name: sys.executable)
    assert cli._launch_command() == [str(Path(sys.executable).resolve())]
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    command, script = cli._launch_command()
    assert script.endswith("server.py") and Path(script).exists()


def test_mail_works_without_calendar_libraries():
    """An older install without icalendar still gets the mail tools."""
    project = Path(config.__file__).resolve().parent.parent
    code = f"""
import sys
sys.path.insert(0, {str(project)!r})
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in ("icalendar", "recurring_ical_events"):
            raise ImportError("No module named " + name)
sys.meta_path.insert(0, Block())
import asyncio
from icloud_mail_mcp import app
names = {{t.name for t in asyncio.run(app.load_tools().list_tools())}}
print("open_email" in names, "list_events" in names, app.calendar_available)
"""
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["True", "False", "False"]
    assert "Calendar and Contacts tools are unavailable" in out.stderr


def test_read_only_mode_offers_only_reading_tools():
    project = Path(config.__file__).resolve().parent.parent
    code = (
        f"import sys, asyncio; sys.path.insert(0, {str(project)!r}); "
        "from icloud_mail_mcp import app; "
        "print(sorted(t.name for t in asyncio.run(app.load_tools().list_tools())))"
    )
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith("ICLOUD_")}
    env["ICLOUD_READ_ONLY"] = "true"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert ast.literal_eval(out.stdout) == [
        "find_emails",
        "get_contact",
        "list_calendars",
        "list_events",
        "list_folders",
        "open_email",
        "recent_emails",
        "search_contacts",
        "unread_summary",
    ]
