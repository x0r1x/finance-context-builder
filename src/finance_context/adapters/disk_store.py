from __future__ import annotations

from pathlib import Path

from finance_context.store.paths import LOCAL_SESSION, session_job_dir, shared_book_dir


class DiskStore:
    def __init__(self, root: Path, *, session_id: str = LOCAL_SESSION) -> None:
        self.root = root
        self.session_id = session_id or LOCAL_SESSION

    def dest_dir(self, job_id: str) -> Path:
        return session_job_dir(self.root, job_id, self.session_id)

    def shared_dir(self, job_id: str) -> Path:
        return shared_book_dir(self.root, job_id)
