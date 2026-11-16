# Nangate45 minimum integration test

The end-to-end smoke uses the repository's pyCircuit counter:

```bash
python3 -m pycircuit.cli build designs/examples/counter/counter.py \
  --out-dir eda/nangate45/build/counter \
  --target verilog \
  --param width=8 \
  --eda-flow nangate45
```

Acceptance checks:

1. `counter.mapped.v` exists and contains Nangate45 cell instances.
2. `stat.json` parses and reports a non-zero cell count.
3. No Yosys internal sequential cells remain in the mapped netlist.
