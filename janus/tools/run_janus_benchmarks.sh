#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/../.." && pwd)"

BENCH_DIR="${ROOT_DIR}/janus/generated/benchmarks"
mkdir -p "${BENCH_DIR}"

CORE_SRC="${ROOT_DIR}/janus/programs_src/coremark_lite.S"
DHRY_SRC="${ROOT_DIR}/janus/programs_src/dhrystone_lite.S"
CORE_MEMH="${BENCH_DIR}/coremark_lite.memh"
DHRY_MEMH="${BENCH_DIR}/dhrystone_lite.memh"

CORE_OPS=4096
DHRY_ITERS=4096

MAX_CYCLES="${PYC_MAX_CYCLES:-20000000}"

bash "${ROOT_DIR}/janus/tools/build_linx_memh.sh" "${CORE_SRC}" "${CORE_MEMH}" >/dev/null
bash "${ROOT_DIR}/janus/tools/build_linx_memh.sh" "${DHRY_SRC}" "${DHRY_MEMH}" >/dev/null

run_cpp() {
  local name="$1"
  shift
  local memh="$1"
  shift || true
  local trace_dir="${BENCH_DIR}/trace_cpp_${name}"
  mkdir -p "${trace_dir}"
  PYC_TRACE_DIR="${trace_dir}" PYC_MAX_CYCLES="${MAX_CYCLES}" PYC_KONATA=1 \
    bash "${ROOT_DIR}/janus/tools/run_janus_bcc_ooo_pyc_cpp.sh" "${memh}" 2>&1
}

run_vlt() {
  local name="$1"
  shift
  local memh="$1"
  shift || true
  local konata_path="${BENCH_DIR}/trace_sv_${name}.konata"
  bash "${ROOT_DIR}/janus/tools/run_janus_bcc_ooo_pyc_verilator.sh" \
    +notrace +nolog \
    +konata="${konata_path}" \
    +max_cycles="${MAX_CYCLES}" \
    +memh="${memh}" 2>&1
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

CORE_CPP_LOG="${BENCH_DIR}/coremark_cpp.log"
DHRY_CPP_LOG="${BENCH_DIR}/dhrystone_cpp.log"
CORE_VLT_LOG="${BENCH_DIR}/coremark_verilog.log"
DHRY_VLT_LOG="${BENCH_DIR}/dhrystone_verilog.log"

run_cpp "coremark" "${CORE_MEMH}" | tee "${CORE_CPP_LOG}" >/dev/null
run_cpp "dhrystone" "${DHRY_MEMH}" | tee "${DHRY_CPP_LOG}" >/dev/null
run_vlt "coremark" "${CORE_MEMH}" | tee "${CORE_VLT_LOG}" >/dev/null
run_vlt "dhrystone" "${DHRY_MEMH}" | tee "${DHRY_VLT_LOG}" >/dev/null

CORE_CPP_CYCLES="$(extract_cycles < "${CORE_CPP_LOG}")"
DHRY_CPP_CYCLES="$(extract_cycles < "${DHRY_CPP_LOG}")"
CORE_VLT_CYCLES="$(extract_cycles < "${CORE_VLT_LOG}")"
DHRY_VLT_CYCLES="$(extract_cycles < "${DHRY_VLT_LOG}")"

python3 - <<PY > "${BENCH_DIR}/janus_bcc_report.md"
core_ops = ${CORE_OPS}
dhry_iters = ${DHRY_ITERS}
core_cpp_cycles = ${CORE_CPP_CYCLES}
dhry_cpp_cycles = ${DHRY_CPP_CYCLES}
core_vlt_cycles = ${CORE_VLT_CYCLES}
dhry_vlt_cycles = ${DHRY_VLT_CYCLES}

def coremark_per_mhz(ops, cycles):
    return ops / cycles

def dmips(iters, cycles, freq_hz=1_000_000):
    dhrystones_per_sec = (iters * freq_hz) / cycles
    return dhrystones_per_sec / 1757.0

print("# Janus BCC Benchmark Report")
print("")
print("Workloads:")
print("- CoreMark-lite proxy ops: {}".format(core_ops))
print("- Dhrystone-lite proxy iterations: {}".format(dhry_iters))
print("- DMIPS assumes 1 MHz cycle clock for normalization.")
print("")
print("| Mode | Core Cycles | CoreMark/MHz (proxy) | Dhrystone Cycles | DMIPS (proxy) |")
print("| --- | ---: | ---: | ---: | ---: |")
print("| C++ model | {} | {:.6f} | {} | {:.6f} |".format(
    core_cpp_cycles,
    coremark_per_mhz(core_ops, core_cpp_cycles),
    dhry_cpp_cycles,
    dmips(dhry_iters, dhry_cpp_cycles),
))
print("| Verilog model (Verilator) | {} | {:.6f} | {} | {:.6f} |".format(
    core_vlt_cycles,
    coremark_per_mhz(core_ops, core_vlt_cycles),
    dhry_vlt_cycles,
    dmips(dhry_iters, dhry_vlt_cycles),
))
PY

cat "${BENCH_DIR}/janus_bcc_report.md"
