from __future__ import annotations

import pytest
from tests.helpers.fast_sheet import fast_cells


@pytest.fixture
def sheet() -> list[dict]:
    return fast_cells()
