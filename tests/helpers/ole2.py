"""Minimal OLE2 (Compound File Binary) containers for tests.

Only the directory is real: legacy `.xls` has a `Workbook` stream, an encrypted
OOXML book has `EncryptionInfo` and `EncryptedPackage`. Streams carry no data.
"""

from __future__ import annotations

import struct
from pathlib import Path

SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
_FREE = 0xFFFFFFFF
_END = 0xFFFFFFFE
_FAT = 0xFFFFFFFD
_SECTOR = 512
_ENTRIES_PER_SECTOR = _SECTOR // 128

XLS_STREAMS = ("Workbook", "\x05SummaryInformation", "\x05DocumentSummaryInformation")
ENCRYPTED_STREAMS = ("EncryptionInfo", "EncryptedPackage", "\x06DataSpaces")


def write_ole2(path: Path, streams: tuple[str, ...] | list[str]) -> Path:
    entries = [_entry("Root Entry", 5), *(_entry(name, 2) for name in streams)]
    while len(entries) % _ENTRIES_PER_SECTOR:
        entries.append(b"\x00" * 128)
    dir_sectors = len(entries) // _ENTRIES_PER_SECTOR
    fat = [_FAT]
    for index in range(dir_sectors):
        fat.append(index + 2 if index + 1 < dir_sectors else _END)
    fat.extend([_FREE] * (_SECTOR // 4 - len(fat)))
    header = bytearray(_SECTOR)
    header[0:8] = SIGNATURE
    struct.pack_into("<HHHHH", header, 24, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 40, 0, 1, 1, 0, 4096, _END, 0, _END, 0)
    difat = [0] + [_FREE] * 108
    struct.pack_into("<109I", header, 76, *difat)
    body = struct.pack(f"<{_SECTOR // 4}I", *fat) + b"".join(entries)
    path.write_bytes(bytes(header) + body)
    return path


def _entry(name: str, kind: int) -> bytes:
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    out = bytearray(128)
    out[0 : len(encoded)] = encoded
    struct.pack_into("<HBB", out, 64, len(encoded), kind, 1)
    struct.pack_into("<III", out, 68, _FREE, _FREE, _FREE)
    struct.pack_into("<I", out, 116, _END)
    return bytes(out)
