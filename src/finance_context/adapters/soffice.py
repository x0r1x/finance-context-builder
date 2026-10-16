"""LibreOffice headless as the `.xls` -> `.xlsx` converter.

Each call runs in its own process group with a fresh profile and `HOME` under the
call's temporary directory, so no user settings, extensions or a running instance
take part. Proxy variables are dropped; the conversion itself needs no network,
and the container is expected to run without egress. A timeout kills the whole group.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
from pathlib import Path

from finance_context.errors import ContextError
from finance_context.excel.legacy import NO_CONVERTER
from finance_context.settings import Settings

_SEARCH = ("soffice", "libreoffice")
_MAC_APP = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
_PROXY_VARS = {"http_proxy", "https_proxy", "ftp_proxy", "all_proxy", "no_proxy"}
_STDERR_TAIL = 300


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
            env=_offline_env(out_dir),
            start_new_session=True,
        )
        try:
            _stdout, stderr = process.communicate(timeout=self.timeout_sec)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            process.communicate()
            raise ContextError(
                "conversion_failed", f"soffice did not finish in {self.timeout_sec:g}s"
            ) from None
        finally:
            _kill_group(process)
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


def _offline_env(home: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items() if key.casefold() not in _PROXY_VARS
    }
    env["HOME"] = str(home)
    return env


def _kill_group(process: subprocess.Popen) -> None:
    """soffice forks `soffice.bin`; the leader alone is not enough."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
