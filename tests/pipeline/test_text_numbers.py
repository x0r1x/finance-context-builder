"""Text that Python accepts as a float must not abort the workbook.

``inf``, ``nan`` and ``1e400`` pass ``float`` and then fail on ``int``.
Grouped text such as ``1 230`` is a number only after the spaces are removed.
"""

from __future__ import annotations

from pathlib import Path

import xlsxwriter
from tests.helpers.published import attach_streamed_rows

from finance_context.app.pipeline import Pipeline
from finance_context.settings import Settings


def test_text_numbers_do_not_abort_the_build(tmp_path: Path) -> None:
    source = tmp_path / "job" / "source" / "source.xlsx"
    source.parent.mkdir(parents=True)
    book = xlsxwriter.Workbook(str(source))
    sheet = book.add_worksheet("CF")
    headers = ["Показатель", "2023", "2024", "inf"]
    for col, text in enumerate(headers):
        sheet.write_string(0, col, text)
    sheet.write_string(1, 0, "Выручка")
    sheet.write_number(1, 1, 100)
    sheet.write_number(1, 2, 110)
    sheet.write_string(1, 3, "1 230")
    sheet.write_string(2, 0, "Год")
    for col, text in enumerate(("1", "2", "nan", "1_000", "1\xa0230,5"), start=1):
        sheet.write_string(2, col, text)
    sheet.write_string(5, 0, "Параметр")
    sheet.write_string(5, 1, "Значение")
    sheet.write_string(6, 0, "Ставка")
    sheet.write_string(6, 1, "1 230,5")
    sheet.write_string(7, 0, "Мусор")
    sheet.write_string(7, 1, "inf")
    book.close()

    document = attach_streamed_rows(
        Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None)).run(
            tmp_path / "job",
            job_id="job",
            source_filename="book.xlsx",
        )
    )
    assert document.meta.status == "succeeded"
    assert document.blocks
    rate = next(row for block in document.blocks for row in block.rows if row.label == "Ставка")
    assert rate.normalized_values == ["1230.5", None]
    assert rate.numeric_summary is not None
    assert rate.numeric_summary.n == 1
    assert rate.numeric_summary.minimum == "1230.5"
    assert rate.numeric_summary.maximum == "1230.5"
    assert rate.numeric_summary.first == "1 230,5"
