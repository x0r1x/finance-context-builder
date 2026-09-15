#!/usr/bin/env python3
"""Extract rows without a concept mapping from mapping or context JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def extract_unmapped(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("input document must be a JSON object")

    if "unmapped" in document:
        rows = document["unmapped"]
        filter_rows = False
    else:
        rows = document.get("rows")
        filter_rows = True
    if not isinstance(rows, list):
        raise ValueError("input document must contain a 'rows' or 'unmapped' array")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("every extracted row must be a JSON object")

    unmapped = [row for row in rows if row.get("concept_id") is None]
    if filter_rows:
        unmapped = [
            row
            for row in unmapped
            if row.get("disposition", "abstained") != "excluded"
        ]
    return {"count": len(unmapped), "rows": unmapped}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract unmapped rows from mapping.json or context.json."
    )
    parser.add_argument("input", type=Path, help="path to mapping.json or context.json")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="output path (default: unmapped.json next to the input file)",
    )
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    output = args.output or args.input.with_name("unmapped.json")

    try:
        document = json.loads(args.input.read_text(encoding="utf-8"))
        result = extract_unmapped(document)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))

    print(f"Wrote {result['count']} unmapped row(s) to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
