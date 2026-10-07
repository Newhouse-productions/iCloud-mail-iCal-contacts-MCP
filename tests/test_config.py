"""Settings: parsing, file locations, precedence, and the keychain lookup."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from icloud_mail_mcp import config
from icloud_mail_mcp.config import Settings

PROJECT = Path(config.__file__).resolve().parent.parent


def test_from_env_defaults_and_derived_values():
    s = Settings.from_env({"ICLOUD_EMAIL": "me@icloud.com", "ICLOUD_SEND_ALLOWLIST": "@Corp.com, Bob@x.com"})
    assert (s.imap_user, s.apple_id) == ("me@icloud.com", "me@icloud.com")
    assert s.send_allowlist == ("@corp.com", "bob@x.com")
    assert s.allow_send is False and s.save_sent is True


@pytest.mark.parametrize("value", ["0", "-5", "oops"])
def test_bad_numbers_fall_back_to_defaults(value):
    assert Settings.from_env({"ICLOUD_CONNECT_TIMEOUT": value}).connect_timeout == 10


def test_my_addresses_include_apple_twins_and_aliases():
    s = Settings(email="me@icloud.com", aliases=("hi@mydomain.com",))
    assert s.my_addresses() == {"me@icloud.com", "me@me.com", "me@mac.com", "hi@mydomain.com"}


def test_default_config_folder_per_platform(tmp_path):
    windows = config.config_dir({"APPDATA": str(tmp_path / "Roaming")}, platform="win32")
    assert windows == tmp_path / "Roaming" / "icloud-mail-mcp"
    mac = config.config_dir({"XDG_CONFIG_HOME": str(tmp_path / "xdg")}, platform="darwin")
    assert mac == tmp_path / "xdg" / "icloud-mail-mcp"
    assert config.config_dir({"ICLOUD_MAIL_MCP_HOME": str(tmp_path / "x")}) == tmp_path / "x"


def _probe(env_extra: dict) -> list[str]:
    """Load settings in a fresh process, as the server does at startup."""
    code = (
        f"import sys; sys.path.insert(0, {str(PROJECT)!r}); from icloud_mail_mcp import config; "
        "s = config.current(); print(s.email, s.timeout, s.config_file)"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("ICLOUD_")}
    env.update(env_extra)
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=str(Path.home()), env=env, capture_output=True, text=True, check=True
    )
    return out.stdout.split()


def test_settings_file_in_user_config_folder(tmp_path):
    home = tmp_path / "cfg"
    home.mkdir()
    (home / ".env").write_text("ICLOUD_EMAIL=probe@icloud.com\nICLOUD_TIMEOUT=oops\n")
    assert _probe({"ICLOUD_MAIL_MCP_HOME": str(home)}) == ["probe@icloud.com", "30", str(home / ".env")]


def test_environment_overrides_settings_file(tmp_path):
    home = tmp_path / "cfg"
    home.mkdir()
    (home / ".env").write_text("ICLOUD_EMAIL=file@icloud.com\n")
    assert _probe({"ICLOUD_MAIL_MCP_HOME": str(home), "ICLOUD_EMAIL": "env@icloud.com"})[0] == "env@icloud.com"


def test_password_lookup_order(monkeypatch):
    import keyring

    stored = {("icloud-mail-mcp", "me"): "old-key-pw"}  # where versions before 0.3 kept it
    monkeypatch.setattr(keyring, "get_password", lambda service, account: stored.get((service, account)))
    s = Settings(email="me@icloud.com", imap_user="me", app_password="file-pw")
    assert config.password(s) == "old-key-pw"
    stored[("icloud-mail-mcp", "me@icloud.com")] = "new-key-pw"
    assert config.password(s) == "new-key-pw"
    stored.clear()
    assert config.password(s) == "file-pw"
    with pytest.raises(ToolError, match="No app-specific password"):
        config.password(s.with_(app_password=""))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_warns_when_password_file_is_readable_by_others(tmp_path, caplog):
    path = tmp_path / ".env"
    path.write_text("ICLOUD_EMAIL=me@icloud.com\nICLOUD_APP_PASSWORD=abcd-efgh\n")
    path.chmod(0o644)
    assert config.warn_if_exposed(path) is True
    assert "can be read by other users" in caplog.text
    path.chmod(0o600)
    assert config.warn_if_exposed(path) is False
    path.write_text("export ICLOUD_APP_PASSWORD = abcd-efgh\n")  # forms dotenv also accepts
    path.chmod(0o644)
    assert config.warn_if_exposed(path) is True
    path.write_text("ICLOUD_EMAIL=me@icloud.com\n")  # no password: nothing to protect
    path.chmod(0o644)
    assert config.warn_if_exposed(path) is False
