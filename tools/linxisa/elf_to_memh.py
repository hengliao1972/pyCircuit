#!/usr/bin/env python3
from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path


SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_RELA = 4
SHT_NOBITS = 8
SHT_REL = 9

ET_REL = 1
ET_EXEC = 2

SHF_ALLOC = 0x2


@dataclass(frozen=True)
class Section:
    index: int
    name: str
    sh_type: int
    sh_flags: int
    sh_addr: int
    sh_offset: int
    sh_size: int
    sh_link: int
    sh_info: int
    sh_addralign: int
    sh_entsize: int


@dataclass(frozen=True)
class Symbol:
    name: str
    st_value: int
    st_size: int
    st_info: int
    st_other: int
    st_shndx: int


def _align_up(x: int, align: int) -> int:
    if align <= 0:
        return x
    return (x + align - 1) & ~(align - 1)


def _read_cstr(blob: bytes, off: int) -> str:
    end = blob.find(b"\x00", off)
    if end < 0:
        end = len(blob)
    return blob[off:end].decode("utf-8", errors="replace")


def _parse_sections(data: bytes) -> tuple[int, int, list[Section], dict[str, Section]]:
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")
    if data[4] != 2 or data[5] != 1:
        raise ValueError("unsupported ELF class/endian (need 64-bit LSB)")

    (e_ident, e_type, _e_machine, _e_version, e_entry, _e_phoff, e_shoff, _e_flags, _e_ehsize, _e_phentsize, _e_phnum, e_shentsize, e_shnum, e_shstrndx,) = struct.unpack_from(
        "<16sHHIQQQIHHHHHH", data, 0
    )
    _ = (e_ident, _e_version, e_entry, _e_phoff, _e_flags, _e_ehsize, _e_phentsize, _e_phnum)

    shdrs: list[tuple[int, int, int, int, int, int, int, int, int, int]] = []
    for i in range(e_shnum):
        sh = struct.unpack_from("<IIQQQQIIQQ", data, e_shoff + i * e_shentsize)
        shdrs.append(tuple(int(x) for x in sh))

    shstr = b""
    if 0 <= e_shstrndx < len(shdrs):
        sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, sh_addralign, sh_entsize = shdrs[
            e_shstrndx
        ]
        _ = (sh_name, sh_type, sh_flags, sh_addr, sh_link, sh_info, sh_addralign, sh_entsize)
        shstr = data[sh_offset : sh_offset + sh_size]

    sections: list[Section] = []
    by_name: dict[str, Section] = {}
    for i, raw in enumerate(shdrs):
        sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size, sh_link, sh_info, sh_addralign, sh_entsize = raw
        name = _read_cstr(shstr, sh_name) if shstr else ""
        sec = Section(
            index=i,
            name=name,
            sh_type=sh_type,
            sh_flags=sh_flags,
            sh_addr=sh_addr,
            sh_offset=sh_offset,
            sh_size=sh_size,
            sh_link=sh_link,
            sh_info=sh_info,
            sh_addralign=sh_addralign,
            sh_entsize=sh_entsize,
        )
        sections.append(sec)
        if name:
            by_name[name] = sec

    return int(e_type), int(e_entry), sections, by_name


def _parse_symtab(data: bytes, sections: list[Section], symtab: Section) -> list[Symbol]:
    if symtab.sh_type != SHT_SYMTAB:
        return []
    if symtab.sh_entsize == 0:
        raise ValueError("symtab has entsize=0")
    if not (0 <= symtab.sh_link < len(sections)):
        raise ValueError("symtab sh_link out of range")
    strtab = sections[symtab.sh_link]
    if strtab.sh_type != SHT_STRTAB:
        raise ValueError("symtab sh_link does not point to strtab")
    strblob = data[strtab.sh_offset : strtab.sh_offset + strtab.sh_size]

    out: list[Symbol] = []
    for off in range(0, symtab.sh_size, symtab.sh_entsize):
        st_name, st_info, st_other, st_shndx, st_value, st_size = struct.unpack_from(
            "<IBBHQQ", data, symtab.sh_offset + off
        )
        name = _read_cstr(strblob, int(st_name)) if st_name else ""
        out.append(
            Symbol(
                name=name,
                st_value=int(st_value),
                st_size=int(st_size),
                st_info=int(st_info),
                st_other=int(st_other),
                st_shndx=int(st_shndx),
            )
        )
    return out


def _apply_text_relocs(
    text: bytearray,
    *,
    text_addr: int,
    data: bytes,
    sections: list[Section],
    symtab: list[Symbol],
    section_addrs: dict[int, int],
) -> None:
    # Handle relocations that target .text.
    for sec in sections:
        if sec.sh_type not in (SHT_RELA, SHT_REL):
            continue
        if sec.sh_info < 0 or sec.sh_info >= len(sections):
            continue
        target = sections[sec.sh_info]
        if target.name != ".text":
            continue

        if sec.sh_entsize == 0:
            raise ValueError(f"{sec.name}: relocation section has entsize=0")

        for off in range(0, sec.sh_size, sec.sh_entsize):
            if sec.sh_type == SHT_RELA:
                r_offset, r_info, r_addend = struct.unpack_from("<QQq", data, sec.sh_offset + off)
                addend = int(r_addend)
            else:
                r_offset, r_info = struct.unpack_from("<QQ", data, sec.sh_offset + off)
                addend = 0
            r_offset = int(r_offset)
            r_info = int(r_info)
            sym_index = (r_info >> 32) & 0xFFFFFFFF
            r_type = int(r_info & 0xFFFFFFFF)

            if sym_index < 0 or sym_index >= len(symtab):
                raise ValueError(f"reloc @{r_offset:#x}: sym index out of range: {sym_index}")
            sym = symtab[sym_index]
            if sym.st_shndx == 0:
                raise ValueError(f"reloc @{r_offset:#x}: undefined symbol {sym.name!r}")
            if sym.st_shndx not in section_addrs:
                raise ValueError(f"reloc @{r_offset:#x}: no address for section {sym.st_shndx}")

            S = section_addrs[sym.st_shndx] + sym.st_value
            P = text_addr + r_offset

            if r_offset < 0 or r_offset >= len(text):
                raise ValueError(f"reloc @{r_offset:#x}: out of bounds for .text size {len(text)}")

            def read_u16(off: int) -> int:
                if off + 2 > len(text):
                    raise ValueError(f"reloc @{r_offset:#x}: out of bounds u16 read at {off:#x} (.text size {len(text)})")
                return int.from_bytes(text[off : off + 2], "little", signed=False)

            def write_u16(off: int, v: int) -> None:
                if off + 2 > len(text):
                    raise ValueError(f"reloc @{r_offset:#x}: out of bounds u16 write at {off:#x} (.text size {len(text)})")
                text[off : off + 2] = int(v & 0xFFFF).to_bytes(2, "little", signed=False)

            def read_u32(off: int) -> int:
                if off + 4 > len(text):
                    raise ValueError(f"reloc @{r_offset:#x}: out of bounds u32 read at {off:#x} (.text size {len(text)})")
                return int.from_bytes(text[off : off + 4], "little", signed=False)

            def write_u32(off: int, v: int) -> None:
                if off + 4 > len(text):
                    raise ValueError(f"reloc @{r_offset:#x}: out of bounds u32 write at {off:#x} (.text size {len(text)})")
                text[off : off + 4] = int(v & 0xFFFF_FFFF).to_bytes(4, "little", signed=False)

            def read_hl48(off: int) -> tuple[int, int]:
                # 48-bit HL instruction: 16-bit prefix at off, 32-bit main at off+2.
                prefix = read_u16(off)
                main32 = read_u32(off + 2)
                return prefix, main32

            def write_hl48(off: int, prefix: int, main32: int) -> None:
                write_u16(off, prefix)
                write_u32(off + 2, main32)

            def masked_eq(val: int, *, mask: int, match: int) -> bool:
                return (val & mask) == match

            # R_LINX_* PC-relative address materialization for `addtpc` + `addi`:
            #   rd = (PC & ~0xFFF) + (sext(imm20) << 12)   // addtpc
            #   rd = rd + uimm12                          // addi
            #
            # Split relocation scheme:
            #   hi20 reloc patches imm20 (allows any S; low 12 handled by lo12 reloc)
            #   lo12 reloc patches uimm12 (0..4095)
            #
            # This is analogous to RISC-V AUIPC/ADDI pairs, but with a page base.
            if r_type == 15:
                # ADDTPC hi20 (insn opcode 0x07, imm20 at bits[31:12]).
                insn = read_u32(r_offset)
                if (insn & 0x7F) != 0x07:
                    raise ValueError(f"hi20 reloc @{r_offset:#x}: expected ADDTPC, got insn=0x{insn:08x}")
                p_page = P & ~0xFFF
                delta = int((S + addend) - p_page)
                imm20 = delta >> 12  # arithmetic
                imm20_bits = imm20 & ((1 << 20) - 1)
                patched = (insn & ~(0xFFFFF << 12)) | (imm20_bits << 12)
                write_u32(r_offset, patched)
                continue

            if r_type == 17:
                # ADDI lo12 (mask=0x707f match=0x0015, uimm12 at bits[31:20]).
                insn = read_u32(r_offset)
                if (insn & 0x707F) != 0x0015:
                    raise ValueError(f"lo12 reloc @{r_offset:#x}: expected ADDI, got insn=0x{insn:08x}")
                p_page = P & ~0xFFF
                delta = int((S + addend) - p_page)
                uimm12 = delta & 0xFFF
                patched = (insn & ~(0xFFF << 20)) | (uimm12 << 20)
                write_u32(r_offset, patched)
                continue

            if r_type == 5:
                # 48-bit HL.BSTART.STD.CALL relocation (PC-relative in halfwords).
                prefix, main32 = read_hl48(r_offset)
                insn48 = int(prefix) | (int(main32) << 16)
                if not masked_eq(insn48, mask=0x00007FFF000F, match=0x00004001000E):
                    raise ValueError(f"hl_bstart_call reloc @{r_offset:#x}: unexpected insn48=0x{insn48:012x}")
                delta = int((S + addend) - P)
                if (delta & 0x1) != 0:
                    raise ValueError(
                        f"hl_bstart_call reloc @{r_offset:#x}: delta {delta:#x} not 2-byte aligned (S={S:#x} P={P:#x})"
                    )
                simm = delta >> 1
                hi12 = (simm >> 17) & 0xFFF
                lo17 = simm & 0x1FFFF
                prefix = (prefix & ~(0xFFF << 4)) | (hi12 << 4)
                main32 = (main32 & ~(0x1FFFF << 15)) | (lo17 << 15)
                write_hl48(r_offset, prefix, main32)
                continue

            if r_type == 20:
                # 48-bit HL.LW.PCR relocation (PC-relative byte offset).
                prefix, main32 = read_hl48(r_offset)
                insn48 = int(prefix) | (int(main32) << 16)
                # HL.<load>.PCR class match:
                # - prefix low nibble 0xE
                # - opcode 0x39
                # Ignore funct3, which encodes access width/signedness.
                if not masked_eq(insn48, mask=0x0000007F000F, match=0x00000039000E):
                    raise ValueError(f"hl_lw_pcr reloc @{r_offset:#x}: unexpected insn48=0x{insn48:012x}")
                delta = int((S + addend) - P)
                simm = delta
                hi12 = (simm >> 17) & 0xFFF
                lo17 = simm & 0x1FFFF
                prefix = (prefix & ~(0xFFF << 4)) | (hi12 << 4)
                main32 = (main32 & ~(0x1FFFF << 15)) | (lo17 << 15)
                write_hl48(r_offset, prefix, main32)
                continue

            if r_type == 21:
                # 48-bit HL.SW.PCR relocation (PC-relative byte offset).
                prefix, main32 = read_hl48(r_offset)
                insn48 = int(prefix) | (int(main32) << 16)
                # HL.<store>.PCR class match:
                # - prefix low nibble 0xE
                # - opcode 0x69
                # Ignore funct3 (width).
                if not masked_eq(insn48, mask=0x0000007F000F, match=0x00000069000E):
                    raise ValueError(f"hl_sw_pcr reloc @{r_offset:#x}: unexpected insn48=0x{insn48:012x}")
                delta = int((S + addend) - P)
                simm = delta
                hi12 = (simm >> 17) & 0xFFF
                mid5 = (simm >> 12) & 0x1F
                lo12 = simm & 0xFFF
                prefix = (prefix & ~(0xFFF << 4)) | (hi12 << 4)
                # packed bits[23:27] => main32 bits[7:11]
                main32 = (main32 & ~(0x1F << 7)) | (mid5 << 7)
                # packed bits[36:47] => main32 bits[20:31]
                main32 = (main32 & ~(0xFFF << 20)) | (lo12 << 20)
                write_hl48(r_offset, prefix, main32)
                continue

            # Legacy / bring-up support: BSTART.STD CALL in 32-bit form.
            # mask=0x7fff match=0x4001, simm17 in bits[31:15] (offset in halfwords).
            if r_offset + 4 <= len(text):
                insn = read_u32(r_offset)
                if (insn & 0x7FFF) == 0x4001:
                    delta = int((S + addend) - P)
                    if (delta & 0x1) != 0:
                        raise ValueError(
                            f"BSTART.STD reloc @{r_offset:#x}: delta {delta:#x} not 2-byte aligned (S={S:#x} P={P:#x})"
                        )
                    simm17 = delta >> 1
                    simm17_bits = simm17 & ((1 << 17) - 1)
                    patched = (insn & ~(((1 << 17) - 1) << 15)) | (simm17_bits << 15)
                    write_u32(r_offset, patched)
                    continue

                # Legacy ADDTPC hi20 (when r_type is unknown).
                if (insn & 0x7F) == 0x07:
                    p_page = P & ~0xFFF
                    delta = int((S + addend) - p_page)
                    imm20 = delta >> 12
                    imm20_bits = imm20 & ((1 << 20) - 1)
                    patched = (insn & ~(0xFFFFF << 12)) | (imm20_bits << 12)
                    write_u32(r_offset, patched)
                    continue

            raise ValueError(
                f"unsupported relocation at .text+0x{r_offset:x}: type={r_type} sym={sym.name!r} shndx={sym.st_shndx}"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="Build a memory init (.memh) from an ELF (relocatable or executable).")
    ap.add_argument("elf", help="Input ELF (.o or .elf)")
    ap.add_argument("-o", "--out", required=True, help="Output memh path")
    ap.add_argument("--text-base", default="0x10000", help="Base address for .text when input is ET_REL (hex)")
    ap.add_argument("--data-base", default="0x20000", help="Base address for .data when input is ET_REL (hex)")
    ap.add_argument("--page-align", default="0x1000", help="Alignment for section placement when ET_REL (hex)")
    ap.add_argument("--start-symbol", default="_start", help="Symbol to use as boot PC when emitting metadata (default: _start)")
    ap.add_argument("--print-start", action="store_true", help="Print resolved start PC (hex) to stdout")
    ap.add_argument(
        "--print-max",
        action="store_true",
        help="Print required max address (exclusive, hex) to stdout (after any --print-start line)",
    )
    ns = ap.parse_args()

    path = Path(ns.elf)
    data = path.read_bytes()
    e_type, e_entry, sections, by_name = _parse_sections(data)

    text_sec = by_name.get(".text")
    if text_sec is None:
        raise SystemExit("error: missing .text section")
    text_bytes = bytearray(data[text_sec.sh_offset : text_sec.sh_offset + text_sec.sh_size])

    data_sec = by_name.get(".data")
    bss_sec = by_name.get(".bss")

    symtab_sec = by_name.get(".symtab")
    if symtab_sec is None:
        raise SystemExit("error: missing .symtab section (needed for relocations)")
    symtab = _parse_symtab(data, sections, symtab_sec)

    # Determine section runtime addresses.
    section_addrs: dict[int, int] = {}
    if e_type == ET_REL:
        page_align = int(str(ns.page_align), 0)
        text_base = int(str(ns.text_base), 0)
        data_base = int(str(ns.data_base), 0)

        text_addr = _align_up(text_base, page_align)
        section_addrs[text_sec.index] = text_addr

        # Place all remaining SHF_ALLOC sections into the "data" region.
        # This includes .rodata*, .data, .bss, and any other alloc sections.
        cur = _align_up(data_base, page_align)
        for sec in sections:
            if sec.index == text_sec.index:
                continue
            if sec.sh_size == 0:
                continue
            if (sec.sh_flags & SHF_ALLOC) == 0:
                continue
            align = int(sec.sh_addralign) if sec.sh_addralign else 1
            cur = _align_up(cur, max(1, align))
            section_addrs[sec.index] = cur
            cur += int(sec.sh_size)
    elif e_type == ET_EXEC:
        for sec in sections:
            if sec.sh_addr != 0 and sec.sh_size != 0 and sec.name:
                section_addrs[sec.index] = sec.sh_addr
        text_addr = section_addrs.get(text_sec.index, text_sec.sh_addr)
    else:
        raise SystemExit(f"error: unsupported ELF type {e_type} (expected ET_REL={ET_REL} or ET_EXEC={ET_EXEC})")

    text_addr = section_addrs[text_sec.index]

    _apply_text_relocs(
        text_bytes,
        text_addr=text_addr,
        data=data,
        sections=sections,
        symtab=symtab,
        section_addrs=section_addrs,
    )

    segments: list[tuple[int, bytes]] = [(text_addr, bytes(text_bytes))]

    # Emit all remaining allocated sections (data/rodata/bss/etc).
    for sec in sections:
        if sec.index == text_sec.index:
            continue
        if sec.index not in section_addrs:
            continue
        if sec.sh_size == 0:
            continue
        if (sec.sh_flags & SHF_ALLOC) == 0:
            continue
        if sec.sh_type == SHT_NOBITS:
            segments.append((section_addrs[sec.index], bytes([0] * sec.sh_size)))
        else:
            segments.append((section_addrs[sec.index], data[sec.sh_offset : sec.sh_offset + sec.sh_size]))

    segments.sort(key=lambda x: x[0])

    max_end = 0
    for addr, blob in segments:
        max_end = max(max_end, int(addr) + len(blob))

    out_lines: list[str] = []
    for addr, blob in segments:
        if not blob:
            continue
        out_lines.append(f"@{addr:08x}")
        for b in blob:
            out_lines.append(f"{b:02x}")

    Path(ns.out).write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    if ns.print_start:
        start_sym = str(ns.start_symbol)
        start_pc: int | None = None
        for sym in symtab:
            if sym.name != start_sym:
                continue
            if sym.st_shndx == 0:
                continue
            if e_type == ET_REL:
                if sym.st_shndx not in section_addrs:
                    raise SystemExit(f"error: start symbol {start_sym!r} has unknown section index {sym.st_shndx}")
                start_pc = section_addrs[sym.st_shndx] + sym.st_value
            else:
                start_pc = sym.st_value
            break

        if start_pc is None and e_type == ET_EXEC and e_entry:
            start_pc = int(e_entry)

        if start_pc is None:
            raise SystemExit(f"error: start symbol {start_sym!r} not found")

        print(f"0x{start_pc:x}")

    if ns.print_max:
        print(f"0x{max_end:x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
