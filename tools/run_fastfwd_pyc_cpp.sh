#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=../scripts/lib.sh
source "${ROOT_DIR}/scripts/lib.sh"
pyc_find_pyc_compile

SEED="${SEED:-1}"
CYCLES="${CYCLES:-20000}"
PACKETS="${PACKETS:-60000}"
PARAMS=()
STATS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --seed)
      SEED="${2:?missing value for --seed}"
      shift 2
      ;;
    --cycles)
      CYCLES="${2:?missing value for --cycles}"
      shift 2
      ;;
    --packets)
      PACKETS="${2:?missing value for --packets}"
      shift 2
      ;;
    --param)
      PARAMS+=("${2:?missing value for --param}")
      shift 2
      ;;
    --stats)
      STATS=1
      shift 1
      ;;
    -h|--help)
      cat <<EOF
Usage:
  $0 [--seed N] [--cycles N] [--packets N] [--param name=value]... [--stats]

Env vars:
  SEED, CYCLES, PACKETS

Tracing:
  PYC_TRACE=1        write a text log
  PYC_VCD=1          write a VCD
  PYC_KONATA=1       write a Konata trace log
  PYC_TRACE_DIR=...  output directory (default: examples/generated/fastfwd_pyc)

Stats:
  --stats            also emit Verilog and print basic size proxies (regs/fifos/wires/assigns)
EOF
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

WORK_DIR="$(mktemp -d -t fastfwd_pyc.XXXXXX)"
trap 'rm -rf "${WORK_DIR}"' EXIT

cd "${ROOT_DIR}"

EMIT_ARGS=()
N_FE=""
ENG_PER_LANE=1
if (( ${#PARAMS[@]} )); then
  for p in "${PARAMS[@]}"; do
    if [[ "${p}" == N_FE=* ]]; then
      raw="${p#N_FE=}"
      if [[ "${raw}" =~ ^[0-9]+$ ]]; then
        N_FE="${raw}"
      else
        echo "error: --param N_FE expects an integer, got: ${raw}" >&2
        exit 2
      fi
    fi
    if [[ "${p}" == ENG_PER_LANE=* ]]; then
      raw="${p#ENG_PER_LANE=}"
      if [[ "${raw}" =~ ^[0-9]+$ ]]; then
        ENG_PER_LANE="${raw}"
      else
        echo "error: --param ENG_PER_LANE expects an integer, got: ${raw}" >&2
        exit 2
      fi
    fi
    EMIT_ARGS+=(--param "${p}")
  done
fi

emit_cmd=(python3 -m pycircuit.cli emit examples/fastfwd_pyc/fastfwd_pyc.py)
if (( ${#EMIT_ARGS[@]} )); then
  emit_cmd+=("${EMIT_ARGS[@]}")
fi
emit_cmd+=(-o "${WORK_DIR}/fastfwd_pyc.pyc")

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$(pyc_pythonpath)" "${emit_cmd[@]}"

"${PYC_COMPILE}" "${WORK_DIR}/fastfwd_pyc.pyc" --emit=cpp -o "${WORK_DIR}/fastfwd_pyc_gen.hpp"
if (( STATS )); then
  "${PYC_COMPILE}" "${WORK_DIR}/fastfwd_pyc.pyc" --emit=verilog -o "${WORK_DIR}/fastfwd_pyc.v"
fi

FASTFWD_TOTAL_ENG="$(( 4 * ENG_PER_LANE ))"
if [[ -n "${N_FE}" ]]; then
  FASTFWD_TOTAL_ENG="${N_FE}"
fi

"${CXX:-clang++}" -std=c++17 -O2 -DFASTFWD_TOTAL_ENG="${FASTFWD_TOTAL_ENG}" \
  -I "${ROOT_DIR}/include" \
  -I "${WORK_DIR}" \
  -o "${WORK_DIR}/tb_fastfwd_pyc" \
  "${ROOT_DIR}/examples/fastfwd_pyc/tb_fastfwd_pyc.cpp"

tb_out="$("${WORK_DIR}/tb_fastfwd_pyc" --seed "${SEED}" --cycles "${CYCLES}" --packets "${PACKETS}")"
echo "${tb_out}"

if (( STATS )); then
  sent="$(echo "${tb_out}" | sed -n 's/.*sent=\([0-9][0-9]*\).*/\1/p')"
  got="$(echo "${tb_out}" | sed -n 's/.*got=\([0-9][0-9]*\).*/\1/p')"
  thr="$(echo "${tb_out}" | sed -n 's/.*throughput=\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p')"
  bkpr="$(echo "${tb_out}" | sed -n 's/.*bkpr=\([0-9][0-9]*\.[0-9][0-9]*\)%.*/\1/p')"

  stats="$(
    python3 - "${WORK_DIR}/fastfwd_pyc.v" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
txt = path.read_text(encoding="utf-8", errors="ignore").splitlines()

reg_bits = 0
fifo_bits = 0
wire_decl = 0
assigns = 0

re_reg = re.compile(r"\bpyc_reg\s*#\(\.WIDTH\((\d+)\)\)")
re_fifo = re.compile(r"\bpyc_fifo\s*#\(\.WIDTH\((\d+)\),\s*\.DEPTH\((\d+)\)\)")

for line in txt:
    s = line.lstrip()
    if s.startswith("wire "):
        wire_decl += 1
    if s.startswith("assign "):
        assigns += 1
    m = re_reg.search(line)
    if m:
        reg_bits += int(m.group(1))
        continue
    m = re_fifo.search(line)
    if m:
        w = int(m.group(1))
        d = int(m.group(2))
        fifo_bits += w * d
        continue

storage_bits = reg_bits + fifo_bits
size_bytes = path.stat().st_size

print(
    f"reg_bits={reg_bits} fifo_bits={fifo_bits} storage_bits={storage_bits} "
    f"wire_decl={wire_decl} assigns={assigns} v_bytes={size_bytes}"
)
PY
  )"

  echo "metrics: eng_per_lane=${ENG_PER_LANE} total_eng=${FASTFWD_TOTAL_ENG} sent=${sent:-0} got=${got:-0} thr=${thr:-0} bkpr=${bkpr:-0} ${stats}"
fi
