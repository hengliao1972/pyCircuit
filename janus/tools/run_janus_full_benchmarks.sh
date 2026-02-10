#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

BENCH_DIR="${ROOT_DIR}/janus/generated/benchmarks_full"
LOG_DIR="${BENCH_DIR}/logs"
mkdir -p "${LOG_DIR}"

CORE_ITERATIONS="${CORE_ITERATIONS:-10}"
DHRY_RUNS="${DHRY_RUNS:-1000}"

# Keep within the default 1 MiB Janus bring-up RAM (addresses are byte-indexed).
BOOT_SP="${PYC_BOOT_SP:-0x00000000000ff000}"
BOOT_SP_HEX="${BOOT_SP#0x}"

# Conservative caps; override with PYC_MAX_CYCLES / +max_cycles when needed.
MAX_CYCLES="${PYC_MAX_CYCLES:-50000000}"

build_out="$(
  CORE_ITERATIONS="${CORE_ITERATIONS}" \
  DHRY_RUNS="${DHRY_RUNS}" \
  bash "${ROOT_DIR}/janus/tools/build_linxisa_benchmarks_memh.sh"
)"
CORE_MEMH="$(printf "%s\n" "${build_out}" | sed -n '1p')"
DHRY_MEMH="$(printf "%s\n" "${build_out}" | sed -n '2p')"

run_cpp() {
  local name="$1"
  local memh="$2"
  shift 2
  echo "[janus-cpp] ${name}"
  PYC_MAX_CYCLES="${MAX_CYCLES}" \
  PYC_BOOT_SP="${BOOT_SP}" \
  bash "${ROOT_DIR}/janus/tools/run_janus_bcc_ooo_pyc_cpp.sh" "${memh}" "$@"
}

run_vlt() {
  local name="$1"
  local memh="$2"
  shift 2
  echo "[janus-vlt] ${name}"
  bash "${ROOT_DIR}/janus/tools/run_janus_bcc_ooo_pyc_verilator.sh" \
    +notrace +nolog +zeromem \
    +max_cycles="${MAX_CYCLES}" \
    +boot_sp="${BOOT_SP_HEX}" \
    +memh="${memh}" "$@"
}

extract_cycles() {
  awk '
    match($0, /cycles=[0-9]+/) {
      s = substr($0, RSTART, RLENGTH);
      gsub("cycles=", "", s);
      c = s;
    }
    END {
      if (c == "") exit 1;
      print c;
    }
  '
}

CORE_CPP_LOG="${LOG_DIR}/coremark_cpp.log"
DHRY_CPP_LOG="${LOG_DIR}/dhrystone_cpp.log"
CORE_VLT_LOG="${LOG_DIR}/coremark_verilog.log"
DHRY_VLT_LOG="${LOG_DIR}/dhrystone_verilog.log"

run_cpp "coremark" "${CORE_MEMH}" 2>&1 | tee "${CORE_CPP_LOG}" >/dev/null
run_cpp "dhrystone" "${DHRY_MEMH}" 2>&1 | tee "${DHRY_CPP_LOG}" >/dev/null
run_vlt "coremark" "${CORE_MEMH}" 2>&1 | tee "${CORE_VLT_LOG}" >/dev/null
run_vlt "dhrystone" "${DHRY_MEMH}" 2>&1 | tee "${DHRY_VLT_LOG}" >/dev/null

grep -q "Correct operation validated." "${CORE_CPP_LOG}"
grep -q "Correct operation validated." "${DHRY_CPP_LOG}"
grep -q "Correct operation validated." "${CORE_VLT_LOG}"
grep -q "Correct operation validated." "${DHRY_VLT_LOG}"

CORE_CPP_CYCLES="$(extract_cycles < "${CORE_CPP_LOG}")"
DHRY_CPP_CYCLES="$(extract_cycles < "${DHRY_CPP_LOG}")"
CORE_VLT_CYCLES="$(extract_cycles < "${CORE_VLT_LOG}")"
DHRY_VLT_CYCLES="$(extract_cycles < "${DHRY_VLT_LOG}")"

python3 - <<PY > "${BENCH_DIR}/janus_bcc_full_report.md"
core_iterations = ${CORE_ITERATIONS}
dhry_runs = ${DHRY_RUNS}
boot_sp = "${BOOT_SP}"
max_cycles = ${MAX_CYCLES}
core_cpp_cycles = ${CORE_CPP_CYCLES}
dhry_cpp_cycles = ${DHRY_CPP_CYCLES}
core_vlt_cycles = ${CORE_VLT_CYCLES}
dhry_vlt_cycles = ${DHRY_VLT_CYCLES}

def coremark_per_mhz(iters, cycles):
    return iters / cycles

def dmips_per_mhz(runs, cycles):
    # DMIPS/MHz = (dhrystones_per_second / 1757) / MHz.
    # With a 1 MHz normalization, this is runs * 1e6 / (cycles * 1757).
    return (runs * 1_000_000.0) / (cycles * 1757.0)

print("# Janus BCC Full Benchmark Report")
print("")
print("Inputs:")
print(f"- CoreMark iterations: {core_iterations}")
print(f"- Dhrystone runs: {dhry_runs}")
print(f"- Boot SP: {boot_sp}")
print(f"- Max cycles: {max_cycles}")
print("")
print("| Mode | Core Cycles | CoreMark/MHz | Dhrystone Cycles | DMIPS/MHz |")
print("| --- | ---: | ---: | ---: | ---: |")
print("| C++ model | {} | {:.9f} | {} | {:.9f} |".format(
    core_cpp_cycles,
    coremark_per_mhz(core_iterations, core_cpp_cycles),
    dhry_cpp_cycles,
    dmips_per_mhz(dhry_runs, dhry_cpp_cycles),
))
print("| Verilog model (Verilator) | {} | {:.9f} | {} | {:.9f} |".format(
    core_vlt_cycles,
    coremark_per_mhz(core_iterations, core_vlt_cycles),
    dhry_vlt_cycles,
    dmips_per_mhz(dhry_runs, dhry_vlt_cycles),
))
PY

cat "${BENCH_DIR}/janus_bcc_full_report.md"
