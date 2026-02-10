#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    import serial  # type: ignore
except Exception as e:  # pragma: no cover
    serial = None
    _serial_import_error = e


@dataclass(frozen=True)
class TestCase:
    name: str
    memh: Path
    boot_pc: int | None
    boot_sp: int | None
    expected_exit: int


HALT_RE = re.compile(r"HALT exit=0x([0-9a-fA-F]{8}) cycles=([0-9]+)")


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def parse_int(x: str) -> int:
    return int(x, 0)


def load_manifest(path: Path) -> tuple[int, int, list[TestCase]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    boot_pc_default = parse_int(data.get("boot_pc", "0x10000"))
    boot_sp_default = parse_int(data.get("boot_sp", "0xff000"))
    tests: list[TestCase] = []
    for t in data["tests"]:
        tests.append(
            TestCase(
                name=t["name"],
                memh=Path(t["memh"]),
                boot_pc=parse_int(t["boot_pc"]) if "boot_pc" in t else None,
                boot_sp=parse_int(t["boot_sp"]) if "boot_sp" in t else None,
                expected_exit=int(t.get("expected_exit", 0)),
            )
        )
    return boot_pc_default, boot_sp_default, tests


def read_memh_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


class Monitor:
    def __init__(self, port: str, baud: int, timeout_s: float) -> None:
        if serial is None:
            _die(f"missing pyserial ({_serial_import_error}); install with: pip install -r tools/zybo/requirements.txt")
        self.ser = serial.Serial(port=port, baudrate=baud, timeout=timeout_s)

    def close(self) -> None:
        self.ser.close()

    def write_line(self, line: str) -> None:
        if not line.endswith("\n"):
            line += "\n"
        self.ser.write(line.encode("utf-8"))

    def read_line(self) -> str:
        raw = self.ser.readline()
        if not raw:
            return ""
        return raw.decode("utf-8", errors="replace").rstrip("\r\n")

    def expect(self, pattern: re.Pattern[str], timeout_s: float) -> re.Match[str]:
        deadline = time.time() + timeout_s
        buf: list[str] = []
        while time.time() < deadline:
            line = self.read_line()
            if not line:
                continue
            buf.append(line)
            m = pattern.search(line)
            if m:
                return m
        _die("timeout waiting for pattern; last lines:\n" + "\n".join(buf[-20:]))

    def drain_until_prompt(self, timeout_s: float = 2.0) -> None:
        self.expect(re.compile(r"^> "), timeout_s=timeout_s)


def run_case(mon: Monitor, *, tc: TestCase, boot_pc_default: int, boot_sp_default: int, timeout_s: float) -> tuple[int, int]:
    memh_text = read_memh_text(tc.memh)
    boot_pc = tc.boot_pc if tc.boot_pc is not None else boot_pc_default
    boot_sp = tc.boot_sp if tc.boot_sp is not None else boot_sp_default

    mon.write_line("RESET 1")
    mon.expect(re.compile(r"^OK RESET"), timeout_s=2.0)

    mon.write_line(f"BOOT {boot_pc:016x} {boot_sp:016x}")
    mon.expect(re.compile(r"^OK BOOT"), timeout_s=2.0)

    mon.write_line("LOAD_MEMH")
    mon.expect(re.compile(r"^OK LOAD_MEMH"), timeout_s=2.0)
    for line in memh_text.splitlines():
        mon.write_line(line)
    mon.write_line("END")
    mon.expect(re.compile(r"^OK LOADED"), timeout_s=10.0)

    mon.write_line("RUN")
    mon.expect(re.compile(r"^OK RUN"), timeout_s=2.0)
    m = mon.expect(HALT_RE, timeout_s=timeout_s)
    exit_code = int(m.group(1), 16)
    cycles = int(m.group(2), 10)
    return exit_code, cycles


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Linx FPGA regression/benchmarks via the Zybo PS monitor UART protocol.")
    ap.add_argument("--port", required=True, help="Windows COM port (e.g. COM5) or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200, help="PS UART baud rate (default: 115200)")
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--timeout", type=float, default=60.0, help="Per-test timeout in seconds")
    ap.add_argument("--monitor-timeout", type=float, default=0.5, help="UART read timeout in seconds")
    args = ap.parse_args()

    manifest_path = args.manifest.resolve()
    repo_root = Path(__file__).resolve().parents[2]
    os.chdir(repo_root)

    boot_pc_default, boot_sp_default, tests = load_manifest(manifest_path)

    mon = Monitor(args.port, args.baud, timeout_s=args.monitor_timeout)
    try:
        mon.drain_until_prompt(timeout_s=5.0)

        failures = 0
        for tc in tests:
            print(f"[fpga] {tc.name}")
            exit_code, cycles = run_case(
                mon,
                tc=tc,
                boot_pc_default=boot_pc_default,
                boot_sp_default=boot_sp_default,
                timeout_s=args.timeout,
            )
            print(f"[fpga] {tc.name}: exit=0x{exit_code:08x} cycles={cycles}")
            if exit_code != tc.expected_exit:
                print(f"[fpga] FAIL {tc.name}: expected_exit=0x{tc.expected_exit:08x}", file=sys.stderr)
                failures += 1

        return 1 if failures else 0
    finally:
        mon.close()


if __name__ == "__main__":
    raise SystemExit(main())

