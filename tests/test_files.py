"""Saving attachments: never overwrite or follow links, private, marked, no programs."""

import os
import stat
import sys

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from icloud_mail_mcp import files


def test_never_overwrites(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"original")
    saved = files.save_download(tmp_path, "a.pdf", b"new")
    assert saved.name == "a (1).pdf" and (tmp_path / "a.pdf").read_bytes() == b"original"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin rights on Windows")
def test_never_follows_a_planted_link(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.txt").symlink_to(victim)
    saved = files.save_download(out, "a.txt", b"attacker data")
    assert saved.name == "a (1).txt" and victim.read_text() == "keep me"


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin rights on Windows")
def test_refuses_a_symlinked_folder(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    with pytest.raises(ToolError, match="symbolic link"):
        files.save_download(tmp_path / "link", "a.pdf", b"x")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_saved_files_are_private(tmp_path):
    saved = files.save_download(tmp_path, "a.pdf", b"x")
    assert stat.S_IMODE(os.stat(saved).st_mode) == 0o600


@pytest.mark.parametrize(
    "name",
    [
        "Setup.exe",
        "invoice.pdf.exe",
        "run.command",
        "Tool.app",
        "x.JS",
        "evil.ps1.",
        "help.chm",
        "disk.vhdx",
        "app.appref-ms",
        "launch.desktop",
        "link.webloc",
        "profile.mobileconfig",
    ],
)
def test_programs_are_refused_unless_allowed(tmp_path, name):
    with pytest.raises(ToolError, match="program or script"):
        files.save_download(tmp_path, name, b"x")
    assert files.save_download(tmp_path, name.rstrip("."), b"x", allow_executables=True).exists()


def test_documents_are_fine(tmp_path):
    for name in ("report.pdf", "photo.HEIC", "data.xlsx", "notes"):
        assert files.save_download(tmp_path, name, b"x").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Mark of the Web")
def test_windows_mark_of_the_web(tmp_path):
    saved = files.save_download(tmp_path, "a.pdf", b"x")
    with open(f"{saved}:Zone.Identifier", encoding="ascii") as stream:
        assert "ZoneId=3" in stream.read()


def test_macos_quarantine_flag(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(files.sys, "platform", "darwin")
    monkeypatch.setattr(files.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    saved = files.save_download(tmp_path, "a.pdf", b"x")
    assert calls[0][:3] == ["xattr", "-w", "com.apple.quarantine"] and calls[0][-1] == str(saved)
    assert calls[0][3].startswith("0083;")


def test_marking_failure_does_not_fail_the_save(tmp_path, monkeypatch):
    monkeypatch.setattr(files.sys, "platform", "darwin")

    def no_xattr(*args, **kwargs):
        raise FileNotFoundError("xattr")

    monkeypatch.setattr(files.subprocess, "run", no_xattr)
    assert files.save_download(tmp_path, "a.pdf", b"x").read_bytes() == b"x"
