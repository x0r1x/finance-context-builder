from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def job_id_for(content_sha256: str) -> str:
    return hashlib.sha256(f"context:{content_sha256}".encode()).hexdigest()
