"""ICLOUD_LOG_LEVEL=DEBUG must produce debug logs, from the environment or the settings file."""

import os
import subprocess
import sys
from pathlib import Path

from icloud_mail_mcp import config

PROJECT = Path(config.__file__).resolve().parent.parent


def _run(env_extra: dict) -> str:
    code = (
        f"import sys; sys.argv = ['icloud-mail-mcp', '--config-path']; sys.path.insert(0, {str(PROJECT)!r}); "
        "import logging; from icloud_mail_mcp import cli; "
        "cli.main(); logging.getLogger('icloud-mail').debug('probe-debug-line')"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("ICLOUD_")}
    env.update(env_extra)
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True).stderr


def test_debug_level_from_environment(tmp_path):
    assert "probe-debug-line" in _run({"ICLOUD_LOG_LEVEL": "DEBUG", "ICLOUD_MAIL_MCP_HOME": str(tmp_path)})


def test_debug_level_from_settings_file(tmp_path):
    (tmp_path / ".env").write_text("ICLOUD_LOG_LEVEL=DEBUG\n")
    assert "probe-debug-line" in _run({"ICLOUD_MAIL_MCP_HOME": str(tmp_path)})


def test_info_by_default(tmp_path):
    assert "probe-debug-line" not in _run({"ICLOUD_MAIL_MCP_HOME": str(tmp_path)})
