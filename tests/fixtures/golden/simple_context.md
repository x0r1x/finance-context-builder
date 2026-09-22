# Financial context

- Schema: `1.12.0`
- Job: `job1`
- Status: `succeeded`
- Source: `simple.xlsx`
- Sheets: 1
- Cells: 4
- Formulas: 1
- Content completeness: 1.00 (1/1 layout rows)
- Concept coverage: 1.00 (1/1 annotatable)
- Label coverage: 0.00
- Semantic coverage: 0.00
- Unit coverage: 0.00
- Temporal coverage: 0.00
- Formula coverage: 0.00
- Confidence threshold passed: false

## CF / `CF!r1`

Block: `CF!r1`. Kind: `timeline`. grain=year Periods: 2. Rows: 1.

#### Opening cash

- Row: `CF|2|CF!r1`
- Kind: fact · Disposition: mapped · Concept: bs.cash (high)
- Unit: k£ ×1000
- Time: bop/beginning/first
- Formula: `=RC[-1]`

| Period | Value |
| --- | --- |
| 2023 (B) | 100 (100000) |
| 2024E (C) | 110 (110000) |
