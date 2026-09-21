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

    if isinstance(document.get("blocks"), list):
        rows = []
        for block in document["blocks"]:
            if isinstance(block, dict):
                block_rows = block.get("rows")
                if isinstance(block_rows, list):
                    rows.extend(row for row in block_rows if isinstance(row, dict))
    elif isinstance(document.get("unmapped"), list):
        rows = document["unmapped"]
    elif isinstance(document.get("rows"), list):
        rows = document["rows"]
    else:
        raise ValueError("input document must contain blocks, rows, or unmapped")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("every extracted row must be a JSON object")

    unmapped = [_compact(row) for row in rows if _is_unmapped(row)]
    return {"count": len(unmapped), "rows": unmapped}


def _is_unmapped(row: dict[str, Any]) -> bool:
    if row.get("concept_id") is not None:
        return False
    mapping = row.get("mapping") if isinstance(row.get("mapping"), dict) else {}
    disposition = row.get("disposition") or mapping.get("disposition") or "abstained"
    return disposition == "abstained"


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    out = {key: value for key, value in row.items() if key != "values"}
    source = out.get("source")
    if isinstance(source, dict) and source.get("sheet") and source.get("addr"):
        out.setdefault("ref", f"{source['sheet']}!{source['addr']}")
    return out


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
