"""Optional post-Verilog EDA integrations.

This module deliberately starts after ``pycc`` has emitted verified Verilog.
External synthesis tools consume that output; they never alter PYC semantics or
replace the MLIR legality gates.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class EdaError(RuntimeError):
    """Raised when an optional EDA flow cannot be staged or completed."""


@dataclass(frozen=True)
class XingtianOptions:
    """Configuration for one XingTian remote-synthesis invocation."""

    sdc: Path | None = None
    config: Path | None = None
    runner: Path | None = None
    host: str | None = None
    ssh_config: Path | None = None
    identity_file: Path | None = None
    lib_variant: str = "6t"
    sdc_style: str = "native"


@dataclass(frozen=True)
class EdaResult:
    """Normalized local artifacts returned by one post-Verilog EDA flow."""

    flow: str
    exit_code: int
    result_dir: Path | None
    netlist: Path | None
    qor_report: Path | None
    metrics: Path | None
    log: Path | None
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        """Return true only when the runner and required netlist succeeded."""

        return self.exit_code == 0 and self.netlist is not None

    def as_manifest(self, *, relative_to: Path) -> dict[str, Any]:
        """Render paths relative to the pyCircuit build directory."""

        def rel(path: Path | None) -> str | None:
            if path is None:
                return None
            try:
                return str(path.resolve().relative_to(relative_to.resolve()))
            except ValueError:
                return str(path.resolve())

        return {
            "flow": self.flow,
            "success": self.success,
            "exit_code": self.exit_code,
            "result_dir": rel(self.result_dir),
            "netlist": rel(self.netlist),
            "qor_report": rel(self.qor_report),
            "metrics": rel(self.metrics),
            "log": rel(self.log),
        }


XingtianResult = EdaResult


@dataclass(frozen=True)
class EdaRunRequest:
    """Backend-neutral inputs produced by the verified Verilog pipeline."""

    top: str
    verilog_files: tuple[Path, ...]
    build_dir: Path


class EdaFlow(Protocol):
    """Typed extension point for a registered post-Verilog backend."""

    name: str

    def run(
        self,
        request: EdaRunRequest,
        options: Mapping[str, Any],
    ) -> EdaResult:
        """Run this backend for a staged pyCircuit design."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_runner() -> Path:
    override = os.environ.get("PYC_XINGTIAN_RUNNER", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent / "tools" / "xingtian" / "xt_remote_syn.sh"


def xingtian_runner_path() -> Path:
    """Return the packaged XingTian runner used by the typed adapter."""

    return _default_runner()


def _config_path(options: XingtianOptions) -> tuple[Path, bool]:
    if options.config is not None:
        return options.config.expanduser().resolve(), True
    override = os.environ.get("PYC_XINGTIAN_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve(), True
    return (
        Path.cwd() / "eda" / "xingtian" / "config.local.json"
    ).resolve(), False


def _configured_options(options: XingtianOptions) -> XingtianOptions:
    config_path, required = _config_path(options)
    config: dict[str, Any] = {}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EdaError(f"cannot read XingTian config {config_path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise EdaError(f"XingTian config must contain a JSON object: {config_path}")
        allowed = {"host", "sdc", "ssh_config", "identity_file"}
        unknown = sorted(set(loaded) - allowed)
        if unknown:
            raise EdaError(
                f"unknown XingTian config field(s): {', '.join(unknown)}"
            )
        for key, value in loaded.items():
            if not isinstance(value, str) or not value.strip():
                kind = "host string" if key == "host" else "path string"
                raise EdaError(f"XingTian config field {key!r} must be a {kind}")
        config = loaded
    elif required:
        raise EdaError(f"XingTian config does not exist: {config_path}")

    def configured_path(key: str) -> Path | None:
        raw = config.get(key)
        if raw is None:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = config_path.parent / path
        return path.resolve()

    sdc = options.sdc or configured_path("sdc")
    if sdc is None:
        raise EdaError(
            "XingTian SDC is not configured; use --eda-sdc or "
            "eda/xingtian/config.local.json"
        )
    host = options.host or config.get("host")
    if host is None:
        raise EdaError(
            "XingTian SSH host is not configured; use --eda-host or "
            "eda/xingtian/config.local.json"
        )
    return XingtianOptions(
        sdc=sdc,
        config=config_path if config_path.is_file() else None,
        runner=options.runner,
        host=host,
        ssh_config=options.ssh_config or configured_path("ssh_config"),
        identity_file=options.identity_file or configured_path("identity_file"),
        lib_variant=options.lib_variant,
        sdc_style=options.sdc_style,
    )


def _validated_options(options: XingtianOptions) -> XingtianOptions:
    runner = (options.runner or _default_runner()).expanduser().resolve()
    if not runner.is_file() or not os.access(runner, os.X_OK):
        raise EdaError(f"XingTian runner is missing or not executable: {runner}")
    sdc = options.sdc
    if sdc is not None:
        sdc = sdc.expanduser().resolve()
        if not sdc.is_file():
            raise EdaError(f"XingTian SDC does not exist: {sdc}")
    if options.lib_variant not in {"6t", "asap7", "7p5t"}:
        raise EdaError(f"unsupported XingTian library variant: {options.lib_variant}")
    if options.sdc_style not in {"native", "dc"}:
        raise EdaError(f"unsupported XingTian SDC style: {options.sdc_style}")
    if options.host is None or not options.host.strip():
        raise EdaError("XingTian SSH host must not be empty")

    ssh_config = options.ssh_config
    if ssh_config is not None:
        ssh_config = ssh_config.expanduser().resolve()
        if not ssh_config.is_file():
            raise EdaError(f"SSH config does not exist: {ssh_config}")

    identity_file = options.identity_file
    if identity_file is not None:
        identity_file = identity_file.expanduser().resolve()
        if not identity_file.is_file():
            raise EdaError(f"SSH identity file does not exist: {identity_file}")

    return XingtianOptions(
        sdc=sdc,
        config=options.config,
        runner=runner,
        host=options.host.strip(),
        ssh_config=ssh_config,
        identity_file=identity_file,
        lib_variant=options.lib_variant,
        sdc_style=options.sdc_style,
    )


def stage_verilog_project(
    *,
    verilog_files: Iterable[Path],
    sdc: Path | None,
    staging_dir: Path,
) -> Path:
    """Copy generated RTL and an optional SDC into an rsync-safe project.

    Each non-primitive file keeps its path relative to the common ``verilog``
    root supplied by the caller. Duplicate ``pyc_primitives.v`` files are
    accepted only when their content is identical. If ``sdc`` is omitted, the
    runner is responsible for materializing its machine-local default.
    """

    sources = sorted({Path(path).resolve() for path in verilog_files}, key=str)
    if not sources:
        raise EdaError("XingTian staging received no generated Verilog files")
    missing = [str(path) for path in sources if not path.is_file()]
    if missing:
        raise EdaError(f"generated Verilog file is missing: {missing[0]}")
    if sdc is not None:
        sdc = sdc.expanduser().resolve()
        if not sdc.is_file():
            raise EdaError(f"XingTian SDC does not exist: {sdc}")

    staging_dir = staging_dir.resolve()
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    rtl_dir = staging_dir / "rtl"
    rtl_dir.mkdir(parents=True)

    primitive_sources = [path for path in sources if path.name == "pyc_primitives.v"]
    if primitive_sources:
        primitive_hashes = {_sha256(path) for path in primitive_sources}
        if len(primitive_hashes) != 1:
            raise EdaError("generated pyc_primitives.v files differ across modules")

    # The immediate children of device/verilog are module directories. Keeping
    # that relative layout avoids basename collisions between generated files.
    common_root = Path(os.path.commonpath([str(path.parent) for path in sources]))
    filelist_entries: list[str] = []
    primitive_written = False
    for source in sources:
        if source.name == "pyc_primitives.v":
            if primitive_written:
                continue
            destination = rtl_dir / "pyc_primitives.v"
            primitive_written = True
        else:
            relative = source.relative_to(common_root)
            destination = rtl_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=True)
        filelist_entries.append(destination.relative_to(staging_dir).as_posix())

    if sdc is not None:
        staged_sdc = staging_dir / "constraints.sdc.tcl"
        shutil.copy2(sdc, staged_sdc, follow_symlinks=True)
    filelist = staging_dir / "filelist.f"
    filelist.write_text("".join(f"{entry}\n" for entry in filelist_entries), encoding="utf-8")
    return filelist


stage_xingtian_project = stage_verilog_project


def _latest_result(results_dir: Path, *, previous: set[Path]) -> Path | None:
    latest = results_dir / "latest"
    if latest.exists():
        return latest.resolve()
    candidates = (
        sorted(
            (
                path
                for path in results_dir.iterdir()
                if path.is_dir() and path.resolve() not in previous
            ),
            key=lambda path: path.name,
        )
        if results_dir.is_dir()
        else []
    )
    return candidates[-1].resolve() if candidates else None


def run_xingtian(
    *,
    top: str,
    verilog_files: Iterable[Path],
    build_dir: Path,
    options: XingtianOptions,
) -> XingtianResult:
    """Stage and run remote XingTian synthesis, preserving all local evidence."""

    checked = _validated_options(_configured_options(options))
    eda_root = build_dir.resolve() / "eda" / "xingtian"
    staging_dir = eda_root / "input"
    results_dir = eda_root / "results"
    stage_verilog_project(
        verilog_files=verilog_files,
        sdc=checked.sdc,
        staging_dir=staging_dir,
    )
    results_dir.mkdir(parents=True, exist_ok=True)

    command = [
        str(checked.runner),
        "--design",
        str(staging_dir),
        "--top",
        top,
        "--filelist",
        "filelist.f",
        "--results-dir",
        str(results_dir),
        "--host",
        checked.host,
        "--lib-variant",
        checked.lib_variant,
        "--sdc-style",
        checked.sdc_style,
    ]
    if checked.sdc is not None:
        command.extend(["--sdc", "constraints.sdc.tcl"])
    if checked.ssh_config is not None:
        command.extend(["--ssh-config", str(checked.ssh_config)])
    if checked.identity_file is not None:
        command.extend(["--identity-file", str(checked.identity_file)])

    previous_results = {
        path.resolve() for path in results_dir.iterdir() if path.is_dir()
    }
    previous_latest = results_dir / "latest"
    if previous_latest.is_symlink() or previous_latest.is_file():
        previous_latest.unlink()
    process = subprocess.run(command, text=True, capture_output=True)
    result_dir = _latest_result(results_dir, previous=previous_results)
    netlist = result_dir / "output" / f"{top}.mapped.v" if result_dir else None
    if netlist is not None and not netlist.is_file():
        netlist = None
    qor = result_dir / "rpt" / "qor.rpt" if result_dir else None
    if qor is not None and not qor.is_file():
        qor = None
    metrics = result_dir / "rpt" / "metrics.json" if result_dir else None
    if metrics is not None and not metrics.is_file():
        metrics = None
    log = result_dir / "log" / "xt_run.log" if result_dir else None
    if log is not None and not log.is_file():
        log = None

    result = EdaResult(
        flow="xingtian",
        exit_code=process.returncode,
        result_dir=result_dir,
        netlist=netlist,
        qor_report=qor,
        metrics=metrics,
        log=log,
        stdout=process.stdout,
        stderr=process.stderr,
    )
    summary_path = eda_root / "result.json"
    summary_path.write_text(
        json.dumps(result.as_manifest(relative_to=build_dir), sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


@dataclass(frozen=True)
class XingtianFlow:
    """Registered adapter for remote XingTian logic synthesis."""

    name: str = "xingtian"

    def run(
        self,
        request: EdaRunRequest,
        options: Mapping[str, Any],
    ) -> EdaResult:
        allowed = {
            "sdc",
            "config",
            "runner",
            "host",
            "ssh_config",
            "identity_file",
            "lib_variant",
            "sdc_style",
        }
        unknown = sorted(set(options) - allowed)
        if unknown:
            raise EdaError(
                f"unsupported {self.name} option(s): {', '.join(unknown)}"
            )

        def optional_path(name: str) -> Path | None:
            value = options.get(name)
            if value is None or value == "":
                return None
            return value if isinstance(value, Path) else Path(str(value))

        host_value = options.get("host")
        host = None if host_value is None or host_value == "" else str(host_value)

        return run_xingtian(
            top=request.top,
            verilog_files=request.verilog_files,
            build_dir=request.build_dir,
            options=XingtianOptions(
                sdc=optional_path("sdc"),
                config=optional_path("config"),
                runner=optional_path("runner"),
                host=host,
                ssh_config=optional_path("ssh_config"),
                identity_file=optional_path("identity_file"),
                lib_variant=str(options.get("lib_variant") or "6t"),
                sdc_style=str(options.get("sdc_style") or "native"),
            ),
        )


_EDA_FLOWS: dict[str, EdaFlow] = {}


def register_eda_flow(flow: EdaFlow, *, replace: bool = False) -> None:
    """Register a typed backend adapter by its stable CLI name."""

    name = str(flow.name).strip()
    if not name or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for ch in name):
        raise EdaError(f"invalid EDA flow name: {name!r}")
    if name in _EDA_FLOWS and not replace:
        raise EdaError(f"EDA flow is already registered: {name}")
    _EDA_FLOWS[name] = flow


def available_eda_flows() -> tuple[str, ...]:
    """Return registered backend names in deterministic CLI order."""

    return tuple(sorted(_EDA_FLOWS))


def run_eda_flow(
    flow_name: str,
    *,
    top: str,
    verilog_files: Iterable[Path],
    build_dir: Path,
    options: Mapping[str, Any] | None = None,
) -> EdaResult:
    """Dispatch verified Verilog artifacts to one registered backend."""

    flow = _EDA_FLOWS.get(flow_name)
    if flow is None:
        available = ", ".join(available_eda_flows()) or "<none>"
        raise EdaError(
            f"unknown EDA flow {flow_name!r}; available flows: {available}"
        )
    request = EdaRunRequest(
        top=top,
        verilog_files=tuple(Path(path) for path in verilog_files),
        build_dir=build_dir,
    )
    return flow.run(request, {} if options is None else options)


def _register_builtin_flows() -> None:
    from .eda_backends.nangate45 import Nangate45Flow

    register_eda_flow(XingtianFlow())
    register_eda_flow(Nangate45Flow())


_register_builtin_flows()
