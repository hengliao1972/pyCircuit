#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SRC="${1:-$ROOT/../qemu/tests/linxisa/mcopy_mset_basic.s}"

LLVM_BUILD="${LLVM_BUILD:-$HOME/llvm-project/build-linxisa-clang}"
LLVM_MC="${LLVM_MC:-$LLVM_BUILD/bin/llvm-mc}"

QEMU_BIN="${QEMU_BIN:-/Users/zhoubot/qemu/build/qemu-system-linx64}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/linx-diff.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

OBJ="$WORK/test.o"
QEMU_TRACE="$WORK/qemu.jsonl"
PYC_TRACE="$WORK/pyc.jsonl"

if [[ ! -x "$LLVM_MC" ]]; then
  echo "error: llvm-mc not found: $LLVM_MC" >&2
  exit 2
fi
if [[ ! -x "$QEMU_BIN" ]]; then
  echo "error: qemu-system-linx64 not found: $QEMU_BIN" >&2
  exit 2
fi
if [[ ! -f "$SRC" ]]; then
  echo "error: missing source: $SRC" >&2
  exit 2
fi

echo "[llvm-mc] $SRC"
"$LLVM_MC" -triple=linx64 -filetype=obj "$SRC" -o "$OBJ"

echo "[qemu] commit trace: $QEMU_TRACE"
LINX_COMMIT_TRACE="$QEMU_TRACE" "$QEMU_BIN" -nographic -monitor none -machine virt -kernel "$OBJ" >/dev/null

echo "[pyc] commit trace: $PYC_TRACE"
PYC_KONATA=0 PYC_EXPECT_EXIT=0 PYC_BOOT_PC=0x10000 PYC_COMMIT_TRACE="$PYC_TRACE" \
  bash "$ROOT/tools/run_linx_cpu_pyc_cpp.sh" --elf "$OBJ" >/dev/null

echo "[diff]"
python3 "$ROOT/tools/linx_trace_diff.py" "$QEMU_TRACE" "$PYC_TRACE" --ignore cycle
