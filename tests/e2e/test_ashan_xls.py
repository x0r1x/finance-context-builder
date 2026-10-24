"""The АШАН CF model as a legacy `.xls` against the same model saved as `.xlsx`.

Both books are private: they live only in a local `resources/corpus/` (gitignored)
and are never committed. Each test skips when a book or LibreOffice is missing.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from finance_context.adapters.soffice import SofficeConverter
from finance_context.app.pipeline import Pipeline
from finance_context.excel.legacy import LEGACY_SOURCE, SOURCE
from finance_context.models.context import BlockRow, ContextDocument
from finance_context.settings import Settings

_CORPUS = Path(__file__).resolve().parents[2] / "resources" / "corpus"
XLS = _CORPUS / "АШАН_модель CF.xls"
XLSX = _CORPUS / "АШАН_модель_CF.xlsx"
# The statements the CF model is built from. The Inputs* sheets hold scenario inputs.
STATEMENTS = ("ОПиУ", "Баланс", "Коэффициенты", "Денежные потоки", "Предпосылки")
SHEETS = [
    "Вход",
    "Фин отчетность РСБУ",
    "Фин отчетность МСФО",
    "Предпосылки_",
    "Предпосылки",
    "ОПиУ",
    "Баланс",
    "Коэффициенты",
    "Денежные потоки",
    "Inputs25012017БАЗ",
    "Inputs25012017КОН",
    "Inputs25012017ОПТ",
    "Outputs",
    "Формулы",
    "Портфель",
    "Показатели FLM",
    "Фин. показатели для мониторинга",
    "Для Стратегии 2017",
    "График погашения",
]


def _build(root: Path, workbook: Path, source: str) -> tuple[Path, ContextDocument]:
    if not workbook.is_file():
        pytest.skip(f"missing {workbook}; private book, copy it into resources/corpus")
    dest = root / workbook.suffix.lstrip(".")
    dest.mkdir()
    (dest / source).write_bytes(workbook.read_bytes())
    pipeline = Pipeline(Settings(data_dir=root / "data", _env_file=None))
    doc = pipeline.run(dest, job_id=dest.name, source_filename=workbook.name)
    return dest, doc


@pytest.fixture(scope="module")
def xls_book(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, ContextDocument]:
    if not SofficeConverter().available():
        pytest.skip("LibreOffice (soffice) is not installed")
    return _build(tmp_path_factory.mktemp("ashan-xls"), XLS, LEGACY_SOURCE)


@pytest.fixture(scope="module")
def xlsx_book(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, ContextDocument]:
    return _build(tmp_path_factory.mktemp("ashan-xlsx"), XLSX, SOURCE)


def _rows(doc: ContextDocument, sheets: tuple[str, ...] | None = None) -> dict[str, BlockRow]:
    return {
        row.row_key: row
        for block in doc.blocks
        for row in block.rows
        if sheets is None or row.sheet in sheets
    }


def _same(a: str | None, b: str | None) -> bool:
    if a == b:
        return True
    try:
        x, y = float(a), float(b)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    # The .xls cache is copied onto the converted book. A small tolerance covers
    # decimal serialisation of the same float.
    return math.isclose(x, y, rel_tol=1e-12, abs_tol=1e-9)


@pytest.mark.e2e_models
def test_ashan_xls_converts_and_builds(xls_book: tuple[Path, ContextDocument]) -> None:
    dest, doc = xls_book
    assert (dest / LEGACY_SOURCE).is_file()
    assert (dest / SOURCE).is_file()  # the LibreOffice copy every later stage reads
    assert (dest / "context" / "context.json").is_file()
    assert doc.meta.status == "succeeded"
    assert doc.meta.questions == []
    assert doc.workbook.sheets == SHEETS
    assert _rows(doc, STATEMENTS)


@pytest.mark.e2e_models
def test_ashan_xls_has_the_xlsx_structure(
    xls_book: tuple[Path, ContextDocument], xlsx_book: tuple[Path, ContextDocument]
) -> None:
    _, legacy = xls_book
    _, modern = xlsx_book
    assert legacy.workbook.sheets == modern.workbook.sheets
    old, new = _rows(legacy, STATEMENTS), _rows(modern, STATEMENTS)
    assert old.keys() == new.keys()
    for key, row in new.items():
        other = old[key]
        assert (other.label, other.kind, other.formula) == (row.label, row.kind, row.formula), key


@pytest.mark.e2e_models
def test_ashan_xls_values_match_xlsx(
    xls_book: tuple[Path, ContextDocument], xlsx_book: tuple[Path, ContextDocument]
) -> None:
    _, legacy = xls_book
    _, modern = xlsx_book
    old, new = _rows(legacy, STATEMENTS), _rows(modern, STATEMENTS)
    differ = sorted(
        key
        for key, row in new.items()
        if key in old
        and (
            len(row.values) != len(old[key].values)
            or not all(_same(a, b) for a, b in zip(row.values, old[key].values, strict=True))
        )
    )
    assert differ == [], f"{len(differ)} rows differ, e.g. {differ[:5]}"
