"""Network filesystem detection. Probes are mocked so Linux and macOS agree."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from finance_context.api.app import create_app
from finance_context.settings import Settings
from finance_context.store import filesystem
from finance_context.store.filesystem import (
    DataDirNotLocalError,
    ensure_data_dir_local,
    is_network_kind,
    kind_from_mounts,
    linux_magic_kind,
    network_status,
    parse_mount_output,
    parse_proc_mounts,
    probe_filesystem_kind,
)

_PROC = """\
rootfs / rootfs rw 0 0
/dev/sda1 / ext4 rw,relatime 0 0
server:/export /mnt/data nfs4 rw,relatime 0 0
server:/export /mnt/my\\040share nfs rw 0 0
//files/share /mnt/share cifs rw 0 0
host:/repo /mnt/ssh fuse.sshfs rw 0 0
"""

_DARWIN = """\
/dev/disk1s1 on / (apfs, sealed, local, read-only, journaled)
//user@host/share on /Volumes/share (smbfs, nodev, nosuid)
server:/export on /Users/me/nfs (nfs, nodev)
host:/x on /Users/me/My Disk (afpfs, nodev)
files.example on /Users/me/dav (webdav, nodev)
"""


def test_proc_mounts_unescape_and_longest_prefix() -> None:
    mounts = parse_proc_mounts(_PROC)
    assert ("/mnt/my share", "nfs") in mounts
    assert kind_from_mounts(mounts, Path("/mnt/data/sessions")) == "nfs4"
    assert kind_from_mounts(mounts, Path("/mnt/my share/jobs")) == "nfs"
    assert kind_from_mounts(mounts, Path("/mnt/share/book")) == "cifs"
    assert kind_from_mounts(mounts, Path("/mnt/ssh/repo")) == "fuse.sshfs"
    assert kind_from_mounts(mounts, Path("/var/lib")) == "ext4"


def test_darwin_mount_output_longest_prefix() -> None:
    mounts = parse_mount_output(_DARWIN)
    assert kind_from_mounts(mounts, Path("/Volumes/share/a")) == "smbfs"
    assert kind_from_mounts(mounts, Path("/Users/me/nfs/job")) == "nfs"
    assert kind_from_mounts(mounts, Path("/Users/me/My Disk/jobs")) == "afpfs"
    assert kind_from_mounts(mounts, Path("/Users/me/dav/a")) == "webdav"
    assert kind_from_mounts(mounts, Path("/Users/me/other")) == "apfs"


@pytest.mark.parametrize(
    ("kind", "network"),
    [
        ("nfs", True),
        ("nfs4", True),
        ("cifs", True),
        ("smb", True),
        ("smbfs", True),
        ("fuse.sshfs", True),
        ("sshfs", True),
        ("afpfs", True),
        ("afp", True),
        ("webdav", True),
        ("ext4", False),
        ("apfs", False),
        ("xfs", False),
        ("tmpfs", False),
        ("btrfs", False),
    ],
)
def test_network_kind_table(kind: str, network: bool) -> None:
    assert is_network_kind(kind) is network
    assert network_status(kind) is network


def test_unknown_and_bare_fuse_are_not_a_decision() -> None:
    assert network_status(None) is None
    assert network_status("") is None
    assert network_status("fuse") is None


def test_linux_statfs_magic_names() -> None:
    assert linux_magic_kind(0x6969) == "nfs"
    assert linux_magic_kind(0xFF534D42) == "cifs"
    assert linux_magic_kind(0x517B) == "smb"
    assert linux_magic_kind(0xEF53) == "ext4"
    assert linux_magic_kind(0x65735546) == "fuse"
    assert linux_magic_kind(0x1234) is None


def test_probe_linux_reads_proc_mounts(monkeypatch, tmp_path: Path) -> None:
    resolved = tmp_path.resolve()
    text = f"/dev/sda1 / ext4 rw 0 0\nserver:/export {resolved} nfs4 rw 0 0\n"
    monkeypatch.setattr(filesystem.sys, "platform", "linux")
    monkeypatch.setattr(filesystem, "_read_proc_mounts", lambda: text)
    assert probe_filesystem_kind(tmp_path) == "nfs4"


def test_probe_linux_statfs_when_proc_mounts_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "linux")
    monkeypatch.setattr(filesystem, "_read_proc_mounts", lambda: None)
    monkeypatch.setattr(filesystem, "_linux_statfs_kind", lambda path: "cifs")
    assert probe_filesystem_kind(tmp_path) == "cifs"


def test_probe_darwin_uses_statfs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "darwin")
    monkeypatch.setattr(filesystem, "_darwin_statfs_kind", lambda path: "smbfs")
    assert probe_filesystem_kind(tmp_path) == "smbfs"


def test_probe_darwin_falls_back_to_mount(monkeypatch, tmp_path: Path) -> None:
    resolved = tmp_path.resolve()
    text = f"/dev/disk1 on / (apfs, local)\nhost:/x on {resolved} (webdav, nodev)\n"
    monkeypatch.setattr(filesystem.sys, "platform", "darwin")
    monkeypatch.setattr(filesystem, "_darwin_statfs_kind", lambda path: None)
    monkeypatch.setattr(filesystem, "_read_mount_command", lambda: text)
    assert probe_filesystem_kind(tmp_path) == "webdav"


def test_probe_unknown_platform_returns_none(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(filesystem.sys, "platform", "win32")
    assert probe_filesystem_kind(tmp_path) is None


def test_startup_refuses_network_fs_unless_allowed(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger="finance_context.store.filesystem")
    with pytest.raises(DataDirNotLocalError, match="DATA_DIR_ALLOW_NETWORK_FS"):
        ensure_data_dir_local(tmp_path, allow_network=False, kind_of=lambda _path: "nfs")
    ensure_data_dir_local(tmp_path, allow_network=True, kind_of=lambda _path: "nfs")
    ensure_data_dir_local(tmp_path, allow_network=False, kind_of=lambda _path: "apfs")
    ensure_data_dir_local(tmp_path, allow_network=False, kind_of=lambda _path: None)
    assert "could not be detected" in caplog.text


def test_create_app_refuses_network_data_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(filesystem, "probe_filesystem_kind", lambda _path: "fuse.sshfs")
    settings = Settings(
        data_dir=tmp_path,
        data_dir_allow_network_fs=False,
        _env_file=None,
    )
    with pytest.raises(DataDirNotLocalError, match="DATA_DIR_ALLOW_NETWORK_FS"):
        create_app(settings)


def test_create_app_allows_network_data_dir_when_configured(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(filesystem, "probe_filesystem_kind", lambda _path: "nfs4")
    settings = Settings(
        data_dir=tmp_path,
        data_dir_allow_network_fs=True,
        _env_file=None,
    )
    create_app(settings)
