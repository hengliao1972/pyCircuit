#!/usr/bin/env python3
"""Normalize tile instruction names and bundled descriptor summaries."""

from __future__ import annotations

import math
import re
from pathlib import Path


REPO_ROOT = Path("/Users/zhoubot/linx-isa/tools/pyCircuit")
MANUAL_ROOT = REPO_ROOT / "docs/pto-manual"
TILE_DIR = MANUAL_ROOT / "tile-instructions"


def tex_escape(text: object) -> str:
    s = "" if text is None else str(text)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(ch, ch) for ch in s)


def tt(text: object) -> str:
    return r"\texttt{" + tex_escape(text) + "}"


def display_name(raw: str) -> str:
    raw = raw.replace(r"\_", "_")
    return raw.removeprefix("pto.").upper()


def parse_rom_params(text: str) -> list[str]:
    match = re.search(r"^def\s+rom_[^(]+\((?P<params>[^)]*)\):", text, re.MULTILINE)
    if not match:
        return []
    return [part.strip() for part in match.group("params").split(",") if part.strip()]


def classify_params(params: list[str]) -> dict[str, list[str]]:
    roles = {"tile_inputs": [], "tile_outputs": [], "tile_masks": [], "scratch_tiles": [], "scalar_gprs": [], "other": []}
    for param in params:
        if param == "dst":
            roles["tile_outputs"].append(param)
        elif param == "mask":
            roles["tile_masks"].append(param)
        elif param == "tmp":
            roles["scratch_tiles"].append(param)
        elif param.startswith("src"):
            roles["tile_inputs"].append(param)
        elif param in {"scalar", "slope"}:
            roles["scalar_gprs"].append(param)
        else:
            roles["other"].append(param)
    return roles


def needs_dim(display: str, old_bundle: str) -> bool:
    return "B.DIM" in old_bundle or display.startswith(("TROW", "TCOL"))


def needs_attr(display: str, old_bundle: str) -> bool:
    return "B.ATTR" in old_bundle or display.startswith(("TCVT", "TFILLPAD", "TPART", "TSEL"))


def iot_count_for(roles: dict[str, list[str]]) -> int:
    """Return the number of architectural tile-binding descriptors.

    A B.IOT descriptor carries up to two tile sources and one tile destination.
    Scratch temporaries used by the ROM body are not PTO-ISA header operands.
    """

    tile_sources = roles["tile_inputs"] + roles["tile_masks"]
    tile_outputs = roles["tile_outputs"]
    if not tile_sources and not tile_outputs:
        return 0
    return max(len(tile_outputs), math.ceil(len(tile_sources) / 2), 1)


def bundle_for(display: str, old_bundle: str, roles: dict[str, list[str]]) -> list[str]:
    iot_count = iot_count_for(roles)
    bundle = ["B.OP"]
    if needs_attr(display, old_bundle):
        bundle.append("B.ATTR")
    if needs_dim(display, old_bundle):
        bundle.append("B.DIM")
    if roles["scalar_gprs"]:
        bundle.append("B.IOR")
    bundle.extend(["B.IOT"] * iot_count)
    return bundle


def join_tt(items: list[str]) -> str:
    if not items:
        return "none"
    return ", ".join(tt(item) for item in items)


def iot_mapping_text(roles: dict[str, list[str]], iot_count: int) -> str:
    parts = []
    if roles["tile_inputs"]:
        parts.append(f"{len(roles['tile_inputs'])} tile input(s): {join_tt(roles['tile_inputs'])}")
    if roles["tile_masks"]:
        parts.append(f"{len(roles['tile_masks'])} mask tile input(s): {join_tt(roles['tile_masks'])}")
    if roles["tile_outputs"]:
        parts.append(f"{len(roles['tile_outputs'])} tile output(s): {join_tt(roles['tile_outputs'])}")
    joined = "; ".join(parts) if parts else "No architectural tile operand."
    descriptor = "descriptor" if iot_count == 1 else "descriptors"
    scratch_note = (
        f" ROM scratch tiles are not counted in {tt('B.OP.desc_count')}."
        if roles["scratch_tiles"]
        else ""
    )
    return f"{joined}. Bound by {iot_count} architectural {tt('B.IOT')} {descriptor}; each {tt('B.IOT')} supplies at most two source selectors plus one destination selector.{scratch_note}"


def metadata_text(display: str, old_bundle: str, bundle: list[str]) -> str:
    fields = []
    if "B.DIM" in bundle:
        if display.startswith("TROW"):
            fields.append(f"{tt('B.DIM')} supplies row-axis reduction or row-broadcast bounds.")
        elif display.startswith("TCOL"):
            fields.append(f"{tt('B.DIM')} supplies column-axis reduction or column-broadcast bounds.")
        else:
            fields.append(f"{tt('B.DIM')} supplies profile dimension or loop-bound metadata.")
    if "B.ATTR" in bundle:
        if display.startswith("TCVT"):
            fields.append(f"{tt('B.ATTR')} carries source/destination format refinement for conversion.")
        elif display.startswith("TFILLPAD"):
            fields.append(f"{tt('B.ATTR')} carries pad/fill and valid-region policy.")
        elif display.startswith("TPART"):
            fields.append(f"{tt('B.ATTR')} carries partial-valid-region policy.")
        elif display.startswith("TSEL"):
            fields.append(f"{tt('B.ATTR')} carries predicate/mask interpretation metadata.")
        else:
            fields.append(f"{tt('B.ATTR')} carries profile-specific metadata refinements.")
    return " ".join(fields) if fields else "No additional dimension or attribute descriptor is required beyond the tile operand bindings."


def descriptor_roles(display: str, bundle: list[str], roles: dict[str, list[str]]) -> list[str]:
    result = []
    iot_index = 0
    for descriptor in bundle:
        if descriptor == "B.OP":
            result.append(
                f"{tt('B.OP')} selects {tt(display)}, carries element format/variant, "
                f"declares {tt('desc_count')}, and names the ROM entry."
            )
        elif descriptor == "B.ATTR":
            result.append(metadata_text(display, "", ["B.ATTR"]))
        elif descriptor == "B.DIM":
            result.append(metadata_text(display, "", ["B.DIM"]))
        elif descriptor == "B.IOR":
            result.append(
                f"{tt('B.IOR')} imports scalar operand(s) {join_tt(roles['scalar_gprs'])} "
                "through 5-bit scalar register IDs."
            )
        elif descriptor == "B.IOT":
            iot_index += 1
            label = f"B.IOT[{iot_index - 1}]" if bundle.count("B.IOT") > 1 else "B.IOT"
            result.append(
                f"{tt(label)} binds architectural tile operands. Across all {tt('B.IOT')} descriptors: "
                f"tile inputs {join_tt(roles['tile_inputs'])}, mask inputs {join_tt(roles['tile_masks'])}, "
                f"and outputs {join_tt(roles['tile_outputs'])}."
            )
    return result


def assembly_text(display: str, bundle: list[str], roles: dict[str, list[str]]) -> str:
    parts = [f"{index}: {text}" for index, text in enumerate(descriptor_roles(display, bundle, roles))]
    return " ".join(parts)


def bundle_summary(display: str, entry: str, bundle: list[str], roles: dict[str, list[str]]) -> str:
    iot_count = bundle.count("B.IOT")
    desc_count = len(bundle) - 1
    scalar = roles["scalar_gprs"]
    scratch = roles["scratch_tiles"]
    other = roles["other"]
    lines = [
        r"\paragraph{Bundled descriptor (PTO-ISA header).}",
        r"\begin{description}[leftmargin=0.18\linewidth,style=nextline]",
        rf"\item[Bundle] {tt(' + '.join(bundle))}. {tt('B.OP.desc_count')} = {desc_count}; {tt('B.OP.rom_entry_id')} = {tex_escape(entry)}.",
        rf"\item[Assembly] {assembly_text(display, bundle, roles)}",
        rf"\item[Tile operands] {iot_mapping_text(roles, iot_count)}",
    ]
    if scalar:
        lines.append(rf"\item[Scalar GPR operands] {len(scalar)} scalar input(s): {join_tt(scalar)}. Imported through {tt('B.IOR')} using 5-bit scalar register IDs.")
    else:
        lines.append(rf"\item[Scalar GPR operands] No scalar GPR import/export descriptor is required.")
    lines.append(rf"\item[Metadata operands] {metadata_text(display, '', bundle)}")
    if scratch:
        lines.append(rf"\item[ROM scratch operands] {join_tt(scratch)} are allocated by lowering or the implementation profile for the microcode body. They are not encoded in the CPU-visible PTO-ISA header.")
    if other:
        lines.append(rf"\item[Other arguments] {join_tt(other)} are profile-derived operands and MUST be supplied by descriptor metadata or implementation scratch state, not by an implicit source register.")
    lines.extend([r"\end{description}", r"\par\smallskip"])
    return "\n".join(lines)


def rewrite_entry(path: Path) -> bool:
    text = path.read_text()
    section_match = re.search(r"\\section\{(?:\\texttt|\s*exttt)\{(?P<name>[^}]*)\}\}", text)
    metadata_match = re.search(
        r"Vec entry\s+(?P<entry>\d+), tile catalog opcode \\texttt\{(?P<opcode>[^}]*)\}\. "
        r"Descriptor bundle: (?:\\texttt|\s*exttt)\{(?P<bundle>[^}]*)\}; "
        r"WO/WM sites: (?P<sites>\d+); VEC beat-level sites: (?P<vec_sites>\d+); ROM bytes: (?P<rom_bytes>\d+)\.",
        text,
    )
    macro_match = re.search(
        r"\\TileInstructionEncoding\{(?P<th0>[^}]*)\}\{(?P<th2>[^}]*)\}\{(?P<opcode>[^}]*)\}\{(?P<entry>[^}]*)\}\{(?P<bundle>[^}]*)\}",
        text,
    )
    if not section_match or not metadata_match or not macro_match:
        raise RuntimeError(f"Could not parse tile entry {path}")

    old_name = section_match.group("name")
    display = display_name(old_name)
    roles = classify_params(parse_rom_params(text))
    bundle = bundle_for(display, metadata_match.group("bundle"), roles)
    bundle_text = " + ".join(bundle)

    text = re.sub(
        r"\\section\{(?:\\texttt|\s*exttt)\{[^}]*\}\}",
        lambda _match: r"\section{" + tt(display) + "}",
        text,
        count=1,
    )
    text = re.sub(
        r"(Vec entry\s+\d+, tile catalog opcode \\texttt\{[^}]*\}\. Descriptor bundle: )(?:\\texttt|\s*exttt)\{[^}]*\}",
        lambda match: match.group(1) + tt(bundle_text),
        text,
        count=1,
    )
    text = re.sub(
        r"(\\TileInstructionEncoding\{[^}]*\}\{[^}]*\}\{[^}]*\}\{[^}]*\})\{[^}]*\}",
        lambda match: match.group(1) + "{" + bundle_text + "}",
        text,
        count=1,
    )

    summary = bundle_summary(display, metadata_match.group("entry"), bundle, roles)
    summary_re = re.compile(
        r"(?:\\paragraph\{(?:(?:Bundled instruction|Bundled descriptor)(?: \(PTO-ISA header\))?\.?|Operand and descriptor binding\.?)\}\n"
        r"\\begin\{description\}(?:\[[^\n]*\])?\n"
        r".*?\\end\{description\}\n"
        r"\\par\\smallskip\n)+",
        re.DOTALL,
    )
    if summary_re.search(text):
        text = summary_re.sub(lambda _match: summary + "\n", text, count=1)
    else:
        macro_end = re.search(r"^\\TileInstructionEncoding[^\n]*\n", text, re.MULTILINE)
        if not macro_end:
            raise RuntimeError(f"Could not place bundle summary in {path}")
        text = text[: macro_end.end()] + summary + "\n" + text[macro_end.end() :]

    text = re.sub(
        r"(?<!Descriptor profile\.} )Vec entry",
        r"\\noindent\\textbf{Descriptor profile.} Vec entry",
        text,
        count=1,
    )
    text = re.sub(
        r"(?:\\paragraph\{Microcode site table\.?\}\n)?\{\\scriptsize\n\\begin\{longtable\}",
        r"\\paragraph{Microcode site table.}\n{\\scriptsize\n\\begin{longtable}",
        text,
        count=1,
    )
    text = re.sub(
        r"Seq & Micro instruction & WO fields & WM fields \\\\",
        lambda _match: r"Seq & Micro instruction & WO[63:32] fields & WM[31:0] fields \\",
        text,
        count=1,
    )
    text = re.sub(
        r"(?:\\paragraph\{ROM body DSL\.?\}\n)?\\begin\{lstlisting\}",
        r"\\paragraph{ROM body DSL.}\n\\begin{lstlisting}",
        text,
        count=1,
    )

    changed = text != path.read_text()
    if changed:
        path.write_text(text)
    return changed


def main() -> None:
    changed = 0
    for path in sorted(TILE_DIR.glob("*.tex")):
        if rewrite_entry(path):
            changed += 1
    print(f"rewritten tile entries: {changed}")


if __name__ == "__main__":
    main()
