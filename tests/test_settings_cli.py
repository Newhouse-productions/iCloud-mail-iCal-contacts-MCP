"""icloud-mail-mcp --settings / --set / --unset."""

import re
import sys
from pathlib import Path

import pytest

from icloud_mail_mcp import cli, config, options


def test_every_setting_the_server_reads_is_listed():
    source = Path(config.__file__).read_text()
    read = set(re.findall(r'"(ICLOUD_[A-Z_]+)"', source)) - {"ICLOUD_MAIL_MCP_HOME"}
    assert read == set(options.OPTIONS) | {options.SECRET}


def test_set_checks_and_saves(settings, capsys):
    cli.set_settings(["allow_send=yes", "ICLOUD_TIMEOUT=45", "default-calendar=Work"])
    text = settings.config_file.read_text()
    assert "ICLOUD_ALLOW_SEND=true" in text
    assert "ICLOUD_TIMEOUT=45" in text
    assert "ICLOUD_DEFAULT_CALENDAR=Work" in text
    if sys.platform != "win32":
        assert settings.config_file.stat().st_mode & 0o777 == 0o600
    assert "Restart Claude" in capsys.readouterr().out


def test_set_keeps_other_lines_and_replaces_in_place(settings):
    settings.config_file.parent.mkdir(parents=True)
    settings.config_file.write_text("# mine\nICLOUD_EMAIL=me@icloud.com\nICLOUD_TIMEOUT=30\n")
    cli.set_settings(["TIMEOUT=60"])
    assert settings.config_file.read_text() == "# mine\nICLOUD_EMAIL=me@icloud.com\nICLOUD_TIMEOUT=60\n"


@pytest.mark.parametrize(
    "assignment, message",
    [
        ("ALLOW_SEND=maybe", "true or false"),
        ("TIMEOUT=0", "whole number"),
        ("TIMEZONE=Mars/Olympus", "time zone"),
        ("CALDAV_URL=http://example.com/", "https://"),
        ("EMAIL=nobody", "email address"),
        ("LOG_LEVEL=loud", "DEBUG"),
        ("NOT_A_THING=1", "not a setting"),
        ("APP_PASSWORD=abcd-efgh", "--store-password"),
        ("ALLOW_SEND", "NAME=value"),
        ("DEFAULT_CALENDAR=Work #2", "comment"),
    ],
)
def test_set_refuses_bad_values_and_saves_nothing(settings, assignment, message):
    with pytest.raises(SystemExit) as stop:
        cli.set_settings(["READ_ONLY=true", assignment])
    assert message in str(stop.value.code)
    assert not settings.config_file.exists()  # the good one wasn't saved either


def test_empty_value_and_unset_remove_the_line(settings, capsys):
    settings.config_file.parent.mkdir(parents=True)
    settings.config_file.write_text("ICLOUD_READ_ONLY=true\nICLOUD_TIMEOUT=60\nICLOUD_APP_PASSWORD=abcd\n")
    cli.set_settings(["READ_ONLY="])
    cli.unset_settings(["timeout", "app_password", "save_sent"])
    assert settings.config_file.read_text() == ""
    assert "ICLOUD_SAVE_SENT was not in the file" in capsys.readouterr().out


def test_unset_refuses_unknown_names(settings):
    with pytest.raises(SystemExit) as stop:
        cli.unset_settings(["TIMEOUTS"])
    assert "not a setting" in str(stop.value.code)


def test_settings_lists_values_defaults_and_environment(settings, monkeypatch, capsys):
    settings.config_file.parent.mkdir(parents=True)
    settings.config_file.write_text("ICLOUD_TIMEOUT=60\nICLOUD_APP_PASSWORD=secret-pw\n")
    monkeypatch.setenv("ICLOUD_READ_ONLY", "true")
    monkeypatch.setattr(config, "ENVIRONMENT_KEYS", {"ICLOUD_READ_ONLY"})
    cli.show_settings()
    out = capsys.readouterr().out
    assert re.search(r"ICLOUD_TIMEOUT\s+60", out)
    assert re.search(r"ICLOUD_SAVE_SENT\s+-\s+\(default: true\)", out)
    assert "true   (from the environment; overrides the file)" in out
    assert "secret-pw" not in out
    assert "ICLOUD_APP_PASSWORD: in the settings file" in out


def test_set_warns_when_the_environment_wins(settings, monkeypatch, capsys):
    monkeypatch.setattr(config, "ENVIRONMENT_KEYS", {"ICLOUD_ALLOW_SEND"})
    cli.set_settings(["ALLOW_SEND=true"])
    assert "also set in your environment" in capsys.readouterr().out
