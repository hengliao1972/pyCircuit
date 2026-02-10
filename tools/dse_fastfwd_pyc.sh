#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

SEED="${SEED:-1}"
CYCLES="${CYCLES:-20000}"
PACKETS="${PACKETS:-60000}"
MAX_RUNS="${MAX_RUNS:-60}"
AREA_PER_REG_BIT="${AREA_PER_REG_BIT:-1.0}"
AREA_PER_FIFO_BIT="${AREA_PER_FIFO_BIT:-1.0}"
FE_AREA_PER_ENG="${FE_AREA_PER_ENG:-3500.0}"

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
    --max-runs)
      MAX_RUNS="${2:?missing value for --max-runs}"
      shift 2
      ;;
    -h|--help)
      cat <<EOF
Usage:
  $0 [--seed N] [--cycles N] [--packets N] [--max-runs N]

This runs a design-space exploration sweep for the FastFwd example by varying
JIT parameters and collecting both performance and simple area proxies.

Note:
  - This is a *proxy* DSE: true PPA requires synthesis.
  - FE area is modeled as FE_AREA_PER_ENG * total_eng (default: 3500).
  - Storage is modeled as reg_bits + fifo_bits, scaled by AREA_PER_*_BIT.

Env vars:
  MAX_RUNS              limit number of sampled configs (default: 60)
  FE_AREA_PER_ENG       default: 3500.0
  AREA_PER_REG_BIT      default: 1.0
  AREA_PER_FIFO_BIT     default: 1.0
EOF
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

cd "${ROOT_DIR}"

# Candidate sets (tune as needed).
ENG_PER_LANE_LIST=(1 2 4 8)
LANE_Q_DEPTH_LIST=(8 16 32 64)
ENG_Q_DEPTH_LIST=(4 8 16)
ROB_DEPTH_LIST=(8 16 32)
STASH_WIN_LIST=(0 2 4 6 8)
BKPR_SLACK_LIST=(1 2 4)

cfg_all="$(mktemp -t fastfwd_dse.cfg.all.XXXXXX)"
cfg_sel="$(mktemp -t fastfwd_dse.cfg.sel.XXXXXX)"
results="$(mktemp -t fastfwd_dse.results.XXXXXX)"
trap 'rm -f "${cfg_all}" "${cfg_sel}" "${results}"' EXIT

for ENG_PER_LANE in "${ENG_PER_LANE_LIST[@]}"; do
  for LANE_Q_DEPTH in "${LANE_Q_DEPTH_LIST[@]}"; do
    for ENG_Q_DEPTH in "${ENG_Q_DEPTH_LIST[@]}"; do
      for ROB_DEPTH in "${ROB_DEPTH_LIST[@]}"; do
        for STASH_WIN in "${STASH_WIN_LIST[@]}"; do
          for BKPR_SLACK in "${BKPR_SLACK_LIST[@]}"; do
            echo "ENG_PER_LANE=${ENG_PER_LANE} LANE_Q_DEPTH=${LANE_Q_DEPTH} ENG_Q_DEPTH=${ENG_Q_DEPTH} ROB_DEPTH=${ROB_DEPTH} STASH_WIN=${STASH_WIN} BKPR_SLACK=${BKPR_SLACK}" >>"${cfg_all}"
          done
        done
      done
    done
  done
done

# Sample configs deterministically (seeded by SEED).
python3 - "${cfg_all}" "${cfg_sel}" "${MAX_RUNS}" "${SEED}" <<'PY'
import random
import sys
from pathlib import Path

all_path = Path(sys.argv[1])
sel_path = Path(sys.argv[2])
max_runs = int(sys.argv[3])
seed = int(sys.argv[4])

cfgs = [l.strip() for l in all_path.read_text(encoding="utf-8").splitlines() if l.strip()]
rng = random.Random(seed)
rng.shuffle(cfgs)

if max_runs > 0:
    cfgs = cfgs[:max_runs]

sel_path.write_text("\n".join(cfgs) + ("\n" if cfgs else ""), encoding="utf-8")
PY

printf "%-9s  %5s  %6s  %6s  %8s  %10s  %10s  %10s  %10s  %10s  %s\n" \
  "score" "eng" "thr" "bkpr" "vKB" "reg_bits" "fifo_bits" "storage" "wire" "assigns" "config"
printf "%-9s  %5s  %6s  %6s  %8s  %10s  %10s  %10s  %10s  %10s  %s\n" \
  "---------" "-----" "------" "------" "--------" "----------" "----------" "----------" "----------" "----------" "------"

while IFS= read -r cfg; do
  [[ -z "${cfg}" ]] && continue

  params=()
  for kv in ${cfg}; do
    params+=(--param "${kv}")
  done

  set +e
  out="$("${ROOT_DIR}/tools/run_fastfwd_pyc_cpp.sh" --seed "${SEED}" --cycles "${CYCLES}" --packets "${PACKETS}" --stats "${params[@]}" 2>&1)"
  rc=$?
  set -e
  if (( rc != 0 )); then
    printf "%-9s  %5s  %6s  %6s  %8s  %10s  %10s  %10s  %10s  %10s  %s\n" \
      "FAIL" "?" "?" "?" "?" "?" "?" "?" "?" "?" "${cfg}"
    continue
  fi
  metrics="$(echo "${out}" | sed -n 's/^metrics: //p')"
  if [[ -z "${metrics}" ]]; then
    echo "error: missing metrics line for cfg: ${cfg}" >&2
    echo "${out}" >&2
    exit 2
  fi

  # Parse key=value tokens (bash 3 compatible: no associative arrays).
  thr=0
  bkpr=0
  total_eng=0
  reg_bits=0
  fifo_bits=0
  storage_bits=0
  wire_decl=0
  assigns=0
  v_bytes=0
  for tok in ${metrics}; do
    case "${tok}" in
      thr=*) thr="${tok#thr=}" ;;
      bkpr=*) bkpr="${tok#bkpr=}" ;;
      total_eng=*) total_eng="${tok#total_eng=}" ;;
      reg_bits=*) reg_bits="${tok#reg_bits=}" ;;
      fifo_bits=*) fifo_bits="${tok#fifo_bits=}" ;;
      storage_bits=*) storage_bits="${tok#storage_bits=}" ;;
      wire_decl=*) wire_decl="${tok#wire_decl=}" ;;
      assigns=*) assigns="${tok#assigns=}" ;;
      v_bytes=*) v_bytes="${tok#v_bytes=}" ;;
    esac
  done

  # Area proxy: FE area + storage bits (scaled).
  score="$(awk \
    -v thr="${thr}" \
    -v eng="${total_eng}" \
    -v fe_area="${FE_AREA_PER_ENG}" \
    -v reg_bits="${reg_bits}" \
    -v fifo_bits="${fifo_bits}" \
    -v a_reg="${AREA_PER_REG_BIT}" \
    -v a_fifo="${AREA_PER_FIFO_BIT}" \
    'BEGIN { area = eng*fe_area + reg_bits*a_reg + fifo_bits*a_fifo; if (area <= 0) area = 1; printf "%.6f", (thr/area)*1e6 }' \
  )"
  v_kb="$(awk -v b="${v_bytes}" 'BEGIN { printf "%.1f", b/1024.0 }')"

  printf "%-9s  %5s  %6s  %6s  %8s  %10s  %10s  %10s  %10s  %10s  %s\n" \
    "${score}" "${total_eng}" "${thr}" "${bkpr}" "${v_kb}" "${reg_bits}" "${fifo_bits}" "${storage_bits}" "${wire_decl}" "${assigns}" "${cfg}"

  # Machine-sortable record (TSV) for final ranking.
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "${score}" "${thr}" "${bkpr}" "${total_eng}" "${v_kb}" "${reg_bits}" "${fifo_bits}" "${storage_bits}" "${wire_decl}" "${assigns}" "${cfg}" >>"${results}"
done <"${cfg_sel}"

echo
echo "Top 10 by score (score = throughput/area_proxy, scaled by 1e6):"
sort -t$'\t' -nr -k1,1 "${results}" | head -n 10 | awk -F'\t' '{printf "  score=%s thr=%s eng=%s storage=%s wire=%s assigns=%s cfg=%s\n", $1,$2,$4,$8,$9,$10,$11}'
