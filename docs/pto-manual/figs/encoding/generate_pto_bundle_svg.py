#!/usr/bin/env python3
"""Generate WaveDrom register diagrams for PTO instruction encodings."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
import re


BASE_DIR = Path(__file__).resolve().parent
MANUAL_ROOT = BASE_DIR.parents[1]
TILE_DIR = MANUAL_ROOT / "tile-instructions"
ENTRY_DIR = BASE_DIR / "tile_instruction_entries"


def field(name: str, start: int, end: int, attr: str = "") -> dict[str, object]:
    data: dict[str, object] = {"name": name, "start": start, "end": end}
    if attr:
        data["attr"] = attr
    return data


TEMPLATE_TMA = [
    field("DataType", 27, 31, "DT"),
    field("MODE", 25, 26),
    field("Function", 20, 24, "FN"),
    field("Family", 15, 19, "TMA"),
    field("PREFIX", 12, 14),
    field("KIND", 7, 11),
    field("PREFIX", 4, 6),
    field("PREFIX", 1, 3),
    field("X", 0, 0),
]
TEMPLATE_CUBE = [dict(item, **({"attr": "CUBE"} if item["name"] == "Family" else {})) for item in TEMPLATE_TMA]
TEMPLATE_TEPL = [
    field("DataType", 27, 31, "DT"),
    field("MODE", 25, 26),
    field("TileOpcode", 15, 24, "TOP"),
    field("PREFIX", 12, 14),
    field("KIND", 7, 11),
    field("PREFIX", 4, 6),
    field("PREFIX", 1, 3),
    field("X", 0, 0),
]
TEMPLATE_VPAR = [
    field("RSVD", 27, 31),
    field("Mode", 25, 26, "M"),
    field("RSVD", 20, 24),
    field("Family", 15, 19, "VPAR"),
    field("PREFIX", 12, 14),
    field("KIND", 7, 11),
    field("PREFIX", 4, 6),
    field("PREFIX", 1, 3),
    field("X", 0, 0),
]
TEMPLATE_VSEQ = [dict(item, **({"attr": "VSEQ"} if item["name"] == "Family" else {})) for item in TEMPLATE_VPAR]

B_OP = [
    field("OpcodeSel", 22, 31, "OP"),
    field("DataType", 17, 21, "DT"),
    field("ROMEntry", 12, 16, "RID"),
    field("DescCnt", 8, 11, "DC"),
    field("Variant", 5, 7, "VAR"),
    field("Ext", 4, 4, "E"),
    field("Primary", 1, 3, "PRI"),
    field("Marker", 0, 0, "M"),
]
B_ATTR = [
    field("PadValue", 27, 31, "PV"),
    field("DR", 26, 26),
    field("C", 25, 25),
    field("DataType", 20, 24, "DT"),
    field("T", 19, 19),
    field("far", 18, 18),
    field("atom", 17, 17),
    field("aq", 16, 16),
    field("rl", 15, 15),
    field("PREFIX", 12, 14),
    field("DataLayout", 7, 11, "DL"),
    field("PREFIX", 4, 6),
    field("PREFIX", 1, 3),
    field("X", 0, 0),
]
B_ARG = [
    field("RSVD", 15, 31),
    field("PREFIX", 12, 14),
    field("format", 7, 11, "FMT"),
    field("PREFIX", 4, 6),
    field("PREFIX", 1, 3),
    field("X", 0, 0),
]
B_DIM = [
    field("uimm17", 20, 31, "IMM_LO"),
    field("RegSrc", 15, 19, "RS"),
    field("PREFIX", 12, 14),
    field("uimm17", 7, 11, "IMM_HI"),
    field("PREFIX", 4, 6),
    field("PREFIX", 1, 3),
    field("X", 0, 0),
]
B_TEXT = [
    field("simm25", 7, 31, "SIMM"),
    field("PREFIX", 4, 6),
    field("PREFIX", 1, 3),
    field("X", 0, 0),
]
B_HINT = [
    field("pref_size", 20, 31, "PSZ"),
    field("RSVD", 19, 19),
    field("temp", 17, 18, "TMP"),
    field("L/UL", 16, 16, "LU"),
    field("V", 15, 15, "VLD"),
    field("PREFIX", 12, 14),
    field("RSVD", 7, 11),
    field("PREFIX", 4, 6),
    field("PREFIX", 1, 3),
    field("X", 0, 0),
]
B_IOR = [
    field("RegSrc2", 27, 31, "RS2"),
    field("RSVD", 25, 26),
    field("RegSrc1", 20, 24, "RS1"),
    field("RegSrc0", 15, 19, "RS0"),
    field("PREFIX", 12, 14),
    field("RegDst", 7, 11, "RD"),
    field("PREFIX", 4, 6),
    field("PREFIX", 1, 3),
    field("X", 0, 0),
]
B_IOT = [
    field("S1R", 31, 31),
    field("S0R", 30, 30),
    field("S1V", 29, 29),
    field("S0V", 28, 28),
    field("DstTile", 25, 27, "TDT"),
    field("SrcTile1", 20, 24, "ST1"),
    field("SrcTile0", 15, 19, "ST0"),
    field("PREFIX", 12, 14),
    field("SizeCode/RegSrc", 7, 11, "SZ/RS"),
    field("PREFIX", 4, 6),
    field("PREFIX", 1, 3),
    field("X", 0, 0),
]
MICRO_64 = [
    field("WM.RSV", 0, 8, "0"),
    field("WM.VAR", 9, 11, "V"),
    field("WM.SRC2", 12, 14, "S2"),
    field("WM.SRC1", 15, 17, "S1"),
    field("WM.SRC0", 18, 20, "S0"),
    field("WM.RES", 21, 22, "R"),
    field("WM.COUNT", 23, 24, "C"),
    field("WM.ENTRY", 25, 31, "EID"),
    field("WO.OP_C", 32, 35, "C"),
    field("WO.OP_B", 36, 39, "B"),
    field("WO.OP_A", 40, 43, "A"),
    field("WO.KIND", 44, 45, "K"),
    field("WO.SEQ", 46, 52, "S"),
    field("WO.FLAGS", 53, 55, "F"),
    field("WO.X", 56, 56, "X"),
    field("WO.OPCODE", 57, 63, "OP"),
]


REGISTER_DIAGRAMS = {
    "pto_template_tma_instruction": TEMPLATE_TMA,
    "pto_template_cube_instruction": TEMPLATE_CUBE,
    "pto_template_tepl_instruction": TEMPLATE_TEPL,
    "pto_template_vpar_instruction": TEMPLATE_VPAR,
    "pto_template_vseq_instruction": TEMPLATE_VSEQ,
    "pto_b_op_instruction": B_OP,
    "pto_b_attr_instruction": B_ATTR,
    "pto_b_arg_instruction": B_ARG,
    "pto_b_dim_instruction": B_DIM,
    "pto_b_text_instruction": B_TEXT,
    "pto_b_hint_instruction": B_HINT,
    "pto_b_ior_instruction": B_IOR,
    "pto_b_iot_instruction": B_IOT,
    "micro_64_instruction": MICRO_64,
}


def wavedrom_reg(fields: list[dict[str, object]], bits: int = 32, lanes: int = 1) -> dict[str, object]:
    reg = []
    for item in sorted(fields, key=lambda value: int(value["start"])):
        reg_item: dict[str, object] = {
            "bits": int(item["end"]) - int(item["start"]) + 1,
            "name": item["name"],
        }
        if item.get("attr"):
            reg_item["attr"] = item["attr"]
        reg.append(reg_item)
    return {"reg": reg, "config": {"bits": bits, "lanes": lanes}}


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def render_wavedrom(payload: dict[str, object], stem: Path) -> bool:
    json_path = stem.with_suffix(".json")
    svg_path = stem.with_suffix(".svg")
    pdf_path = stem.with_suffix(".pdf")
    changed = write_if_changed(json_path, json.dumps(payload, indent=2) + "\n")
    if not changed and svg_path.exists() and pdf_path.exists():
        return False
    if shutil.which("npx") is None:
        raise RuntimeError("npx is required to render WaveDrom diagrams")
    if shutil.which("rsvg-convert") is None:
        raise RuntimeError("rsvg-convert is required to convert WaveDrom SVG output to PDF")
    subprocess.run(
        ["npx", "-y", "wavedrom-cli", "-i", str(json_path), "-s", str(svg_path)],
        check=True,
    )
    subprocess.run(["rsvg-convert", "-f", "pdf1.5", "-o", str(pdf_path), str(svg_path)], check=True)
    return True


def descriptor_stream_payload(words: list[str]) -> dict[str, object]:
    reg = []
    for index, word in enumerate(words):
        reg.append({"bits": 32, "name": f"W{index}: {word}", "attr": "32-bit word"})
    return {"reg": reg, "config": {"bits": 32 * len(words), "lanes": len(words)}}


def b_op_payload(catalog_opcode: str, entry: str, desc_count: int) -> dict[str, object]:
    overrides = {
        "OpcodeSel": catalog_opcode,
        "DataType": "profile",
        "ROMEntry": entry,
        "DescCnt": str(desc_count),
        "Variant": "VAR",
        "Ext": "E",
        "Primary": "PRI",
        "Marker": "M",
    }
    fields = []
    for item in B_OP:
        copied = dict(item)
        copied["attr"] = overrides.get(str(item["name"]), str(item.get("attr", "")))
        fields.append(copied)
    return wavedrom_reg(fields)


def generate_global_diagrams() -> tuple[int, int]:
    rendered = 0
    skipped = 0
    for stem, fields in REGISTER_DIAGRAMS.items():
        bits = 64 if stem == "micro_64_instruction" else 32
        lanes = 2 if stem == "micro_64_instruction" else 1
        if render_wavedrom(wavedrom_reg(fields, bits=bits, lanes=lanes), BASE_DIR / stem):
            rendered += 1
        else:
            skipped += 1
    return rendered, skipped


def generate_tile_instruction_entry_diagrams() -> tuple[int, int]:
    ENTRY_DIR.mkdir(parents=True, exist_ok=True)
    metadata_pattern = re.compile(
        r"Vec entry\s+(?P<entry>\d+), tile catalog opcode \\texttt\{(?P<opcode>[^}]*)\}\. "
        r"Descriptor bundle: \\texttt\{(?P<bundle>[^}]*)\}; "
        r"WO/WM sites: (?P<sites>\d+); VEC beat-level sites: (?P<vec_sites>\d+); ROM bytes: (?P<rom_bytes>\d+)\.",
        re.MULTILINE,
    )
    macro_pattern = re.compile(
        r"\\TileInstructionEncoding\{(?P<th0>[^}]*)\}\{(?P<th2>[^}]*)\}\{[^}]*\}\{[^}]*\}\{[^}]*\}"
    )
    rendered = 0
    skipped = 0
    for tex_path in sorted(TILE_DIR.glob("*.tex")):
        text = tex_path.read_text()
        metadata = metadata_pattern.search(text)
        macro = macro_pattern.search(text)
        if metadata is None or macro is None:
            raise RuntimeError(f"Could not parse tile instruction metadata from {tex_path}")
        data = metadata.groupdict()
        bundle_words = [part.strip() for part in data["bundle"].split("+")]
        desc_count = max(0, len(bundle_words) - 1)
        th0 = ENTRY_DIR / Path(macro.group("th0")).with_suffix("").name
        th2 = ENTRY_DIR / Path(macro.group("th2")).with_suffix("").name
        if render_wavedrom(descriptor_stream_payload(bundle_words), th0):
            rendered += 1
        else:
            skipped += 1
        if render_wavedrom(b_op_payload(data["opcode"], data["entry"], desc_count), th2):
            rendered += 1
        else:
            skipped += 1
    return rendered, skipped


def main() -> None:
    rendered, skipped = generate_global_diagrams()
    entry_rendered, entry_skipped = generate_tile_instruction_entry_diagrams()
    print(f"WaveDrom diagrams rendered: {rendered + entry_rendered}; unchanged: {skipped + entry_skipped}")


if __name__ == "__main__":
    main()
