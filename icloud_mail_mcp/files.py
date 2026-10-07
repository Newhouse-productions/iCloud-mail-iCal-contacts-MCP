"""Saving attachments to disk safely.

Attachments are untrusted files from other people. They are written without ever
overwriting or following a link to something else, readable only by the user, and
marked as downloaded from the internet so macOS Gatekeeper and Windows SmartScreen
check them when they're opened. Programs and scripts are refused unless allowed.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

log = logging.getLogger("icloud-mail")

# Files that run code when opened (double-clicked), on macOS, Windows or a Unix shell.
EXECUTABLE_EXTENSIONS = frozenset(
    {
        # macOS
        ".app",
        ".command",
        ".pkg",
        ".mpkg",
        ".dmg",
        ".scpt",
        ".applescript",
        ".workflow",
        ".terminal",
        ".tool",
        # Windows
        ".exe",
        ".msi",
        ".msix",
        ".appx",
        ".bat",
        ".cmd",
        ".com",
        ".scr",
        ".pif",
        ".cpl",
        ".msc",
        ".hta",
        ".ps1",
        ".psm1",
        ".vbs",
        ".vbe",
        ".js",
        ".jse",
        ".wsf",
        ".wsh",
        ".lnk",
        ".url",
        ".reg",
        ".iso",
        ".img",
        # Anywhere
        ".sh",
        ".bash",
        ".zsh",
        ".py",
        ".pl",
        ".rb",
        ".jar",
        # More that run code, install, or mount when opened
        ".chm",
        ".msp",
        ".mst",
        ".vhd",
        ".vhdx",
        ".appref-ms",
        ".application",
        ".xbap",
        ".gadget",
        ".settingcontent-ms",
        ".library-ms",
        ".search-ms",
        ".diagcab",
        ".xll",
        ".wsc",
        ".sct",
        ".ws",
        ".scf",
        ".inf",
        ".jnlp",
        ".desktop",
        ".fileloc",
        ".webloc",
        ".inetloc",
        ".mobileconfig",
    }
)
MAX_NAME_ATTEMPTS = 1000


def is_executable_name(name: str) -> bool:
    return Path(name.strip().rstrip(".")).suffix.lower() in EXECUTABLE_EXTENSIONS


def save_download(folder: Path, name: str, data: bytes, allow_executables: bool = False) -> Path:
    """Write `data` to a new file in `folder` named `name` (or "name (1)" etc.) and return its path."""
    if not allow_executables and is_executable_name(name):
        raise ToolError(
            f"{name!r} is a program or script, so it wasn't saved. Open it from Mail if you trust it, "
            "or set ICLOUD_ALLOW_EXECUTABLES=true."
        )
    folder.mkdir(parents=True, exist_ok=True)
    if folder.is_symlink():
        raise ToolError(f"{folder} is a symbolic link; refusing to save attachments through it.")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    stem, suffix = Path(name).stem, Path(name).suffix
    for n in range(MAX_NAME_ATTEMPTS):
        target = folder / (name if n == 0 else f"{stem} ({n}){suffix}")
        try:
            # O_EXCL: created new or not at all, so an existing file (or a link planted
            # where the file would go) is never overwritten or followed.
            fd = os.open(target, flags, 0o600)
        except FileExistsError:
            continue
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        mark_downloaded(target)
        return target
    raise ToolError(f"Too many files named like {name!r} in {folder}.")


def mark_downloaded(path: Path) -> None:
    """Mark a file as downloaded from the internet (best effort; never fails the save)."""
    try:
        if sys.platform == "darwin":
            # Gatekeeper checks quarantined files when they're opened.
            value = f"0083;{int(time.time()):x};icloud-mail-mcp;"
            subprocess.run(
                ["xattr", "-w", "com.apple.quarantine", value, str(path)], check=True, capture_output=True, timeout=5
            )
        elif sys.platform == "win32":
            # The "Mark of the Web": SmartScreen and Office Protected View check it.
            with open(f"{path}:Zone.Identifier", "w", encoding="ascii") as stream:
                stream.write("[ZoneTransfer]\nZoneId=3\n")
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("Could not mark %s as downloaded from the internet: %s", path.name, e)
