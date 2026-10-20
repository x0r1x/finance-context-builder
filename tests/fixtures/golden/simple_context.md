# Financial context

- Schema: `1.15.0`
- Job: `job1`
- Status: `succeeded`
- Source: `simple.xlsx`
- Sheets: 1
- Cells: 4
- Formulas: 1
- Content completeness: 1.00 (3/3 numeric and formula cells in context)

## CF / `CF!r1`

Block: `CF!r1`. Kind: `timeline`. grain=year Periods: 2. Rows: 1.

| Label | Row | Path | Kind | Disposition | Unit | Time | Formula | Cells | 2023 (B) | 2024E (C) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Opening cash | CF\|2\|CF!r1 |  | fact |  | k£ ×1000 | bop/beginning/first | =RC[-1] |  | 100 (100000) [CF!B2] | 110 (110000) [CF!C2] |
