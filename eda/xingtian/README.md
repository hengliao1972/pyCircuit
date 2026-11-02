# XingTian backend

This backend consumes Verilog emitted after all pyCircuit MLIR legality gates
and runs remote XingTian logic synthesis.

## Local configuration

Copy `config.example.json` to the ignored `config.local.json` and fill paths
relative to this directory:

```json
{
  "host": "your-ssh-host-alias",
  "sdc": "constraints/default.sdc.tcl",
  "ssh_config": "credentials/ssh-config",
  "identity_file": "credentials/id_ed25519"
}
```

Then run from the repository root:

```bash
PYTHONPATH=$PWD/compiler/frontend \
PYC_TOOLCHAIN_ROOT=$PWD/.pycircuit_out/toolchain/install \
python3 -m pycircuit.cli build <design.py> \
  --out-dir eda/xingtian/build/<design> \
  --target verilog \
  --eda-flow xingtian
```

Explicit `--eda-config`, `--eda-sdc`, `--eda-ssh-config`, and
`--eda-identity-file` options override local defaults.

`scripts/run.sh` exposes the packaged low-level runner for standalone Verilog
projects. `constraints/default.sdc.tcl` is a generic 2 ns bring-up constraint
for designs with a `clk` port; it is not a production QoR constraint.
