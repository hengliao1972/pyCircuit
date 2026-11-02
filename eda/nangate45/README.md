# Nangate45 synthesis backend

This backend maps verified pyCircuit Verilog to the research-only Nangate45
standard-cell library with local Yosys and ABC.

Nangate45 is purposely non-manufacturable. Results are suitable for flow
bring-up and research exploration, not tapeout or commercial PPA claims.

## Setup

```bash
./eda/nangate45/scripts/setup.sh
cp eda/nangate45/config.example.json eda/nangate45/config.local.json
```

The setup script downloads a pinned Liberty and its LICENSE from the official
OpenROAD-flow-scripts platform into the ignored `vendor/` directory and checks
SHA-256.

## Run

```bash
PYTHONPATH=$PWD/compiler/frontend \
PYC_TOOLCHAIN_ROOT=$PWD/.pycircuit_out/toolchain/install \
python3 -m pycircuit.cli build designs/examples/counter/counter.py \
  --out-dir eda/nangate45/build/counter \
  --target verilog \
  --param width=8 \
  --eda-flow nangate45
```

Outputs are under `<out-dir>/eda/nangate45/`:

- `output/<top>.mapped.v`
- `rpt/stat.rpt`
- `rpt/stat.json`
- `log/yosys.log`
- `result.json`

This first integration is synthesis-only. OpenROAD placement/routing and GDS
generation are intentionally outside its scope.
