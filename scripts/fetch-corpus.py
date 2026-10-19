#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "resources" / "corpus.lock.json"
DEST = ROOT / "resources" / "corpus"


def fetch_books(spec: dict, dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    errors = 0
    for book in spec["books"]:
        if book.get("available") is False:
            reason = book.get("reason") or "marked unavailable"
            print(f"skip {book['slug']}: {reason}")
            continue
        path = dest / book["filename"]
        urls = [book["url"]] if book.get("url") else list(book.get("urls") or [])
        data = None
        last_error: Exception | None = None
        for url in urls:
            print(f"fetch {book['slug']} <- {url}")
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    data = response.read()
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                print(f"  skip: {exc}")
        if data is None:
            print(f"  failed: {last_error}")
            errors += 1
            continue
        digest = hashlib.sha256(data).hexdigest()
        expected = book.get("sha256")
        if expected and expected != digest:
            print(f"  hash mismatch: {digest}")
            errors += 1
            continue
        path.write_bytes(data)
        print(f"  wrote {path} sha256={digest}")
    return 1 if errors and not any(dest.glob("*.xlsx")) else 0


def main() -> int:
    spec = json.loads(LOCK.read_text(encoding="utf-8"))
    return fetch_books(spec, DEST)


if __name__ == "__main__":
    sys.exit(main())
