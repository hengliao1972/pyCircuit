# Pipeline

pyCircuit uses a two-stage compile pipeline:

1. Frontend (Python): source scan + JIT elaboration + `.pyc` emission
2. Backend (`pycc`): MLIR passes + emit C++ and/or Verilog

## Frontend

Frontend responsibilities:
- strict API contract scan (entry file + local imports)
- JIT elaboration of `@module` / `@function` / `@const`
- materialize `@module(value_params=...)` as runtime boundary input ports
- emit one `.pyc` per specialized module
- emit a deterministic `project_manifest.json`
- emit a testbench `.pyc` payload from `@testbench`

All emitted modules are stamped with:
- `pyc.frontend.contract = "pycircuit"`

## Backend (`pycc`)

Backend responsibilities:
- verify required frontend contract attrs (`pyc-check-frontend-contract`)
- verify value-param metadata arity/alignment (`pyc.value_params` + `pyc.value_param_types`)
- inline helper functions and run cleanup/verification passes
- preserve `@module` hierarchy boundaries in strict mode (default: `--hierarchy-policy=strict`)
- emit:
  - C++ model (`--emit=cpp`)
  - Verilog netlist (`--emit=verilog`)
  - testbench text (for `.pyc` files containing `pyc.tb.payload`)

Default backend hierarchy policy:
- `--hierarchy-policy=strict`
- `--inline-policy=off` for hierarchy-preserving module builds
- strict mode fails compilation if frontend module symbol set changes after lowering passes

### Per-pass IR dump (diagnostics)

Write the MLIR IR before and/or after every pass to a directory so the effect
of any single pass is directly diffable. Diagnostics only; disabled by
default (zero overhead when not requested). Works on both `pycc` and
`pyc-opt` (pass an explicit dump directory on `pyc-opt`).

```bash
pycc foo.pyc --emit=none --dump-pass-ir=/tmp/pir
diff /tmp/pir/*_before_*eliminate-wires*.mlir /tmp/pir/*_after_*eliminate-wires*.mlir
```

Related flags: `--dump-pass-ir-phase=before|after|both`,
`--dump-pass-ir-filter=<regex>`, `--dump-pass-ir-max-lines=<N>`, and
`--dump-pass-ir=auto` on **`pycc` only** (resolves to `<--out-dir>/pass_ir`).
Coexists with `--profile-pass-timing` / `--profile-json`.

See [mlir_pass_ir_dump.md](mlir_pass_ir_dump.md) for full details.

## CLI entrypoints

Emit a single `.pyc`:

```bash
python3 -m pycircuit.cli emit <design.py> -o out.pyc
```

Build a project (multi-module + testbench):

```bash
python3 -m pycircuit.cli build <tb_or_top.py> --out-dir <dir> --target cpp|verilator|both --jobs <N>
```

Emit device Verilog only (no `@testbench` required):

```bash
python3 -m pycircuit.cli build <top.py> --out-dir <dir> --target verilog --jobs <N>
```

Simulation (Verilator):

```bash
python3 -m pycircuit.cli build <tb.py> --out-dir <dir> --target verilator --run-verilator
```

## Optional post-Verilog EDA flow

XingTian remote synthesis is an explicit, default-off consumer of pycc output.
It runs only after the normal MLIR legality gates and Verilog emission succeed;
it does not change PYC or backend semantics.

```bash
python3 -m pycircuit.cli build <top.py> \
  --out-dir <dir> \
  --target verilog \
  --eda-flow xingtian
```

The default machine-local configuration is
`$PWD/eda/xingtian/config.local.json`:

```json
{
  "host": "your-ssh-host-alias",
  "sdc": "constraints/default.sdc.tcl",
  "ssh_config": "credentials/ssh-config",
  "identity_file": "credentials/id_ed25519"
}
```

Relative paths resolve from the JSON file's directory. Use `--eda-config` or
`PYC_XINGTIAN_CONFIG` to select another file. Explicit `--eda-sdc`,
`--eda-ssh-config`, and `--eda-identity-file` options override config fields.

The flow stages a relative-path `filelist.f`, generated RTL, one deduplicated
`pyc_primitives.v`, and the supplied SDC under
`<dir>/eda/xingtian/input/`. Timestamped mapped netlists, QoR, metrics, and logs
are stored under `<dir>/eda/xingtian/results/` and referenced by
`project_manifest.json`.

Credentials are local-only. If the config omits SSH fields, the runner uses the
normal OpenSSH configuration/agent. Credentials are never copied into staging
or recorded in the manifest. The runner requires Linux, bash, OpenSSH, rsync,
and access to a remote XingTian installation.

### Nangate45 synthesis

The `nangate45` adapter performs local synthesis and technology mapping with
Yosys/ABC. Install the pinned research Liberty once:

```bash
./eda/nangate45/scripts/setup.sh
cp eda/nangate45/config.example.json eda/nangate45/config.local.json
```

Then use `--eda-flow nangate45`. Outputs are normalized under
`<dir>/eda/nangate45/`: mapped Verilog, `stat.rpt`, `stat.json`, Yosys log, and
`result.json`. The first integration is synthesis-only; it does not run
OpenROAD placement/routing or produce GDS. Nangate45 is purposely
non-manufacturable.

### Adding another backend

Public backend assets use `eda/<backend>/`: README, sanitized config template,
scripts, tests, and examples are tracked, while `config.local.json`,
`credentials/`, `vendor/`, `results/`, `work/`, and `build/` remain ignored.
See `eda/README.md`.

Executable backends use the typed registry in `pycircuit.eda`. An adapter
implements the `EdaFlow` protocol and is registered with `register_eda_flow()`;
the canonical CLI dispatch path then stages verified Verilog and records the
normalized `EdaResult`. Backend directories are not arbitrary executable
plugins, so local JSON cannot inject commands into the build.
