#!/usr/bin/env bash
# Run a local Verilog project through a remote XingTian synthesis installation.
# Credentials and vendor tools are intentionally supplied by the local machine.
set -euo pipefail

HOST=""
LIB_VARIANT="6t"
SDC_STYLE="native"
DESIGN_DIR=""
TOP=""
FILELIST_REL=""
SDC_REL=""
RESULTS_DIR=""
SSH_CONFIG=""
IDENTITY_FILE=""
POLL_SEC="${PYC_XINGTIAN_POLL_SEC:-15}"

usage() {
  cat <<'EOF'
Usage:
  xt_remote_syn.sh --design DIR --top NAME --sdc FILE [options]

Options:
  --filelist FILE       File list relative to the design directory (default: filelist.f)
  --results-dir DIR     Local timestamped result root (required)
  --lib-variant NAME    asap7, 6t, or 7p5t (default: 6t)
  --sdc-style STYLE     native or dc (default: native)
  --host NAME           SSH host or alias (required)
  --ssh-config FILE     Optional OpenSSH config file
  --identity-file FILE  Optional private-key path
  --help                Show this help
EOF
}

die() {
  echo "error: $1" >&2
  exit "${2:-1}"
}

log() {
  echo "[xingtian] $*"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --design) DESIGN_DIR="${2:-}"; shift 2 ;;
    --top) TOP="${2:-}"; shift 2 ;;
    --filelist) FILELIST_REL="${2:-}"; shift 2 ;;
    --sdc) SDC_REL="${2:-}"; shift 2 ;;
    --results-dir) RESULTS_DIR="${2:-}"; shift 2 ;;
    --lib-variant) LIB_VARIANT="${2:-}"; shift 2 ;;
    --sdc-style) SDC_STYLE="${2:-}"; shift 2 ;;
    --host) HOST="${2:-}"; shift 2 ;;
    --ssh-config) SSH_CONFIG="${2:-}"; shift 2 ;;
    --identity-file) IDENTITY_FILE="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1" 2 ;;
  esac
done

command -v ssh >/dev/null 2>&1 || die "ssh is required" 2
command -v rsync >/dev/null 2>&1 || die "rsync is required" 2
[[ -n "$DESIGN_DIR" && -d "$DESIGN_DIR" ]] || die "--design must name a directory" 2
[[ -n "$RESULTS_DIR" ]] || die "--results-dir is required" 2
[[ -n "$HOST" ]] || die "--host is required" 2

DESIGN_DIR="$(cd "$DESIGN_DIR" && pwd)"
TOP="${TOP:-$(basename "$DESIGN_DIR")}"
FILELIST_REL="${FILELIST_REL:-filelist.f}"
SDC_REL="${SDC_REL:-constraints.sdc.tcl}"
[[ "$TOP" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || die "invalid Verilog top name: $TOP" 4
case "$LIB_VARIANT" in asap7|6t|7p5t) ;; *) die "invalid library variant: $LIB_VARIANT" 2 ;; esac
case "$SDC_STYLE" in native|dc) ;; *) die "invalid SDC style: $SDC_STYLE" 2 ;; esac

abs_under_design() {
  local spec="$1"
  local absolute parent
  if [[ "$spec" == /* ]]; then
    absolute="$spec"
  else
    absolute="$DESIGN_DIR/$spec"
  fi
  [[ -e "$absolute" ]] || die "input file does not exist: $absolute" 4
  parent="$(cd "$(dirname "$absolute")" && pwd)"
  case "$parent" in
    "$DESIGN_DIR"|"$DESIGN_DIR"/*) ;;
    *) die "input file is outside --design: $absolute" 4 ;;
  esac
  printf '%s\n' "${absolute#"$DESIGN_DIR"/}"
}

FILELIST_REL="$(abs_under_design "$FILELIST_REL")"
SDC_REL="$(abs_under_design "$SDC_REL")"
FILELIST_ABS="$DESIGN_DIR/$FILELIST_REL"
FILELIST_DIR="$(cd "$(dirname "$FILELIST_ABS")" && pwd)"

while IFS= read -r raw || [[ -n "$raw" ]]; do
  line="${raw%%#*}"
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" ]] && continue
  if [[ "$line" == +incdir+* ]]; then
    path="${line#+incdir+}"
    [[ "$path" != /* && -d "$FILELIST_DIR/$path" ]] || die "invalid +incdir+: $path" 4
    continue
  fi
  [[ "$line" == +* ]] && continue
  [[ "$line" != /* && -f "$FILELIST_DIR/$line" ]] || die "invalid filelist entry: $line" 4
done < "$FILELIST_ABS"

SSH_OPTS=(-o BatchMode=yes -o ConnectTimeout=10)
if [[ -n "$SSH_CONFIG" ]]; then
  [[ -f "$SSH_CONFIG" ]] || die "SSH config does not exist: $SSH_CONFIG" 2
  SSH_OPTS+=(-F "$SSH_CONFIG")
fi
if [[ -n "$IDENTITY_FILE" ]]; then
  [[ -f "$IDENTITY_FILE" ]] || die "identity file does not exist: $IDENTITY_FILE" 2
  SSH_OPTS+=(-i "$IDENTITY_FILE" -o IdentitiesOnly=yes)
fi
ssh_h() { ssh "${SSH_OPTS[@]}" "$HOST" "$@"; }
if ! ssh_h true >/dev/null 2>&1; then
  die "cannot connect to SSH host: $HOST" 3
fi

printf -v RSYNC_SSH '%q ' ssh "${SSH_OPTS[@]}"
rsync_to() {
  rsync -az --delete \
    --exclude '.git/' \
    --exclude '*.vcd' --exclude '*.fsdb' --exclude '*.fst' --exclude '*.log' \
    -e "$RSYNC_SSH" "$@"
}
rsync_from() {
  rsync -az -e "$RSYNC_SSH" "$@"
}

REMOTE_HOME="$(ssh_h 'printf %s "$HOME"')"
REMOTE_ROOT="${REMOTE_HOME}/eda_work/${TOP}"
REMOTE_SRC="${REMOTE_ROOT}/src"
REMOTE_FLOW="${REMOTE_ROOT}/flow"

RESULTS_DIR="$(mkdir -p "$RESULTS_DIR" && cd "$RESULTS_DIR" && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOCAL_RESULT="${RESULTS_DIR}/${STAMP}"
LOCAL_LATEST="${RESULTS_DIR}/latest"

LOCAL_SSH_PID=""
INTERRUPTED=0
on_interrupt() {
  INTERRUPTED=1
  echo
  log "interrupt received; stopping remote synthesis"
  ssh_h "if [[ -f '${REMOTE_FLOW}/log/xt_run.pid' ]]; then
    rpid=\$(cat '${REMOTE_FLOW}/log/xt_run.pid')
    kill \"\$rpid\" 2>/dev/null || true
    pkill -P \"\$rpid\" 2>/dev/null || true
  fi" || true
  [[ -n "$LOCAL_SSH_PID" ]] && kill "$LOCAL_SSH_PID" 2>/dev/null || true
}
trap on_interrupt INT TERM

log "top=$TOP filelist=$FILELIST_REL sdc=$SDC_REL lib=$LIB_VARIANT"
ssh_h "mkdir -p '${REMOTE_SRC}' '${REMOTE_FLOW}'"
rsync_to "${DESIGN_DIR}/" "${HOST}:${REMOTE_SRC}/" || die "upload failed" 5

ssh_h bash -s <<EOF || die "remote flow preparation failed" 6
set -euo pipefail
flow='${REMOTE_FLOW}'
src_flow=''
if [[ -d \$HOME/eda_demo/xt/scr ]]; then
  src_flow="\$HOME/eda_demo/xt"
elif [[ -d /data/eda/flow/xt/scr ]]; then
  src_flow='/data/eda/flow/xt'
else
  echo "remote XingTian flow not found" >&2
  exit 1
fi
mkdir -p "\$flow"
rsync -a --delete \
  --exclude output --exclude rpt --exclude log --exclude config.tcl \
  "\$src_flow/" "\$flow/"
mkdir -p "\$flow/output" "\$flow/rpt" "\$flow/log" "\$flow/run"
if [[ -f \$flow/scr/syn_main.tcl ]]; then
  sed -i '/rpt_clocks/s/^/# /' "\$flow/scr/syn_main.tcl"
fi
cat > "\$flow/config.tcl" <<'CFG'
set XT_LIB_VARIANT    "${LIB_VARIANT}"
set DESIGN_NAME       "${TOP}"
set DESIGN_VERSION    "v1"
set DESIGN_SDC        "${REMOTE_SRC}/${SDC_REL}"
set DESIGN_FILE_LIST  "${REMOTE_SRC}/${FILELIST_REL}"
set DESIGN_SDC_STYLE  "${SDC_STYLE}"
CFG
EOF

ssh_h bash -s <<EOF &
set -euo pipefail
source /data/eda/tool.csh
export LD_LIBRARY_PATH=/opt/gcc-10.3/lib64:\${LD_LIBRARY_PATH:-}
cd '${REMOTE_FLOW}/run'
mkdir -p ../log ../output ../rpt
rm -rf ../output/*.mapped.v ../output/*.mapped.ddc ../rpt/* ../log/xt_run.log || true
xt_shell -f ../scr/syn_main.tcl > ../log/xt_run.log 2>&1 &
echo \$! > ../log/xt_run.pid
set +e
wait \$!
echo \$? > ../log/xt_run.exit
exit 0
EOF
LOCAL_SSH_PID=$!

while kill -0 "$LOCAL_SSH_PID" 2>/dev/null; do
  sleep "$POLL_SEC"
  if kill -0 "$LOCAL_SSH_PID" 2>/dev/null; then
    ssh_h "tail -n 8 '${REMOTE_FLOW}/log/xt_run.log' 2>/dev/null || true"
  fi
done

set +e
wait "$LOCAL_SSH_PID"
SSH_RC=$?
set -e
LOCAL_SSH_PID=""
trap - INT TERM

if [[ "$INTERRUPTED" -eq 1 ]]; then
  mkdir -p "$LOCAL_RESULT/log"
  rsync_from "${HOST}:${REMOTE_FLOW}/log/" "${LOCAL_RESULT}/log/" || true
  die "synthesis interrupted; partial logs saved in $LOCAL_RESULT/log" 6
fi
[[ "$SSH_RC" -eq 0 ]] || log "remote SSH exited with $SSH_RC; retrieving available evidence"

mkdir -p "$LOCAL_RESULT/output" "$LOCAL_RESULT/rpt" "$LOCAL_RESULT/log"
rsync_from "${HOST}:${REMOTE_FLOW}/output/" "${LOCAL_RESULT}/output/" || die "output download failed" 7
rsync_from "${HOST}:${REMOTE_FLOW}/rpt/" "${LOCAL_RESULT}/rpt/" || die "report download failed" 7
rsync_from "${HOST}:${REMOTE_FLOW}/log/" "${LOCAL_RESULT}/log/" || die "log download failed" 7
rsync_from "${HOST}:${REMOTE_FLOW}/config.tcl" "${LOCAL_RESULT}/config.tcl" || true
ln -sfn "$STAMP" "$LOCAL_LATEST"

MAPPED="${LOCAL_RESULT}/output/${TOP}.mapped.v"
REMOTE_EXIT="$(ssh_h "cat '${REMOTE_FLOW}/log/xt_run.exit' 2>/dev/null || echo missing")"
SUCCESS=0
if [[ -f "$LOCAL_RESULT/log/xt_run.log" ]] &&
   grep -q '\[SynthesisTools Info\] : SUCCESS' "$LOCAL_RESULT/log/xt_run.log"; then
  SUCCESS=1
fi

log "result=$LOCAL_RESULT"
[[ -f "$MAPPED" ]] || die "mapped netlist missing; see $LOCAL_RESULT/log/xt_run.log" 6
if [[ "$SUCCESS" -eq 1 && "$REMOTE_EXIT" == "0" ]]; then
  log "synthesis succeeded"
  exit 0
fi
die "netlist was returned but synthesis did not report SUCCESS (remote exit=$REMOTE_EXIT)" 6
