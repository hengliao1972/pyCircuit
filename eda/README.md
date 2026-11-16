# EDA backend workspace

Each optional post-Verilog backend owns one directory:

```text
eda/<backend>/
  README.md
  config.example.json
  config.local.json       # ignored
  scripts/                # tracked wrappers/helpers without credentials
  tests/                  # tracked self-owned smoke designs
  examples/               # tracked examples with clear licensing
  credentials/            # ignored
  vendor/                 # ignored vendor tools/docs/PDKs
  results/                # ignored synthesis output
  work/                   # ignored staging
  build/                  # ignored local pyCircuit builds
```

Backend directories are documentation and local-workspace roots, not executable
plugins by themselves. A backend must also provide a typed adapter registered in
`pycircuit.eda`; this keeps configuration validation and result parsing explicit.

Never commit private keys, real SSH configuration, commercial tool/PDK files, or
generated netlists and reports. Use `config.example.json` for a sanitized schema
and keep real paths in `config.local.json`.

Current adapters:

- `xingtian`: remote commercial synthesis integration.
- `nangate45`: local open Yosys/ABC mapping to the non-manufacturable research
  Nangate45 library.
