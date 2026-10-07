"""Command line: run the MCP server, or set it up and test it."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

from . import app, config, options
from .config import APPLE_DOMAINS


def _ask(prompt: str, default: str = "") -> str:
    shown = f" [{default}]" if default else ""
    answer = input(f"{prompt}{shown}: ").strip()
    return answer or default


def _save_settings(values: dict[str, str]) -> Path:
    """Write settings to the per-user file, keeping any other settings already there."""
    from dotenv import set_key

    path = config.current().config_file
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# icloud-mail-mcp settings. See the README for every option.\n")
    if sys.platform != "win32":
        path.chmod(0o600)
    for key, value in values.items():
        set_key(str(path), key, value, quote_mode="never")
    return path


def _file_values(path: Path) -> dict[str, str]:
    from dotenv import dotenv_values

    if not path.exists():
        return {}
    return {k: (v or "") for k, v in dotenv_values(path).items()}


def show_settings():
    """Every setting: its value and where it comes from."""
    path = config.current().config_file
    saved = _file_values(path)
    print(f"Settings file: {path}{'' if path.exists() else ' (not created yet)'}\n")
    width = max(len(name) for name in options.OPTIONS)
    for name, option in options.OPTIONS.items():
        if name in config.ENVIRONMENT_KEYS:
            shown = f"{os.environ.get(name, '')}   (from the environment; overrides the file)"
        elif saved.get(name):
            shown = saved[name]
        else:
            shown = f"-   (default: {option.default})" if option.default else "-"
        print(f"  {name:<{width}}  {shown}")
        print(f"  {'':<{width}}  {option.about}")
    password_in_file = bool(saved.get(options.SECRET))
    print(f"\n  {options.SECRET}: {'in the settings file' if password_in_file else 'kept in the system keychain'}")
    print("\nChange one with: icloud-mail-mcp --set NAME=value   (e.g. --set ALLOW_SEND=true)")


def set_settings(assignments: list[str]):
    """Check every NAME=value first, then save them all, so a typo changes nothing."""
    try:
        changes = dict(options.parse_assignment(a) for a in assignments)
    except options.InvalidValue as e:
        sys.exit(f"Not saved: {e}.")
    to_save = {k: v for k, v in changes.items() if v}
    path = _save_settings(to_save) if to_save else config.current().config_file
    _remove_settings([k for k, v in changes.items() if not v])
    for name, value in changes.items():
        print(f"  {name}={value}" if value else f"  {name} removed (back to the default)")
    _after_change(path, changes)


def unset_settings(names: list[str]):
    try:
        # The password may be removed from the file (once it's in the keychain), just not set here.
        keys = [options.SECRET if options.full_name(n) == options.SECRET else options.lookup(n).name for n in names]
    except options.InvalidValue as e:
        sys.exit(f"Not changed: {e}.")
    removed = _remove_settings(keys)
    for key in keys:
        print(f"  {key} removed (back to the default)" if key in removed else f"  {key} was not in the file")
    _after_change(config.current().config_file, dict.fromkeys(keys, ""))


def _remove_settings(keys: list[str]) -> list[str]:
    from dotenv import unset_key

    path = config.current().config_file
    present = [k for k in keys if k in _file_values(path)]
    for key in present:
        unset_key(str(path), key, quote_mode="never")
    return present


def _after_change(path: Path, changes: dict[str, str]):
    print(f"Saved to {path}")
    for name in changes:
        if name in config.ENVIRONMENT_KEYS:
            print(f"Note: {name} is also set in your environment, which wins over the settings file. Remove it there.")
    print("Restart Claude (fully quit and reopen) to use the new settings.")


def _launch_command() -> list[str]:
    """How Claude should start the server, as absolute paths (Claude Desktop has no PATH)."""
    found = shutil.which("icloud-mail-mcp")
    if found:
        return [str(Path(found).resolve())]
    # Not installed: running from a checkout via `python server.py`.
    return [str(Path(sys.executable).resolve()), str(config.PROJECT_DIR / "server.py")]


def _print_claude_config():
    command, *args = _launch_command()
    print("\nConnect it to Claude:")
    print("\n  Claude Code:")
    print("    claude mcp add -s user --transport stdio icloud-mail -- " + " ".join(f'"{c}"' for c in [command, *args]))
    print("\n  Claude Desktop: add this to claude_desktop_config.json, then fully quit and reopen Claude:")
    entry = {"command": command, **({"args": args} if args else {})}
    snippet = json.dumps({"mcpServers": {"icloud-mail": entry}}, indent=2)
    print("    " + snippet.replace("\n", "\n    "))
    if sys.platform == "win32":
        print("    (file: %APPDATA%\\Claude\\claude_desktop_config.json)")
    else:
        print("    (file: ~/Library/Application Support/Claude/claude_desktop_config.json)")


def setup():
    settings = config.current()
    print("icloud-mail-mcp setup")
    print(f"Settings file: {settings.config_file}\n")
    email = _ask("Your iCloud email address", settings.email)
    if "@" not in email:
        sys.exit("That doesn't look like an email address.")
    local, _, domain = email.lower().partition("@")
    same = settings.email == email
    imap_default = settings.imap_user if same else (local if domain in APPLE_DOMAINS else email)
    imap_user = _ask("Mail login (usually the part before @icloud.com)", imap_default)
    apple_id = _ask("Apple ID email, for Calendar and Contacts", settings.apple_id if same else email)

    values = {"ICLOUD_EMAIL": email, "ICLOUD_IMAP_USER": imap_user, "ICLOUD_APPLE_ID": apple_id}
    path = _save_settings(values)
    print(f"Saved settings to {path}")

    print("\nCreate an app-specific password at https://appleid.apple.com (Sign-In and Security >")
    print("App-Specific Passwords) and paste it here. It is stored in your system keychain.")
    password = getpass.getpass("App-specific password (hidden): ").strip()
    if password:
        try:
            import keyring

            keyring.set_password(config.KEYRING_SERVICE, email, password)
            print("Saved the password to the system keychain.")
        except Exception as e:
            print(f"Could not use the system keychain ({e}).")
            print(f"Add ICLOUD_APP_PASSWORD=... to {path} instead.")
    else:
        print("No password entered; keeping the existing one, if any.")

    print("\nTesting the connection...")
    # A fresh process with the new values: this one's environment still holds the old ones,
    # and settings files never override the environment.
    env = {**os.environ, **values}
    package_parent = str(Path(config.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (package_parent, os.environ.get("PYTHONPATH")) if p)
    result = subprocess.run([sys.executable, "-m", "icloud_mail_mcp", "--check"], env=env, check=False)
    _print_claude_config()
    sys.exit(result.returncode)


def store_password():
    import keyring

    settings = config.current()
    if not settings.email:
        sys.exit("Run `icloud-mail-mcp --setup` first (ICLOUD_EMAIL is not set).")
    pw = getpass.getpass(f"App-specific password for {settings.email} (hidden): ").strip()
    if not pw:
        sys.exit("No password entered.")
    keyring.set_password(config.KEYRING_SERVICE, settings.email, pw)
    print("Saved to the system keychain. You can remove ICLOUD_APP_PASSWORD from your settings file.")


def check():
    """Test mail, calendar and contacts separately, so one failure doesn't hide the others."""
    settings = config.current()
    app.load_tools()
    from . import mail as mail_tools

    print(f"Settings: {settings.config_file if settings.config_file.exists() else 'environment / project .env'}")
    failures = 0

    def run(label, fn):
        nonlocal failures
        try:
            print(f"  OK    {label}: {fn()}")
        except ToolError as e:
            failures += 1
            print(f"  FAIL  {label}: {e}")

    def mail():
        folders = mail_tools.list_folders()
        unread = sum(u["unread"] for u in mail_tools.unread_summary())
        return f"signed in as {settings.imap_user}, {len(folders)} mailboxes, {unread} unread"

    def calendar():
        if not app.calendar_available:
            raise ToolError("not installed (reinstall to add Calendar and Contacts).")
        from . import calendars as cal

        calendars = cal.list_calendars()
        return f"{len(calendars)} calendars ({', '.join(c['name'] for c in calendars)})"

    def contacts():
        if not app.calendar_available:
            raise ToolError("not installed (reinstall to add Calendar and Contacts).")
        from . import contacts as con

        found = con._all_contacts(con._client(), refresh=True)
        return f"{len(found)} contacts"

    run("Mail", mail)
    run("Calendar", calendar)
    run("Contacts", contacts)
    print(f"Sending email is {'ENABLED' if settings.allow_send else 'disabled (drafts only)'}.")
    if failures:
        sys.exit(1)


def main():
    # Settings first: ICLOUD_LOG_LEVEL may be set in the settings file.
    app.configure_logging(config.current().log_level)
    parser = argparse.ArgumentParser(
        prog="icloud-mail-mcp",
        description="iCloud Mail, Calendar and Contacts for Claude (MCP server).",
        epilog="With no options, runs the MCP server on stdio (what Claude starts).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--setup", action="store_true", help="guided setup: settings, password, connection test")
    group.add_argument("--store-password", action="store_true", help="save the app-specific password to the keychain")
    group.add_argument("--check", action="store_true", help="test mail, calendar and contacts access")
    group.add_argument("--config-path", action="store_true", help="print where settings are read from")
    group.add_argument("--settings", action="store_true", help="show every setting and its current value")
    group.add_argument("--set", nargs="+", metavar="NAME=VALUE", help="change settings, e.g. --set ALLOW_SEND=true")
    group.add_argument("--unset", nargs="+", metavar="NAME", help="remove settings, so their defaults apply")
    args = parser.parse_args()
    if args.setup:
        setup()
    elif args.store_password:
        store_password()
    elif args.check:
        check()
    elif args.config_path:
        print(config.current().config_file)
    elif args.settings:
        show_settings()
    elif args.set:
        set_settings(args.set)
    elif args.unset:
        unset_settings(args.unset)
    else:
        if os.isatty(0):
            print(
                "Starting the MCP server on stdio (Claude does this for you). "
                "Run with --setup to configure, or --help. Ctrl+C to stop.",
                file=sys.stderr,
            )
        app.load_tools().run()
