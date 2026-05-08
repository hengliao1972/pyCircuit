#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
OUTPUT_NAME="PTO_Instruction_Set_Architecture_Manual.pdf"

cd "$SCRIPT_DIR"

if ! command -v tectonic >/dev/null 2>&1; then
  echo "error: tectonic is required to build the PTO manual" >&2
  exit 2
fi

mkdir -p "$BUILD_DIR"
rm -f "$BUILD_DIR/$OUTPUT_NAME"

python3 tools/generate_scalar_instruction_catalog.py
python3 tools/polish_tile_instruction_catalog.py
python3 figs/encoding/generate_pto_bundle_svg.py

tectonic --keep-logs --keep-intermediates --outdir "$BUILD_DIR" manual.tex

if command -v makeindex >/dev/null 2>&1; then
  makeindex -o "$BUILD_DIR/manual.ind" -t "$BUILD_DIR/manual.ilg" "$BUILD_DIR/manual.idx"
  tectonic --keep-logs --keep-intermediates --outdir "$BUILD_DIR" manual.tex
else
  echo "warning: makeindex not found; PDF will be built without a populated index" >&2
fi

mv "$BUILD_DIR/manual.pdf" "$BUILD_DIR/$OUTPUT_NAME"
echo "wrote $BUILD_DIR/$OUTPUT_NAME"
