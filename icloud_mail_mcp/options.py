"""Every setting the server reads, with how to check a new value before it's saved.

Used by `icloud-mail-mcp --settings / --set / --unset`, so a typo is caught when it's
typed rather than when Claude next starts the server.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.mcpserver.exceptions import ToolError

PREFIX = "ICLOUD_"
TRUE_WORDS = ("1", "true", "yes", "on")
FALSE_WORDS = ("0", "false", "no", "off")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class InvalidValue(ValueError):
    pass


def _text(value: str) -> str:
    return value


def _email(value: str) -> str:
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        raise InvalidValue("must be an email address")
    return value


def _flag(value: str) -> str:
    word = value.lower()
    if word in TRUE_WORDS:
        return "true"
    if word in FALSE_WORDS:
        return "false"
    raise InvalidValue("must be true or false")


def _whole_number(value: str) -> str:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if number < 1:
        raise InvalidValue("must be a whole number of 1 or more")
    return str(number)


def _address_list(value: str) -> str:
    return ",".join(part.strip() for part in value.split(",") if part.strip())


def _time_zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise InvalidValue("must be a time zone name such as Europe/London or America/New_York") from e
    return value


def _url(value: str) -> str:
    from . import dav

    try:
        dav.require_https(value, "it")
    except ToolError as e:
        raise InvalidValue("must be an https:// URL") from e
    return value


def _folder(value: str) -> str:
    if Path(value).expanduser().exists() and not Path(value).expanduser().is_dir():
        raise InvalidValue("is a file, not a folder")
    return value


def _log_level(value: str) -> str:
    if value.upper() not in LOG_LEVELS:
        raise InvalidValue(f"must be one of {', '.join(LOG_LEVELS)}")
    return value.upper()


@dataclass(frozen=True)
class Option:
    name: str
    check: Callable[[str], str]
    default: str
    about: str


OPTIONS = {
    o.name: o
    for o in (
        Option("ICLOUD_EMAIL", _email, "", "Your iCloud address"),
        Option("ICLOUD_IMAP_USER", _text, "ICLOUD_EMAIL", "Mail login"),
        Option("ICLOUD_APPLE_ID", _email, "ICLOUD_EMAIL", "Apple ID for Calendar and Contacts"),
        Option("ICLOUD_ALIASES", _address_list, "", "Your other addresses, comma-separated"),
        Option("ICLOUD_READ_ONLY", _flag, "false", "Offer Claude only the tools that read"),
        Option("ICLOUD_ALLOW_SEND", _flag, "false", "Let Claude send email (otherwise drafts only)"),
        Option("ICLOUD_SEND_ALLOWLIST", _address_list, "", "Only send to these addresses or @domains"),
        Option("ICLOUD_SAVE_SENT", _flag, "true", "Save a copy of sent mail"),
        Option("ICLOUD_DEFAULT_CALENDAR", _text, "", "Calendar for new events"),
        Option("ICLOUD_TIMEZONE", _time_zone, "your computer's", "Time zone for event times"),
        Option("ICLOUD_CONNECT_TIMEOUT", _whole_number, "10", "Seconds to wait to reach iCloud"),
        Option("ICLOUD_TIMEOUT", _whole_number, "30", "Seconds to wait for each response"),
        Option("ICLOUD_MAX_BODY_CHARS", _whole_number, "8000", "Longest email body shown to Claude"),
        Option("ICLOUD_MAX_ATTACHMENT_MB", _whole_number, "25", "Largest attachment saved, in MB"),
        Option("ICLOUD_ALLOW_EXECUTABLES", _flag, "false", "Let get_attachment save programs and scripts"),
        Option("ICLOUD_ATTACHMENT_DIR", _folder, "~/Downloads/icloud-mail", "Where attachments are saved"),
        Option("ICLOUD_CALDAV_URL", _url, "https://caldav.icloud.com/", "Calendar server"),
        Option("ICLOUD_CARDDAV_URL", _url, "https://contacts.icloud.com/", "Contacts server"),
        Option("ICLOUD_LOG_LEVEL", _log_level, "INFO", "How much to log"),
    )
}

# Kept in the keychain by --setup / --store-password, never typed on a command line.
SECRET = "ICLOUD_APP_PASSWORD"


def full_name(name: str) -> str:
    """Accept `allow_send`, `ALLOW_SEND` or `ICLOUD_ALLOW_SEND` for the same setting."""
    name = name.strip().upper().replace("-", "_")
    return name if name.startswith(PREFIX) else PREFIX + name


def lookup(name: str) -> Option:
    key = full_name(name)
    if key == SECRET:
        raise InvalidValue(
            "the password isn't set this way, because command lines are saved in your shell history. "
            "Run `icloud-mail-mcp --store-password` instead"
        )
    if key not in OPTIONS:
        raise InvalidValue(f"{key} is not a setting. Run `icloud-mail-mcp --settings` to see them all")
    return OPTIONS[key]


def parse_assignment(text: str) -> tuple[str, str]:
    """`NAME=value` -> (full name, checked value). An empty value is allowed: it means the default."""
    name, sep, value = text.partition("=")
    if not sep or not name.strip():
        raise InvalidValue(f"{text!r} should look like NAME=value")
    option = lookup(name)
    value = value.strip()
    if "\n" in value or "\r" in value:
        raise InvalidValue(f"{option.name} must be on one line")
    if " #" in value:
        raise InvalidValue(f"{option.name} can't contain ' #' (the settings file reads it as a comment)")
    if not value:
        return option.name, ""
    try:
        return option.name, option.check(value)
    except InvalidValue as e:
        raise InvalidValue(f"{option.name} {e} (got {value!r})") from e
