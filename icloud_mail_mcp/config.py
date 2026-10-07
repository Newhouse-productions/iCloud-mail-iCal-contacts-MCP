"""Settings: where they live, how they're loaded, and the app-specific password.

Settings are read once into an immutable `Settings` object. Code asks for the
current one with `current()`; tests install their own with `use()`.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import dotenv_values, load_dotenv
from mcp.server.mcpserver.exceptions import ToolError

log = logging.getLogger("icloud-mail")

KEYRING_SERVICE = "icloud-mail-mcp"
APPLE_DOMAINS = ("icloud.com", "me.com", "mac.com")


def config_dir(env: Mapping[str, str] = os.environ, platform: str = sys.platform) -> Path:
    """Per-user settings folder: %APPDATA%\\icloud-mail-mcp on Windows, ~/.config/icloud-mail-mcp elsewhere."""
    if env.get("ICLOUD_MAIL_MCP_HOME"):
        return Path(env["ICLOUD_MAIL_MCP_HOME"]).expanduser()
    if platform == "win32":
        return Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "icloud-mail-mcp"
    return Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "icloud-mail-mcp"


# ICLOUD_ settings that came from the real environment rather than a settings file.
# They win over the settings file, so --set warns about them.
ENVIRONMENT_KEYS: set[str] = set()

# The folder holding pyproject.toml, for running from a checkout (`python server.py`).
PROJECT_DIR = Path(__file__).resolve().parent.parent


def load_env_files() -> Path:
    """Load the per-user settings file, then a checkout's .env, into os.environ.

    Variables already in the environment win (load_dotenv never overrides), then the
    per-user file, then the checkout's .env. Returns the per-user file's path.
    """
    config_file = config_dir() / ".env"
    ENVIRONMENT_KEYS.update(k for k in os.environ if k.startswith("ICLOUD_"))
    for env_file in (config_file, PROJECT_DIR / ".env"):
        warn_if_exposed(env_file)
        load_dotenv(env_file)
    return config_file


def warn_if_exposed(path: Path) -> bool:
    """Warn if a settings file holding the password can be read by other users."""
    if sys.platform == "win32":  # %APPDATA% is private to the user by default
        return False
    try:
        mode = path.stat().st_mode
        # Parse it the way it will be loaded, so "export X=..." and "X = ..." count too.
        holds_password = bool((dotenv_values(path).get("ICLOUD_APP_PASSWORD") or "").strip())
    except (OSError, ValueError):
        return False
    if holds_password and mode & 0o077:
        log.warning(
            "%s holds ICLOUD_APP_PASSWORD and can be read by other users. Run `chmod 600 %s`, "
            "or better, run `icloud-mail-mcp --store-password` and remove the line.",
            path,
            path,
        )
        return True
    return False


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name, "").strip()
    return default if not value else value.lower() in ("1", "true", "yes", "on")


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(env.get(name, "").strip() or default)
    except ValueError:
        value = 0
    if value < 1:
        log.warning("%s must be a whole number of 1 or more; using %s", name, default)
        return default
    return value


def _list(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    return tuple(s.strip().lower() for s in env.get(name, "").split(",") if s.strip())


@dataclass(frozen=True)
class Settings:
    email: str = ""
    imap_user: str = ""
    apple_id: str = ""
    app_password: str = field(default="", repr=False)  # fallback when the keychain isn't used
    aliases: tuple[str, ...] = ()
    read_only: bool = False
    allow_send: bool = False
    send_allowlist: tuple[str, ...] = ()
    save_sent: bool = True
    connect_timeout: int = 10
    timeout: int = 30
    max_body_chars: int = 8000
    max_attachment_mb: int = 25
    allow_executables: bool = False
    attachment_dir: Path = Path("~/Downloads/icloud-mail").expanduser()
    caldav_url: str = "https://caldav.icloud.com/"
    carddav_url: str = "https://contacts.icloud.com/"
    timezone: str = ""
    default_calendar: str | None = None
    log_level: str = "INFO"
    config_file: Path = Path(".env")

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ, config_file: Path | None = None) -> Settings:
        email = env.get("ICLOUD_EMAIL", "").strip()
        return cls(
            email=email,
            imap_user=env.get("ICLOUD_IMAP_USER", "").strip() or email,
            apple_id=env.get("ICLOUD_APPLE_ID", "").strip() or email,
            app_password=env.get("ICLOUD_APP_PASSWORD", "").strip(),
            aliases=_list(env, "ICLOUD_ALIASES"),
            read_only=_bool(env, "ICLOUD_READ_ONLY", False),
            allow_send=_bool(env, "ICLOUD_ALLOW_SEND", False),
            send_allowlist=_list(env, "ICLOUD_SEND_ALLOWLIST"),
            save_sent=_bool(env, "ICLOUD_SAVE_SENT", True),
            connect_timeout=_int(env, "ICLOUD_CONNECT_TIMEOUT", 10),
            timeout=_int(env, "ICLOUD_TIMEOUT", 30),
            max_body_chars=_int(env, "ICLOUD_MAX_BODY_CHARS", 8000),
            max_attachment_mb=_int(env, "ICLOUD_MAX_ATTACHMENT_MB", 25),
            allow_executables=_bool(env, "ICLOUD_ALLOW_EXECUTABLES", False),
            attachment_dir=Path(env.get("ICLOUD_ATTACHMENT_DIR") or "~/Downloads/icloud-mail").expanduser(),
            caldav_url=env.get("ICLOUD_CALDAV_URL") or "https://caldav.icloud.com/",
            carddav_url=env.get("ICLOUD_CARDDAV_URL") or "https://contacts.icloud.com/",
            timezone=env.get("ICLOUD_TIMEZONE", "").strip(),
            default_calendar=env.get("ICLOUD_DEFAULT_CALENDAR", "").strip() or None,
            log_level=env.get("ICLOUD_LOG_LEVEL", "").strip().upper() or "INFO",
            config_file=config_file or config_dir(env) / ".env",
        )

    def with_(self, **changes) -> Settings:
        return replace(self, **changes)

    def my_addresses(self) -> set[str]:
        """The email, its @icloud.com/@me.com/@mac.com twins, and the aliases."""
        me = {self.email.lower(), *self.aliases}
        local, _, domain = self.email.lower().partition("@")
        if domain in APPLE_DOMAINS:
            me |= {f"{local}@{d}" for d in APPLE_DOMAINS}
        return me


_current: Settings | None = None


def current() -> Settings:
    """The active settings, loaded from the environment and settings files on first use."""
    global _current
    if _current is None:
        config_file = load_env_files()
        _current = Settings.from_env(os.environ, config_file)
    return _current


def use(settings: Settings | None) -> None:
    """Install settings (tests, setup), or None to reload from the environment next time."""
    global _current
    _current = settings


def ensure_writable() -> None:
    """Refuse a change when read-only mode is on. The tools that change things aren't
    even offered to Claude then; this guards direct calls and anything missed."""
    if current().read_only:
        raise ToolError("Read-only mode is on (ICLOUD_READ_ONLY=true): nothing can be changed or sent.")


def keyring_accounts(settings: Settings) -> list[str]:
    """Keychain account names to try, in order. The email is the stable key; the IMAP
    login is where versions before 0.3 stored it."""
    return [a for a in dict.fromkeys((settings.email, settings.imap_user)) if a]


def password(settings: Settings | None = None) -> str:
    """App-specific password: OS keychain first, then ICLOUD_APP_PASSWORD."""
    settings = settings or current()
    try:
        import keyring

        for account in keyring_accounts(settings):
            pw = keyring.get_password(KEYRING_SERVICE, account)
            if pw:
                return pw
    except Exception:  # no keyring backend available
        pass
    if settings.app_password:
        return settings.app_password
    raise ToolError(
        "No app-specific password found. Run `icloud-mail-mcp --setup` (or "
        "`--store-password`), or set ICLOUD_APP_PASSWORD in the settings file."
    )
