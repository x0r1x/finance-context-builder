"""Put streamed rows back on a document for tests.

``build_context`` leaves ``blocks[].rows`` empty. Tests that assert on lines
read ``context.json`` onto the same document, so axis serializers stay as they
were when the file was written. The pipeline does not.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from finance_context.context.build import _CONTEXT_DIRS, build_context
from finance_context.models.context import BLOCK_ROW_ADAPTER, ContextDocument


def attach_streamed_rows(doc: ContextDocument) -> ContextDocument:
    directory = _CONTEXT_DIRS[id(doc)]
    payload = json.loads((directory / "context.json").read_text(encoding="utf-8"))
    for block, raw in zip(doc.blocks, payload["blocks"], strict=True):
        block.rows = [BLOCK_ROW_ADAPTER.validate_python(item) for item in raw["rows"]]
    return doc


def published_context(**kwargs) -> ContextDocument:
    """Build and reattach rows.

    A caller that omits ``context_dir`` gets a directory that is removed
    before this returns. The pipeline passes its own directory and keeps it.
    """
    if kwargs.get("context_dir") is not None:
        return attach_streamed_rows(build_context(**kwargs))
    with tempfile.TemporaryDirectory(prefix="fc-context-") as directory:
        kwargs["context_dir"] = Path(directory)
        doc = attach_streamed_rows(build_context(**kwargs))
    _CONTEXT_DIRS.pop(id(doc), None)
    return doc
