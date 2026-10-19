"""LibreOffice headless as the `.xls` -> `.xlsx` converter.

Each call runs in its own process group with a fresh profile and `HOME` under the
call's temporary directory, so no user settings, extensions or a running instance
take part. The child sees only `PATH`, `HOME`, and `LANG`. `prctl(PR_SET_PDEATHSIG)`
kills it if the worker dies; a timeout still kills the whole group.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from finance_context.errors import ContextError
from finance_context.excel.legacy import NO_CONVERTER
from finance_context.settings import Settings

_SEARCH = ("soffice", "libreoffice")
_MAC_APP = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
_STDERR_TAIL = 300
_PR_SET_PDEATHSIG = 1
_LIBC = ctypes.CDLL("libc.so.6", use_errno=True) if sys.platform == "linux" else None
if _LIBC is not None:
    _LIBC.prctl.argtypes = (
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    )
    _LIBC.prctl.restype = ctypes.c_int


class SofficeConverter:
    def __init__(self, binary: str | None = None, *, timeout_sec: float = 300.0) -> None:
        self._binary = binary
        self.timeout_sec = timeout_sec

    @classmethod
    def from_settings(cls, settings: Settings) -> SofficeConverter:
        return cls(settings.soffice_bin, timeout_sec=settings.xls_convert_timeout_sec)

    def binary(self) -> str | None:
        if self._binary:
            return _executable(self._binary)
        for name in _SEARCH:
            found = shutil.which(name)
            if found:
                return found
        return _executable(str(_MAC_APP))

    def available(self) -> bool:
        return self.binary() is not None

    def convert(self, source: Path, out_dir: Path) -> Path:
        binary = self.binary()
        if binary is None:
            raise ContextError("unsupported_media_type", NO_CONVERTER)
        inbox = out_dir / "in"
        outbox = out_dir / "out"
        profile = out_dir / "profile"
        for folder in (inbox, outbox, profile):
            folder.mkdir(parents=True, exist_ok=True)
        book = inbox / "book.xls"
        shutil.copyfile(source, book)
        command = [
            binary,
            f"-env:UserInstallation={profile.as_uri()}",
            "--headless",
            "--invisible",
            "--nologo",
            "--nodefault",
            "--norestore",
            "--nolockcheck",
            "--convert-to",
            "xlsx",
            "--outdir",
            str(outbox),
            str(book),
        ]
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=inbox,
            env=_converter_env(out_dir),
            start_new_session=True,
            preexec_fn=_die_with_parent,
        )
        pgid = _pgid(process)
        try:
            _stdout, stderr = process.communicate(timeout=self.timeout_sec)
        except subprocess.TimeoutExpired:
            if pgid is not None:
                _kill_group(pgid)
            process.communicate()
            raise ContextError(
                "conversion_failed", f"soffice did not finish in {self.timeout_sec:g}s"
            ) from None
        finally:
            if pgid is not None:
                _kill_group(pgid)
        produced = outbox / "book.xlsx"
        if process.returncode != 0 or not produced.is_file():
            detail = f"soffice exited with {process.returncode} and wrote no .xlsx"
            tail = stderr.decode("utf-8", errors="replace").strip()[-_STDERR_TAIL:]
            raise ContextError("conversion_failed", f"{detail}: {tail}" if tail else detail)
        return produced


def _executable(candidate: str) -> str | None:
    found = shutil.which(candidate)
    if found:
        return found
    path = Path(candidate)
    if path.is_file() and os.access(path, os.X_OK):
        return str(path)
    return None


def _converter_env(home: Path) -> dict[str, str]:
    env = {"HOME": str(home)}
    path = os.environ.get("PATH")
    if path:
        env["PATH"] = path
    lang = os.environ.get("LANG")
    if lang:
        env["LANG"] = lang
    return env


def _orphaned(parent_at_fork: int, parent_after_prctl: int) -> bool:
    """The worker died before the death signal was armed.

    A worker that is already PID 1, such as the container entrypoint, is not dead.
    """
    return parent_at_fork != 1 and parent_after_prctl == 1


def _die_with_parent() -> None:
    """SIGKILL this process when the worker exits. Runs after `setsid`, before exec."""
    if _LIBC is None:
        return
    parent = os.getppid()
    if _LIBC.prctl(_PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0:
        return
    if _orphaned(parent, os.getppid()):
        os.kill(os.getpid(), signal.SIGKILL)


def _pgid(process: subprocess.Popen) -> int | None:
    try:
        group = os.getpgid(process.pid)
    except ProcessLookupError:
        return None
    if group <= 0:
        return None
    return group


def _kill_group(pgid: int) -> None:
    """soffice forks `soffice.bin`; the leader alone is not enough.

    Signal only while the group still exists. After a successful `communicate`
    the leader's pid can be reused.
    """
    if pgid <= 0:
        return
    try:
        os.killpg(pgid, 0)
    except (ProcessLookupError, PermissionError):
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
