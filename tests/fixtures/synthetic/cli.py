"""Command line for ``scripts/gen_synthetic.py``.

Examples::

    uv run python scripts/gen_synthetic.py rel 20000 /tmp/rel20k.xlsx
    uv run python scripts/gen_synthetic.py series 10000 /tmp/series10k.xlsx
    uv run python scripts/gen_synthetic.py hops 10000 /tmp/hops10k.xlsx
    uv run python scripts/gen_synthetic.py hops-tl 100000 /tmp/hops-tl100k.xlsx
    uv run python scripts/gen_synthetic.py dense 10000 /tmp/dense2m.xlsx
    uv run python scripts/gen_synthetic.py outline /tmp/outline.xlsx
    uv run python scripts/gen_synthetic.py sumif 10000 /tmp/sumif10k.xlsx
    uv run python scripts/gen_synthetic.py text 200000 /tmp/text200k.xlsx --strlen 64
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tests.fixtures.synthetic.books import (
    DENSE_COLUMNS,
    REL_PERIODS,
    SERIES_PERIODS,
    TEXT_STRLEN,
    dense,
    hops,
    hops_tl,
    outline,
    rel,
    series,
    sumif,
    text,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write one deterministic synthetic workbook.",
    )
    sub = parser.add_subparsers(dest="shape", required=True)

    rel_cmd = sub.add_parser("rel", help="rows × periods of =1+1 cached as 2")
    _rows_out(rel_cmd)
    rel_cmd.add_argument("--periods", type=int, default=REL_PERIODS)

    series_cmd = sub.add_parser("series", help="relative formula grid")
    _rows_out(series_cmd)
    series_cmd.add_argument("--periods", type=int, default=SERIES_PERIODS)

    hops_cmd = sub.add_parser("hops", help="one-period hop chain (params block)")
    _rows_out(hops_cmd)

    hops_tl_cmd = sub.add_parser("hops-tl", help="two-period hop chain (timeline)")
    _rows_out(hops_tl_cmd)

    dense_cmd = sub.add_parser("dense", help="rows × value columns")
    _rows_out(dense_cmd)
    dense_cmd.add_argument("--columns", type=int, default=DENSE_COLUMNS)

    outline_cmd = sub.add_parser("outline", help="outlines and unit-text rows")
    outline_cmd.add_argument("path", type=Path)
    outline_cmd.add_argument("--cycles", type=int, default=1)

    sumif_cmd = sub.add_parser("sumif", help="SUMIF(A:A) plus a running total")
    _rows_out(sumif_cmd)

    text_cmd = sub.add_parser("text", help="unique shared strings")
    _rows_out(text_cmd)
    text_cmd.add_argument("--strlen", type=int, default=TEXT_STRLEN)
    text_cmd.add_argument("--seed", type=int, default=1)

    args = parser.parse_args(argv)
    written = _write(args)
    print(written)
    return 0


def _rows_out(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("rows", type=int)
    parser.add_argument("path", type=Path)


def _write(args: argparse.Namespace) -> Path:
    shape = args.shape
    if shape == "rel":
        return rel(args.path, args.rows, periods=args.periods)
    if shape == "series":
        return series(args.path, args.rows, periods=args.periods)
    if shape == "hops":
        return hops(args.path, args.rows)
    if shape == "hops-tl":
        return hops_tl(args.path, args.rows)
    if shape == "dense":
        return dense(args.path, args.rows, columns=args.columns)
    if shape == "outline":
        return outline(args.path, cycles=args.cycles)
    if shape == "sumif":
        return sumif(args.path, args.rows)
    if shape == "text":
        return text(args.path, args.rows, strlen=args.strlen, seed=args.seed)
    raise AssertionError(shape)


if __name__ == "__main__":
    raise SystemExit(main())
