#!/bin/sh
# Convert a real .xls inside the image and build context from it.
# Not part of `uv run pytest`. Requires Docker and the project venv (to write the seed .xlsx).
# Usage: scripts/docker-xls-smoke.sh [image]
set -eu

IMAGE="${1:-finance-context-builder}"
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
WORKDIR=$(mktemp -d)
# mktemp -d is mode 0700. uid 1000 must traverse and write this mount
# when the host user is someone else.
chmod 0777 "$WORKDIR"
cleanup() {
  if [ -d "${WORKDIR:-}" ]; then
    # Nested files belong to uid 1000. Remove them as that user, then drop the dir.
    docker run --rm -u 1000:1000 \
      -v "$WORKDIR:/work" \
      --entrypoint find \
      "$IMAGE" /work -mindepth 1 -delete >/dev/null 2>&1 || true
    rm -rf "$WORKDIR" || true
  fi
}
trap cleanup EXIT

cd "$ROOT"
uv run python - "$WORKDIR/book.xlsx" <<'PY'
import sys
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

build_xlsx(
    Path(sys.argv[1]),
    sheets=[
        SheetSpec(
            name="P&L",
            cells=[
                CellSpec(addr="A1", value="Item", type="s"),
                CellSpec(addr="B1", value="2023", type="s"),
                CellSpec(addr="C1", value="2024E", type="s"),
                CellSpec(addr="A2", value="Revenue", type="s"),
                CellSpec(addr="B2", value="100"),
                CellSpec(addr="C2", value="110"),
            ],
        )
    ],
    shared_strings=["Item", "2023", "2024E", "Revenue"],
)
PY

# LibreOffice in the image turns the seed .xlsx into a real .xls, as uid 1000.
docker run --rm \
  -u 1000:1000 \
  -e HOME=/app \
  -e LANG=C.UTF-8 \
  -v "$WORKDIR:/work" \
  --entrypoint soffice \
  "$IMAGE" \
  --headless --norestore --convert-to xls --outdir /work /work/book.xlsx

test -s "$WORKDIR/book.xls"

# The service converts that .xls back through the adapter and publishes context.
docker run --rm \
  -u 1000:1000 \
  -e HOME=/app \
  -e LANG=C.UTF-8 \
  -v "$WORKDIR:/work" \
  "$IMAGE" \
  finance-context build /work/book.xls -o /work/out

grep -q Revenue "$WORKDIR/out/context.json"
test -s "$WORKDIR/out/source.xlsx"
echo "docker xls smoke ok ($IMAGE)"
