"""Put streamed rows back on a document for tests.

``build_context`` leaves ``blocks[].rows`` empty. Tests that assert on lines
read ``context.json`` onto the same document, so axis serializers stay as they
were when the file was written. The pipeline does not.
"""

from __future__ import annotations

import json

from finance_context.context.build import _CONTEXT_DIRS, build_context
from finance_context.models.context import BLOCK_ROW_ADAPTER, ContextDocument


def attach_streamed_rows(doc: ContextDocument) -> ContextDocument:
    directory = _CONTEXT_DIRS[id(doc)]
    payload = json.loads((directory / "context.json").read_text(encoding="utf-8"))
    for block, raw in zip(doc.blocks, payload["blocks"], strict=True):
        block.rows = [BLOCK_ROW_ADAPTER.validate_python(item) for item in raw["rows"]]
    return doc


def published_context(**kwargs) -> ContextDocument:
    return attach_streamed_rows(build_context(**kwargs))
