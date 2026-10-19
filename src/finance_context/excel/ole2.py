"""OLE2 (Compound File Binary) sniffing: legacy `.xls` versus an encrypted OOXML book.

Both start with `D0 CF 11 E0 A1 B1 1A E1`. Only the directory names tell them apart:
BIFF keeps a `Workbook` (or BIFF5 `Book`) stream, Office encryption wraps the zip in
`EncryptionInfo` and `EncryptedPackage`. Stream data is never read.
"""

from __future__ import annotations

import os
import struct
from pathlib import Path
from typing import BinaryIO, Literal

OLE2_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")
Ole2Kind = Literal["xls", "encrypted", "other"]

_HEADER = 512
_DIR_ENTRY = 128
_MAX_SECTOR = 0xFFFFFFFA
_END_OF_CHAIN = 0xFFFFFFFE
_ENTRY_TYPES = {1, 2, 5}  # storage, stream, root
_XLS_STREAMS = {"workbook", "book"}
_ENCRYPTION_STREAMS = {"encryptioninfo", "encryptedpackage"}


def is_ole2(head: bytes) -> bool:
    return head[: len(OLE2_SIGNATURE)] == OLE2_SIGNATURE


def is_ole2_file(path: Path) -> bool:
    with path.open("rb") as handle:
        return is_ole2(handle.read(len(OLE2_SIGNATURE)))


def ole2_kind(path: Path) -> Ole2Kind:
    try:
        names = {name.casefold() for name in _entry_names(path)}
    except (OSError, ValueError, struct.error):
        return "other"
    if names & _ENCRYPTION_STREAMS:
        return "encrypted"
    if names & _XLS_STREAMS:
        return "xls"
    return "other"


def _entry_names(path: Path) -> set[str]:
    with path.open("rb") as handle:
        header = handle.read(_HEADER)
        if len(header) < _HEADER or not is_ole2(header):
            raise ValueError("not an OLE2 header")
        shift = struct.unpack_from("<H", header, 30)[0]
        if shift not in (9, 12):
            raise ValueError("sector size")
        reader = _Sectors(handle, 1 << shift)
        fat_sectors = _fat_sectors(header, reader)
        names: set[str] = set()
        sector = struct.unpack_from("<I", header, 48)[0]
        seen: set[int] = set()
        for _ in range(reader.count):
            if sector == _END_OF_CHAIN:
                return names
            if sector > _MAX_SECTOR or sector in seen:
                raise ValueError("directory chain loops")
            seen.add(sector)
            data = reader.read(sector)
            for offset in range(0, len(data), _DIR_ENTRY):
                name = _entry_name(data[offset : offset + _DIR_ENTRY])
                if name is not None:
                    names.add(name)
            sector = reader.next(sector, fat_sectors)
        raise ValueError("directory chain does not end")


class _Sectors:
    def __init__(self, handle: BinaryIO, size: int) -> None:
        self.handle = handle
        self.size = size
        self.file_size = os.fstat(handle.fileno()).st_size
        self.count = self.file_size // size
        self._fat: dict[int, tuple[int, ...]] = {}

    def read(self, sector: int) -> bytes:
        if sector > _MAX_SECTOR or (sector + 2) * self.size > self.file_size:
            raise ValueError("sector out of range")
        self.handle.seek((sector + 1) * self.size)
        data = self.handle.read(self.size)
        if len(data) != self.size:
            raise ValueError("short sector")
        return data

    def next(self, sector: int, fat_sectors: list[int]) -> int:
        per_sector = self.size // 4
        index = sector // per_sector
        if index >= len(fat_sectors):
            raise ValueError("sector outside the FAT")
        if index not in self._fat:
            self._fat[index] = struct.unpack(f"<{per_sector}I", self.read(fat_sectors[index]))
        return self._fat[index][sector % per_sector]


def _fat_sectors(header: bytes, reader: _Sectors) -> list[int]:
    # Header counts are attacker-controlled uint32s. A DIFAT sector that points at
    # itself, or a count past the end of the file, must not walk more sectors than
    # the upload contains.
    limit = reader.file_size // reader.size
    count = min(struct.unpack_from("<I", header, 44)[0], limit)
    found = [sid for sid in struct.unpack_from("<109I", header, 76) if sid <= _MAX_SECTOR]
    sector, remaining = struct.unpack_from("<II", header, 68)
    remaining = min(remaining, limit)
    per_sector = reader.size // 4
    seen: set[int] = set()
    while remaining and sector <= _MAX_SECTOR and len(found) < count:
        if sector in seen:
            raise ValueError("DIFAT chain loops")
        seen.add(sector)
        values = struct.unpack(f"<{per_sector}I", reader.read(sector))
        found.extend(sid for sid in values[:-1] if sid <= _MAX_SECTOR)
        sector = values[-1]
        remaining -= 1
    return found[:count]


def _entry_name(entry: bytes) -> str | None:
    if len(entry) < _DIR_ENTRY or entry[66] not in _ENTRY_TYPES:
        return None
    length = struct.unpack_from("<H", entry, 64)[0]
    if not 2 <= length <= 64 or length % 2:
        return None
    return entry[: length - 2].decode("utf-16-le", errors="replace")
