#!/usr/bin/env python3
"""ADV.BIN 反汇编工作台。文件线性映射在 RAM 0x800643a0。"""
import struct
import sys
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
from pathlib import Path

BASE = 0x800643A0
DATA = Path("/home/mark/Code/RomHacking/P1_Tools/extrac/ADV.BIN").read_bytes()
md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
md.detail = False


def off(ram): return ram - BASE
def ram(o): return o + BASE
def u32(o): return struct.unpack_from("<I", DATA, o)[0]


def dis(start_ram, n_instr=32, out=sys.stdout):
    o = off(start_ram)
    code = DATA[o:o + n_instr * 4]
    count = 0
    for i in md.disasm(code, start_ram):
        print(f"{i.address:08x}: {i.mnemonic:8s} {i.op_str}", file=out)
        count += 1
        if count >= n_instr:
            break
    if count == 0:
        print(f"  (no disasm at {start_ram:#x})", file=out)


def table(start_ram, n):
    o = off(start_ram)
    for i in range(n):
        v = u32(o + i * 4)
        print(f"  [{i:3d}] {start_ram + i*4:08x}: {v:08x}")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "dis":
        dis(int(sys.argv[2], 16), int(sys.argv[3]) if len(sys.argv) > 3 else 32)
    elif cmd == "table":
        table(int(sys.argv[2], 16), int(sys.argv[3]))
