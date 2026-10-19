#!/bin/sh
# Convert a real .xls inside the image and build context from it.
# Not part of `uv run pytest`. Requires Docker and the project venv (to write the seed .xlsx).
# Usage: scripts/docker-xls-smoke.sh [image]
set -eu

IMAGE="${1:-finance-context-builder}"
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
WORKDIR=$(mktemp -d)
# mktemp -d is mode 0700. Run the containers as this user so they can write
# the mount without making it world-writable, and so this process can delete it.
HOST_USER=$(id -u):$(id -g)
cleanup() {
  if [ -d "${WORKDIR:-}" ]; then
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

# LibreOffice in the image turns the seed .xlsx into a real .xls.
# HOME=/tmp is writable for whatever uid the host passes in; /app is not.
docker run --rm \
  --user "$HOST_USER" \
  -e HOME=/tmp \
  -e LANG=C.UTF-8 \
  -v "$WORKDIR:/work" \
  --entrypoint soffice \
  "$IMAGE" \
  --headless --norestore --convert-to xls --outdir /work /work/book.xlsx

test -s "$WORKDIR/book.xls"

# The service converts that .xls back through the adapter and publishes context.
docker run --rm \
  --user "$HOST_USER" \
  -e HOME=/tmp \
  -e LANG=C.UTF-8 \
  -v "$WORKDIR:/work" \
  "$IMAGE" \
  finance-context build /work/book.xls -o /work/out

grep -q Revenue "$WORKDIR/out/context.json"
test -s "$WORKDIR/out/source.xlsx"
echo "docker xls smoke ok ($IMAGE)"
