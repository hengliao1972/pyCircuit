#!/usr/bin/env bash
set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
pyc_find_pycc

PYTHONPATH_VAL="$(pyc_pythonpath)"
OUT_BASE="$(pyc_out_root)/sim"
DISCOVER="${PYC_ROOT_DIR}/flows/tools/discover_examples.py"
mkdir -p "${OUT_BASE}"

pyc_log "using pycc: ${PYCC}"

gate_run_id="${PYC_GATE_RUN_ID:-$(date +%Y%m%d-%H%M%S)}"
docs_gate_dir="${PYC_ROOT_DIR}/docs/gates/logs/${gate_run_id}"
case_log_root="${docs_gate_dir}/cases/run_sims"
mkdir -p "${case_log_root}"

case_timeout_sec="${PYC_SIM_CASE_TIMEOUT_SEC:-3600}"
retry_on_timeout="${PYC_SIM_RETRY_ON_TIMEOUT:-1}"
resume_from_case="${PYC_SIM_RESUME_FROM_CASE:-}"
resume_seen=0

if ! [[ "${case_timeout_sec}" =~ ^[0-9]+$ ]]; then
  pyc_die "PYC_SIM_CASE_TIMEOUT_SEC must be a non-negative integer, got: ${case_timeout_sec}"
fi
if ! [[ "${retry_on_timeout}" =~ ^[0-9]+$ ]]; then
  pyc_die "PYC_SIM_RETRY_ON_TIMEOUT must be a non-negative integer, got: ${retry_on_timeout}"
fi

cat > "${docs_gate_dir}/commands_run_sims.txt" <<EOF
bash flows/scripts/run_sims.sh
env:
  PYC_GATE_RUN_ID=${gate_run_id}
  PYC_SIM_CASE_TIMEOUT_SEC=${case_timeout_sec}
  PYC_SIM_RETRY_ON_TIMEOUT=${retry_on_timeout}
  PYC_SIM_RESUME_FROM_CASE=${resume_from_case}
EOF

is_timeout_rc() {
  local rc="${1:-0}"
  [[ "${rc}" -eq 124 || "${rc}" -eq 137 || "${rc}" -eq 142 ]]
}

should_run_case() {
  local case_name="$1"
  if [[ -z "${resume_from_case}" ]]; then
    return 0
  fi
  if [[ "${resume_seen}" -eq 1 ]]; then
    return 0
  fi
  if [[ "${case_name}" == "${resume_from_case}" ]]; then
    resume_seen=1
    return 0
  fi
  pyc_log "skip ${case_name} (resume from ${resume_from_case})"
  return 1
}

run_with_case_logs() {
  local case_name="$1"
  local stage="$2"
  local workdir="$3"
  shift 3
  local case_dir="${case_log_root}/${case_name}"
  mkdir -p "${case_dir}"

  local attempt=0
  local max_attempts=$((retry_on_timeout + 1))
  while true; do
    attempt=$((attempt + 1))
    local out_attempt="${case_dir}/${stage}.stdout.attempt${attempt}"
    local err_attempt="${case_dir}/${stage}.stderr.attempt${attempt}"
    local rc=0

    if [[ "${case_timeout_sec}" -gt 0 ]]; then
      if perl -e 'alarm shift; chdir shift or die "chdir failed\n"; exec @ARGV' \
        "${case_timeout_sec}" "${workdir}" "$@" >"${out_attempt}" 2>"${err_attempt}"; then
        rc=0
      else
        rc=$?
      fi
    else
      if (cd "${workdir}" && "$@" >"${out_attempt}" 2>"${err_attempt}"); then
        rc=0
      else
        rc=$?
      fi
    fi

    if [[ "${rc}" -eq 0 ]]; then
      cp -f "${out_attempt}" "${case_dir}/${stage}.stdout"
      cp -f "${err_attempt}" "${case_dir}/${stage}.stderr"
      printf '%s\n' "${rc}" > "${case_dir}/${stage}.rc"
      return 0
    fi

    if is_timeout_rc "${rc}" && [[ "${attempt}" -lt "${max_attempts}" ]]; then
      pyc_warn "timeout during ${case_name}/${stage} (attempt=${attempt}/${max_attempts}); retrying"
      continue
    fi

    cp -f "${out_attempt}" "${case_dir}/${stage}.stdout"
    cp -f "${err_attempt}" "${case_dir}/${stage}.stderr"
    printf '%s\n' "${rc}" > "${case_dir}/${stage}.rc"
    return "${rc}"
  done
}

run_case_examples() {
  local name="$1"
  local tb_src="$2"
  local trace_cfg="${3:-}"

  if ! should_run_case "${name}"; then
    return 0
  fi

  local out_dir="${OUT_BASE}/${name}"
  rm -rf "${out_dir}" >/dev/null 2>&1 || true
  mkdir -p "${out_dir}"
  pyc_log "sim(example) ${name}: ${tb_src}"

  local build_args=(
    --out-dir "${out_dir}"
    --target both
    --jobs "${PYC_SIM_JOBS:-4}"
    --logic-depth "${PYC_SIM_LOGIC_DEPTH:-256}"
    --run-verilator
  )
  if [[ -n "${trace_cfg}" ]]; then
    build_args+=(--trace-config "${trace_cfg}")
  fi

  if ! run_with_case_logs "${name}" build "${PYC_ROOT_DIR}" \
      env PYTHONPATH="${PYTHONPATH_VAL}" PYTHONDONTWRITEBYTECODE=1 PYCC="${PYCC}" \
      python3 -m pycircuit.cli build "${tb_src}" "${build_args[@]}"; then
    pyc_die "build failed: ${name} (see ${case_log_root}/${name}/build.stderr)"
  fi

  local cpp_bin
  cpp_bin="$(
    python3 - "${out_dir}/project_manifest.json" <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    manifest = json.load(f)
print(manifest.get("cpp_executable", ""))
PY
  )"
  if [[ -z "${cpp_bin}" || ! -x "${cpp_bin}" ]]; then
    pyc_die "missing or non-executable cpp_executable for ${name}: ${cpp_bin}"
  fi
  pyc_log "run(cpp) ${name}: ${cpp_bin}"
  if ! run_with_case_logs "${name}" run_cpp "${out_dir}" "${cpp_bin}"; then
    pyc_die "cpp execution failed: ${name} (see ${case_log_root}/${name}/run_cpp.stderr)"
  fi

  if [[ -n "${trace_cfg}" ]]; then
    local top
    top="$(
      python3 - "${out_dir}/project_manifest.json" <<'PY'
import json
import sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    m = json.load(f)
print(m.get("top", ""))
PY
    )"
    if [[ -z "${top}" ]]; then
      pyc_die "trace gate: missing top module name in project_manifest.json: ${out_dir}"
    fi
    local tr="${out_dir}/tb_${top}/tb_${top}.pyctrace"
    if [[ ! -f "${tr}" ]]; then
      pyc_die "trace gate: missing pyctrace output file: ${tr}"
    fi

    if ! python3 - "${tr}" >"${case_log_root}/${name}/trace_header.stdout" 2>"${case_log_root}/${name}/trace_header.stderr" <<'PY'
import struct
import sys
from pathlib import Path

p = Path(sys.argv[1]).resolve()
data = p.read_bytes()
if len(data) < 16:
    raise SystemExit(f"pyctrace too small: {p} ({len(data)} bytes)")
if data[:8] != b"PYC4TRC3":
    raise SystemExit(f"pyctrace bad magic: {p} got={data[:8]!r} exp=b'PYC4TRC3'")
schema_version = int(struct.unpack_from("<I", data, 8)[0])
if schema_version != 3:
    raise SystemExit(f"pyctrace bad schema_version: {p} got={schema_version} exp=3")
flags = int(struct.unpack_from("<I", data, 12)[0])
if (flags & 0x1) == 0:
    raise SystemExit(f"pyctrace bad flags: expected little_endian bit set, got flags=0x{flags:x}")
if (flags & (1 << 1)) == 0:
    raise SystemExit(f"pyctrace bad flags: expected external_manifest bit set, got flags=0x{flags:x}")
print("ok: pyctrace binary trace emitted (schema v3, external-manifest mode)")
PY
    then
      pyc_die "trace gate: invalid pyctrace header: ${tr}"
    fi

    if ! python3 "${PYC_ROOT_DIR}/flows/tools/dump_pyctrace.py" "${tr}" \
        --manifest "${out_dir}/probe_manifest.json" \
        --max-cycles 1 --max-events 1 --no-header \
        >"${case_log_root}/${name}/trace_decode.stdout" \
        2>"${case_log_root}/${name}/trace_decode.stderr"; then
      pyc_die "trace gate: failed to decode pyctrace stream: ${tr}"
    fi

    if [[ "${name}" == "example_trace_dsl_smoke" ]]; then
      if ! python3 "${PYC_ROOT_DIR}/flows/tools/dump_pyctrace.py" "${tr}" --manifest "${out_dir}/probe_manifest.json" --max-cycles 10 --max-events 50 --no-header | grep -Fq "(commit)"; then
        pyc_die "trace gate: expected commit-phase value changes for ${name}: ${tr}"
      fi
      if ! python3 "${PYC_ROOT_DIR}/flows/tools/dump_pyctrace.py" "${tr}" --manifest "${out_dir}/probe_manifest.json" --max-cycles 10 --max-events 50 --no-header | grep -Fq "(tick) dut.u0:probe.pv.q"; then
        pyc_die "trace gate: expected tick-phase value changes for pv probe in ${name}: ${tr}"
      fi
      if ! python3 "${PYC_ROOT_DIR}/flows/tools/dump_pyctrace.py" "${tr}" --manifest "${out_dir}/probe_manifest.json" --max-cycles 10 --max-events 200 --no-header | grep -Eq "^cycle 3: 0 value-change events, [1-9][0-9]* write events$"; then
        pyc_die "trace gate: expected Write events at cycle 3 even without ValueChange for ${name}: ${tr}"
      fi
      if ! python3 "${PYC_ROOT_DIR}/flows/tools/dump_pyctrace.py" "${tr}" --manifest "${out_dir}/probe_manifest.json" --max-cycles 10 --max-events 200 --no-header | grep -Fq "(tick) WRITE[reg] dut.u0:out_y"; then
        pyc_die "trace gate: expected tick-phase WRITE[reg] for dut.u0:out_y in ${name}: ${tr}"
      fi
    fi

    if [[ "${name}" == "example_xz_value_model_smoke" ]]; then
      if ! python3 "${PYC_ROOT_DIR}/flows/tools/dump_pyctrace.py" "${tr}" --manifest "${out_dir}/probe_manifest.json" --max-cycles 10 --max-events 100 --no-header | grep -Eq "known=0x[0-9a-f]+ z=0x[0-9a-f]+"; then
        pyc_die "trace gate: expected known/z masks in value-change events for ${name}: ${tr}"
      fi
    fi

    if [[ "${name}" == "example_reset_invalidate_order_smoke" ]]; then
      if ! python3 - "${tr}" <<'PY'
import struct
import sys
from pathlib import Path

p = Path(sys.argv[1]).resolve()
data = p.read_bytes()
if len(data) < 16:
    raise SystemExit(1)
if data[:8] != b"PYC4TRC3":
    raise SystemExit(1)
off = 16
events = []
while off + 8 <= len(data):
    chunk_len, chunk_ty = struct.unpack_from("<II", data, off)
    off += 8
    payload = memoryview(data)[off : off + chunk_len]
    off += chunk_len
    if chunk_ty == 9:
        cyc = int(struct.unpack_from("<Q", payload, 0)[0])
        events.append(("invalidate", cyc))
    elif chunk_ty == 8:
        cyc = int(struct.unpack_from("<Q", payload, 0)[0])
        edge = int(payload[10])
        events.append(("reset_assert" if edge == 1 else "reset_deassert", cyc))

def first(kind):
    for idx, ev in enumerate(events):
        if ev[0] == kind:
            return idx, ev[1]
    return None

inv = first("invalidate")
ast = first("reset_assert")
dea = first("reset_deassert")
if inv is None or ast is None or dea is None:
    raise SystemExit(1)
if not (inv[0] < ast[0] < dea[0]):
    raise SystemExit(1)
if not (inv[1] <= ast[1] <= dea[1]):
    raise SystemExit(1)
raise SystemExit(0)
PY
      then
        pyc_die "trace gate: expected INVALIDATE -> RESET_ASSERT -> RESET_DEASSERT ordering for ${name}: ${tr}"
      fi
    fi
  fi
}

while IFS=$'\t' read -r name _design tb _cfg _tier; do
  [[ -n "${name}" ]] || continue
  trace_cfg=""
  if [[ "${name}" == "bundle_probe_expand" || "${name}" == "trace_dsl_smoke" || "${name}" == "xz_value_model_smoke" || "${name}" == "reset_invalidate_order_smoke" ]]; then
    trace_cfg="${PYC_ROOT_DIR}/designs/examples/${name}/${name}_trace.json"
    [[ -f "${trace_cfg}" ]] || trace_cfg=""
  fi
  run_case_examples "example_${name}" "${tb}" "${trace_cfg}"
done < <(python3 "${DISCOVER}" --root "${PYC_ROOT_DIR}/designs/examples" --tier normal --format tsv)

run_case_nonexample() {
  local name="$1"
  local src="$2"
  if ! should_run_case "${name}"; then
    return 0
  fi
  local out_dir="${OUT_BASE}/${name}"
  rm -rf "${out_dir}" >/dev/null 2>&1 || true
  mkdir -p "${out_dir}"
  pyc_log "sim(non-example) ${name}: ${src}"
  if ! run_with_case_logs "${name}" build "${PYC_ROOT_DIR}" \
      env PYTHONPATH="${PYTHONPATH_VAL}" PYTHONDONTWRITEBYTECODE=1 PYCC="${PYCC}" \
      python3 -m pycircuit.cli build \
      "${src}" \
      --out-dir "${out_dir}" \
      --target verilator \
      --jobs "${PYC_SIM_JOBS:-4}" \
      --logic-depth "${PYC_SIM_LOGIC_DEPTH:-256}" \
      --run-verilator; then
    pyc_die "build failed: ${name} (see ${case_log_root}/${name}/build.stderr)"
  fi
}

run_case_nonexample issq "${PYC_ROOT_DIR}/designs/IssueQueue/tb_issq.py"
run_case_nonexample regfile "${PYC_ROOT_DIR}/designs/RegisterFile/tb_regfile.py"
run_case_nonexample bypass_unit "${PYC_ROOT_DIR}/designs/BypassUnit/tb_bypass_unit.py"

if [[ -n "${resume_from_case}" && "${resume_seen}" -eq 0 ]]; then
  pyc_die "resume case not found: ${resume_from_case}"
fi

# RTL primitive gate: a byte_mem with a narrow address must survive synthesis.
# Out-of-range part selects on a narrow address (e.g. `raddr[31:0]` when
# ADDR_WIDTH < 32) leave the address undefined, which lets synthesis remove the
# whole storage while simulation still passes; only synthesis can catch it.
if command -v yosys >/dev/null 2>&1; then
  bm_dir="$(pyc_out_root)/rtl-primitives/byte_mem_synth"
  rm -rf "${bm_dir}" >/dev/null 2>&1 || true
  mkdir -p "${bm_dir}"
  cat > "${bm_dir}/top.v" <<'V'
module top (
  input             clk,
  input  [6:0]      raddr,
  output [31:0]     rdata,
  input             wvalid,
  input  [6:0]      waddr,
  input  [31:0]     wdata,
  input  [3:0]      wstrb
);
  pyc_byte_mem #(.ADDR_WIDTH(7), .DATA_WIDTH(32), .DEPTH(128)) u_mem (
    .clk(clk),
    .raddr(raddr),
    .rdata(rdata),
    .wvalid(wvalid),
    .waddr(waddr),
    .wdata(wdata),
    .wstrb(wstrb)
  );
endmodule
V
  pyc_log "rtl(primitive) pyc_byte_mem: ADDR_WIDTH=7 synthesis gate"
  if ! yosys -p "read_verilog ${bm_dir}/top.v ${PYC_ROOT_DIR}/runtime/verilog/pyc_byte_mem.v; \
      synth_xilinx -family xcup -nodsp -flatten -top top; stat" >"${bm_dir}/yosys.log" 2>&1; then
    pyc_die "pyc_byte_mem synthesis gate failed to run (see ${bm_dir}/yosys.log)"
  fi
  lcs="$(sed -n 's/^ *Estimated number of LCs: *\([0-9][0-9]*\).*/\1/p' "${bm_dir}/yosys.log" | tail -1)"
  if [[ -z "${lcs}" || "${lcs}" -le 0 ]]; then
    pyc_die "pyc_byte_mem storage optimized away at ADDR_WIDTH=7 (LCs=${lcs:-missing}; see ${bm_dir}/yosys.log)"
  fi
  pyc_log "rtl(primitive) pyc_byte_mem: ${lcs} LCs retained"
else
  pyc_warn "yosys not found; skipping pyc_byte_mem synthesis gate"
fi

cat > "${docs_gate_dir}/run_sims_summary.json" <<EOF
{
  "run_id": "${gate_run_id}",
  "script": "run_sims.sh",
  "status": "pass",
  "case_timeout_sec": ${case_timeout_sec},
  "retry_on_timeout": ${retry_on_timeout}
}
EOF

pyc_log "all sims passed"
