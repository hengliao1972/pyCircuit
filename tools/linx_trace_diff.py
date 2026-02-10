#!/usr/bin/env python3
import argparse
import json
import sys
from dataclasses import dataclass


MANDATORY_FIELDS = [
    "pc",
    "insn",
    "wb_valid",
    "wb_rd",
    "wb_data",
    "mem_valid",
    "mem_addr",
    "mem_wdata",
    "mem_rdata",
    "mem_size",
    "trap_valid",
    "trap_cause",
    "next_pc",
]


@dataclass(frozen=True)
class TraceRec:
    raw: dict

    def get(self, k: str):
        return self.raw.get(k, None)


def load_jsonl(path: str) -> list[TraceRec]:
    out: list[TraceRec] = []
    with open(path, "r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise SystemExit(f"error: {path}:{ln}: invalid JSON: {e}") from e
            if not isinstance(obj, dict):
                raise SystemExit(f"error: {path}:{ln}: expected JSON object per line")
            out.append(TraceRec(obj))
    return out


def fmt_hex(v):
    if isinstance(v, int):
        return hex(v)
    return repr(v)


def first_mismatch(a: list[TraceRec], b: list[TraceRec], *, ignore_fields: set[str]) -> tuple[int, str] | None:
    n = min(len(a), len(b))
    for i in range(n):
        ra = a[i].raw
        rb = b[i].raw
        # Always compare core sequencing fields.
        for k in ["pc", "insn", "next_pc"]:
            if k in ignore_fields:
                continue
            if ra.get(k, None) != rb.get(k, None):
                return i, k

        # WB fields: rd/data are don't-care when wb_valid==0.
        for k in ["wb_valid"]:
            if k in ignore_fields:
                continue
            if ra.get(k, None) != rb.get(k, None):
                return i, k
        if ra.get("wb_valid", 0) and rb.get("wb_valid", 0):
            for k in ["wb_rd", "wb_data"]:
                if k in ignore_fields:
                    continue
                if ra.get(k, None) != rb.get(k, None):
                    return i, k

        # Mem fields: addr/data/size are don't-care when mem_valid==0.
        for k in ["mem_valid"]:
            if k in ignore_fields:
                continue
            if ra.get(k, None) != rb.get(k, None):
                return i, k
        if ra.get("mem_valid", 0) and rb.get("mem_valid", 0):
            for k in ["mem_addr", "mem_wdata", "mem_rdata", "mem_size"]:
                if k in ignore_fields:
                    continue
                if ra.get(k, None) != rb.get(k, None):
                    return i, k

        # Trap fields: cause is don't-care when trap_valid==0.
        for k in ["trap_valid"]:
            if k in ignore_fields:
                continue
            if ra.get(k, None) != rb.get(k, None):
                return i, k
        if ra.get("trap_valid", 0) and rb.get("trap_valid", 0):
            k = "trap_cause"
            if k not in ignore_fields and ra.get(k, None) != rb.get(k, None):
                return i, k
    if len(a) != len(b):
        return n, "<length>"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Diff LinxISA JSONL commit traces (QEMU vs pyCircuit bring-up).")
    ap.add_argument("ref_jsonl", help="Reference JSONL (typically QEMU)")
    ap.add_argument("dut_jsonl", help="DUT JSONL (typically pyCircuit)")
    ap.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Ignore a field (repeatable). Example: --ignore mem_rdata",
    )
    args = ap.parse_args()

    ignore_fields = set(args.ignore)

    ref = load_jsonl(args.ref_jsonl)
    dut = load_jsonl(args.dut_jsonl)

    mm = first_mismatch(ref, dut, ignore_fields=ignore_fields)
    if mm is None:
        print(f"ok: traces match ({len(ref)} commits)")
        return 0

    idx, field = mm
    if field == "<length>":
        print(f"mismatch: length differs: ref={len(ref)} dut={len(dut)} (first extra at idx={idx})")
        return 1

    ra = ref[idx].raw if idx < len(ref) else {}
    rb = dut[idx].raw if idx < len(dut) else {}
    print(f"mismatch: idx={idx} field={field}")
    print(f"  ref.{field}={fmt_hex(ra.get(field, None))}")
    print(f"  dut.{field}={fmt_hex(rb.get(field, None))}")
    # Print a short record summary to speed triage.
    for k in ["pc", "insn", "wb_valid", "wb_rd", "wb_data", "mem_valid", "mem_addr", "trap_valid", "trap_cause", "next_pc"]:
        if k in ignore_fields:
            continue
        print(f"  ref.{k}={fmt_hex(ra.get(k, None))}  dut.{k}={fmt_hex(rb.get(k, None))}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
