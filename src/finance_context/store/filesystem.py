"""Detect whether DATA_DIR sits on a network filesystem.

Linux reads `/proc/mounts` and falls back to `statfs`. macOS reads `statfs`
and falls back to `mount`. An unknown platform, or a probe that fails, is
reported as undetected so startup can log and continue.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import re
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

_LOGGER = logging.getLogger("finance_context.store.filesystem")

# Names seen in /proc/mounts, statfs, and macOS `mount`. Bare "fuse" is not
# here: it is local as often as it is sshfs, so it stays undetected.
_NETWORK_KINDS = frozenset(
    {
        "9p",
        "9pnet",
        "afs",
        "afp",
        "afpfs",
        "ceph",
        "cifs",
        "coda",
        "davfs",
        "fuse.davfs",
        "fuse.rclone",
        "fuse.s3fs",
        "fuse.sshfs",
        "glusterfs",
        "lustre",
        "ncp",
        "ncpfs",
        "nfs",
        "nfs3",
        "nfs4",
        "rclone",
        "s3fs",
        "smb",
        "smb2",
        "smb3",
        "smbfs",
        "sshfs",
        "webdav",
    }
)
_NETWORK_PREFIXES = ("nfs", "fuse.sshfs", "fuse.rclone", "fuse.davfs", "fuse.s3fs")

# Linux statfs f_type. Include local types so a successful statfs is a decision.
_LINUX_MAGIC = {
    0x6969: "nfs",
    0x517B: "smb",
    0xFF534D42: "cifs",
    0xFE534D42: "smb2",
    0x73757245: "coda",
    0x5346414F: "afs",
    0x564C: "ncp",
    0x01021997: "9p",
    0x00C36400: "ceph",
    0x0BD00BD0: "lustre",
    0x61666C67: "glusterfs",
    0xEF53: "ext4",
    0x58465342: "xfs",
    0x9123683E: "btrfs",
    0x01021994: "tmpfs",
    0x794C7630: "overlay",
    0x65735546: "fuse",
}

_OCTAL = re.compile(r"\\([0-7]{3})")


class DataDirNotLocalError(RuntimeError):
    """DATA_DIR is on a network filesystem and the operator did not allow it."""


class _DarwinStatFS(ctypes.Structure):
    _fields_ = [
        ("f_bsize", ctypes.c_uint32),
        ("f_iosize", ctypes.c_int32),
        ("f_blocks", ctypes.c_uint64),
        ("f_bfree", ctypes.c_uint64),
        ("f_bavail", ctypes.c_uint64),
        ("f_files", ctypes.c_uint64),
        ("f_ffree", ctypes.c_uint64),
        ("f_fsid", ctypes.c_int32 * 2),
        ("f_owner", ctypes.c_uint32),
        ("f_type", ctypes.c_uint32),
        ("f_flags", ctypes.c_uint32),
        ("f_fssubtype", ctypes.c_uint32),
        ("f_fstypename", ctypes.c_char * 16),
        ("f_mntonname", ctypes.c_char * 1024),
        ("f_mntfromname", ctypes.c_char * 1024),
        ("f_flags_ext", ctypes.c_uint32),
        ("f_reserved", ctypes.c_uint32 * 7),
    ]


def is_network_kind(kind: str) -> bool:
    name = kind.strip().lower()
    if name in _NETWORK_KINDS:
        return True
    return any(name.startswith(prefix) for prefix in _NETWORK_PREFIXES)


def network_status(kind: str | None) -> bool | None:
    """True when `kind` is a network filesystem, False when it is local.

    None means the probe could not decide (missing data, or bare FUSE).
    """
    if kind is None:
        return None
    name = kind.strip().lower()
    if not name or name == "fuse":
        return None
    return is_network_kind(name)


def linux_magic_kind(raw: int) -> str | None:
    return _LINUX_MAGIC.get(raw & 0xFFFFFFFF)


def parse_proc_mounts(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        rows.append((_unescape(parts[1]), _unescape(parts[2])))
    return rows


def parse_mount_output(text: str) -> list[tuple[str, str]]:
    """macOS and BSD `mount` lines: `device on mountpoint (fstype, options)`."""
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
        parsed = _parse_mount_line(line)
        if parsed is not None:
            rows.append(parsed)
    return rows


def kind_from_mounts(mounts: list[tuple[str, str]], path: Path) -> str | None:
    """Filesystem type of the longest mount point that contains `path`."""
    best_len = -1
    best: str | None = None
    for mount, kind in mounts:
        mount_path = Path(mount)
        if not mount_path.is_absolute():
            continue
        try:
            inside = path.is_relative_to(mount_path)
        except (ValueError, OSError):
            continue
        if not inside:
            continue
        length = len(mount_path.parts)
        # A later row of the same length is the mount stacked on top (rootfs, then ext4).
        if length >= best_len:
            best_len = length
            best = kind
    return best


def probe_filesystem_kind(path: Path) -> str | None:
    anchor = _existing_anchor(path)
    try:
        if sys.platform == "linux":
            return _probe_linux(anchor)
        if sys.platform == "darwin":
            return _probe_darwin(anchor)
    except (OSError, UnicodeError, ValueError):
        return None
    return None


def ensure_data_dir_local(
    path: Path,
    *,
    allow_network: bool,
    kind_of: Callable[[Path], str | None] | None = None,
) -> None:
    """Refuse startup when `path` is on a network filesystem.

    `allow_network` is `DATA_DIR_ALLOW_NETWORK_FS`. When the probe cannot tell,
    log and continue.
    """
    if allow_network:
        return
    probe = kind_of or probe_filesystem_kind
    try:
        kind = probe(path)
    except (OSError, UnicodeError, ValueError):
        _log_unknown(path)
        return
    status = network_status(kind)
    if status is None:
        _log_unknown(path)
        return
    if status:
        raise DataDirNotLocalError(
            f"DATA_DIR {path} is on a network filesystem ({kind}). "
            "Refusing to start. Set DATA_DIR_ALLOW_NETWORK_FS=true to allow it."
        )


def _log_unknown(path: Path) -> None:
    _LOGGER.warning("DATA_DIR filesystem type could not be detected for %s; continuing", path)


def _existing_anchor(path: Path) -> Path:
    try:
        current = path.expanduser().resolve()
    except OSError:
        current = path.expanduser().absolute()
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def _probe_linux(path: Path) -> str | None:
    text = _read_proc_mounts()
    if text:
        kind = kind_from_mounts(parse_proc_mounts(text), path)
        if kind:
            return kind
    return _linux_statfs_kind(path)


def _probe_darwin(path: Path) -> str | None:
    kind = _darwin_statfs_kind(path)
    if kind:
        return kind
    text = _read_mount_command()
    if not text:
        return None
    return kind_from_mounts(parse_mount_output(text), path)


def _read_proc_mounts() -> str | None:
    try:
        return Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError:
        return None


def _read_mount_command() -> str | None:
    try:
        completed = subprocess.run(
            ["mount"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _linux_statfs_kind(path: Path) -> str | None:
    try:
        buffer = ctypes.create_string_buffer(256)
        libc = ctypes.CDLL(None, use_errno=True)
        libc.statfs.argtypes = [ctypes.c_char_p, ctypes.c_void_p]
        libc.statfs.restype = ctypes.c_int
        if libc.statfs(os.fsencode(path), buffer) != 0:
            return None
        (raw,) = struct.unpack_from("@l", buffer.raw, 0)
    except (OSError, struct.error, AttributeError):
        return None
    return linux_magic_kind(raw)


def _darwin_statfs_kind(path: Path) -> str | None:
    try:
        library = ctypes.util.find_library("c") or "libc.dylib"
        libc = ctypes.CDLL(library, use_errno=True)
        buf = _DarwinStatFS()
        libc.statfs.argtypes = [ctypes.c_char_p, ctypes.POINTER(_DarwinStatFS)]
        libc.statfs.restype = ctypes.c_int
        if libc.statfs(os.fsencode(path), ctypes.byref(buf)) != 0:
            return None
        raw = bytes(buf.f_fstypename).split(b"\x00", 1)[0]
        if not raw:
            return None
        return raw.decode("ascii", "replace")
    except (OSError, UnicodeError, AttributeError):
        return None


def _unescape(token: str) -> str:
    return _OCTAL.sub(lambda match: chr(int(match.group(1), 8)), token)


def _parse_mount_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if " (" not in line or not line.endswith(")"):
        return None
    head, _, tail = line.rpartition(" (")
    fstype = tail[:-1].split(",", 1)[0].strip().lower()
    _device, separator, mount = head.rpartition(" on ")
    if not separator or not mount or not fstype:
        return None
    return mount, fstype
