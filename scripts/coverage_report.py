#!/usr/bin/env python3
"""Counters for the sheet-coverage branch. No row labels and no cell values.

Point it at a folder of context.json files from one build of the branch.
Pass --before with the same books built on main. The report is the file to
send back: it says where a list or a matrix appeared, and where a timeline
lost rows or period columns.

A context.json is recognized as context/context.json or *.context.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_KINDS = ("timeline", "params", "key_value", "matrix")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("after", type=Path, help="folder or file from this branch")
    parser.add_argument(
        "--before",
        type=Path,
        default=None,
        help="folder or file from main, matched by source file name",
    )
    parser.add_argument("--out", type=Path, default=None, help="write the report here")
    args = parser.parse_args(argv)
    after = _load_tree(args.after)
    if not after:
        print(f"no context.json under {args.after}", file=sys.stderr)
        return 1
    before = _load_tree(args.before) if args.before else {}
    text = render(after, before)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


def render(after: dict[str, dict[str, Any]], before: dict[str, dict[str, Any]]) -> str:
    """One report. Keys of both maps are the source file name."""
    lines = [f"books {len(after)}", f"paired {sum(1 for name in after if name in before)}", ""]
    for name in sorted(after):
        lines.append(_book_section(name, after[name]))
        if name in before:
            lines.append(_delta_section(before[name], after[name]))
        lines.append("")
    lost = [
        name
        for name, doc in after.items()
        if name in before and _timeline_loss(before[name], doc)
    ]
    lines.append(f"timeline_loss_books {len(lost)}")
    return "\n".join(lines).rstrip() + "\n"


def _book_section(name: str, doc: dict[str, Any]) -> str:
    summary = _summarize(doc)
    lines = [
        f"BOOK {name}",
        f"status {summary['status']}",
        f"sheets {summary['sheet_count']}",
        _kind_line("blocks", summary["blocks"]),
        _kind_line("rows", summary["rows"]),
        f"empty_sheets {summary['empty_sheets']}",
        f"coverage_notes {summary['warnings']['coverage']}",
        f"chart_notes {summary['warnings']['chart']}",
        f"hidden_sheet_warnings {summary['warnings']['hidden_sheet']}",
        f"unparsed_period_warnings {summary['warnings']['unparsed_period']}",
        f"missing_cached_warnings {summary['warnings']['missing_cached']}",
        f"other_warnings {summary['warnings']['other']}",
    ]
    for sheet in summary["sheets"]:
        if not _sheet_worth_printing(sheet):
            continue
        lines.append(
            "sheet {name} timeline_rows={timeline_rows} timeline_periods={timeline_periods} "
            "params_rows={params_rows} key_value_rows={key_value_rows} "
            "matrix_rows={matrix_rows} matrix_columns={matrix_columns}".format(**sheet)
        )
    return "\n".join(lines)


def _delta_section(old: dict[str, Any], new: dict[str, Any]) -> str:
    before = _summarize(old)
    after = _summarize(new)
    flags: list[str] = []
    if _timeline_loss(old, new):
        flags.append("TIMELINE_LOSS")
    if after["rows"]["key_value"] or after["rows"]["matrix"]:
        flags.append("HAS_COVERAGE")
    lines = ["DELTA " + ",".join(flags)]
    for kind in _KINDS:
        lines.append(
            f"{kind}_rows {before['rows'][kind]} -> {after['rows'][kind]}"
        )
    lines.append(f"empty_sheets {before['empty_sheets']} -> {after['empty_sheets']}")
    old_sheets = {item["name"]: item for item in before["sheets"]}
    for item in after["sheets"]:
        prev = old_sheets.get(item["name"])
        if prev is None:
            continue
        row_delta = item["timeline_rows"] - prev["timeline_rows"]
        period_delta = item["timeline_periods"] - prev["timeline_periods"]
        if row_delta == 0 and period_delta == 0:
            continue
        lines.append(
            f"timeline_shift {item['name']} rows {prev['timeline_rows']}->{item['timeline_rows']} "
            f"periods {prev['timeline_periods']}->{item['timeline_periods']}"
        )
    return "\n".join(lines)


def _timeline_loss(old: dict[str, Any], new: dict[str, Any]) -> bool:
    before = _summarize(old)
    after = _summarize(new)
    if after["rows"]["timeline"] < before["rows"]["timeline"]:
        return True
    old_sheets = {item["name"]: item for item in before["sheets"]}
    for item in after["sheets"]:
        prev = old_sheets.get(item["name"])
        if prev is None:
            continue
        if item["timeline_rows"] < prev["timeline_rows"]:
            return True
        if item["timeline_periods"] < prev["timeline_periods"]:
            return True
    return False


def _sheet_worth_printing(sheet: dict[str, Any]) -> bool:
    return bool(
        sheet["key_value_rows"]
        or sheet["matrix_rows"]
        or sheet["timeline_rows"] == 0
        or sheet["params_rows"]
    )


def _summarize(doc: dict[str, Any]) -> dict[str, Any]:
    blocks: Counter[str] = Counter()
    rows: Counter[str] = Counter()
    by_sheet: dict[str, dict[str, Any]] = {}
    for block in doc.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("kind") or "timeline")
        if kind not in _KINDS:
            kind = "other"
        sheet = str(block.get("sheet") or "")
        block_rows = block.get("rows") or []
        n_rows = len(block_rows) if isinstance(block_rows, list) else 0
        periods = block.get("periods") or []
        n_periods = len(periods) if isinstance(periods, list) else 0
        blocks[kind] += 1
        rows[kind] += n_rows
        slot = by_sheet.setdefault(sheet, _empty_sheet(sheet))
        slot[f"{kind}_rows"] = slot.get(f"{kind}_rows", 0) + n_rows
        if kind == "matrix":
            slot["matrix_columns"] = max(slot["matrix_columns"], n_periods)
        if kind == "timeline":
            slot["timeline_periods"] += _timeline_periods(block, doc)
    named = _sheet_names(doc)
    for name in named:
        by_sheet.setdefault(name, _empty_sheet(name))
    sheets = [by_sheet[name] for name in sorted(by_sheet)]
    empty = sum(1 for item in sheets if _sheet_rows(item) == 0)
    return {
        "status": str((doc.get("meta") or {}).get("status") or ""),
        "sheet_count": len(named) or len(by_sheet),
        "blocks": {kind: blocks[kind] for kind in (*_KINDS, "other")},
        "rows": {kind: rows[kind] for kind in (*_KINDS, "other")},
        "empty_sheets": empty,
        "warnings": _warnings(doc.get("warnings") or []),
        "sheets": sheets,
    }


def _empty_sheet(name: str) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "matrix_columns": 0, "timeline_periods": 0}
    for kind in (*_KINDS, "other"):
        item[f"{kind}_rows"] = 0
    return item


def _sheet_rows(sheet: dict[str, Any]) -> int:
    return sum(int(sheet[f"{kind}_rows"]) for kind in (*_KINDS, "other"))


def _timeline_periods(block: dict[str, Any], doc: dict[str, Any]) -> int:
    periods = block.get("periods") or []
    if isinstance(periods, list) and periods:
        return len(periods)
    wanted = {str(item) for item in block.get("axis_ids") or []}
    if not wanted:
        return 0
    total = 0
    for axis in doc.get("axes") or []:
        if not isinstance(axis, dict) or str(axis.get("id") or "") not in wanted:
            continue
        points = axis.get("periods") or []
        if isinstance(points, list):
            total += len(points)
    return total


def _sheet_names(doc: dict[str, Any]) -> list[str]:
    workbook = doc.get("workbook") or {}
    sheets = workbook.get("sheets") or []
    if not isinstance(sheets, list):
        return []
    names: list[str] = []
    for item in sheets:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return names


def _warnings(warnings: list[Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for item in warnings:
        counts[_warning_class(str(item))] += 1
    for key in ("coverage", "chart", "hidden_sheet", "unparsed_period", "missing_cached", "other"):
        counts.setdefault(key, 0)
    return counts


def _warning_class(text: str) -> str:
    if "included without a period axis" in text:
        return "coverage"
    if "chart copy" in text:
        return "chart"
    if text.startswith("Hidden sheet"):
        return "hidden_sheet"
    if text.startswith("Unparsed period"):
        return "unparsed_period"
    if "missing cached" in text:
        return "missing_cached"
    return "other"


def _kind_line(label: str, counts: dict[str, int]) -> str:
    body = " ".join(f"{kind}={counts[kind]}" for kind in (*_KINDS, "other"))
    return f"{label} {body}"


def _load_tree(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for file in _context_files(path):
        doc = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or "blocks" not in doc:
            continue
        found[_book_name(doc, file)] = doc
    return found


def _context_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    named = sorted(path.rglob("context.json"))
    copied = sorted(path.rglob("*.context.json"))
    return named + [item for item in copied if item.name != "context.json"]


def _book_name(doc: dict[str, Any], file: Path) -> str:
    source = (doc.get("meta") or {}).get("source_filename")
    if isinstance(source, str) and source.strip():
        return source.strip()
    if file.name.endswith(".context.json"):
        return file.name[: -len(".context.json")]
    return file.parent.parent.name or file.parent.name


if __name__ == "__main__":
    raise SystemExit(main())
