#!/usr/bin/env python3
"""给 ADV.BIN 注入 FF5B 动态字库 memcpy handler。

记录格式（4 字节）：`FF 5B <page_file_offset:u16>`。
页包格式：`<byte_count:u32> <glyph bytes...>`。

byte_count 必须是 4 的倍数。handler 把字形包拷到
0x801E0000 + CORE*32，然后跳回原 VM 公共收尾。
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
ADV_BASE = 0x800643A0
HANDLER_RAM = 0x800BADEC
HANDLER_OFF = HANDLER_RAM - ADV_BASE
RETURN_RAM = 0x800AD218
DISPATCH_RAM = 0x800655E4
DISPATCH_OFF = DISPATCH_RAM - ADV_BASE
LENGTH_OFF = 0x568F0 + 0x5B
DEFAULT_CORE = 1536


REG = {
    "zero": 0, "v0": 2,
    "a0": 4, "a1": 5, "a2": 6,
    "s5": 21,
}


def i_type(op: int, rs: int, rt: int, imm: int) -> int:
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def lw(rt, imm, rs):
    return i_type(0x23, REG[rs], REG[rt], imm)


def sw(rt, imm, rs):
    return i_type(0x2B, REG[rs], REG[rt], imm)


def lui(rt, imm):
    return i_type(0x0F, 0, REG[rt], imm)


def ori(rt, rs, imm):
    return i_type(0x0D, REG[rs], REG[rt], imm)


def addiu(rt, rs, imm):
    return i_type(0x09, REG[rs], REG[rt], imm)


def lhu(rt, imm, rs):
    return i_type(0x25, REG[rs], REG[rt], imm)


def addu(rd, rs, rt):
    return (REG[rs] << 21) | (REG[rt] << 16) | (REG[rd] << 11) | 0x21


def branch(op: int, rs: str, rt: str, at_index: int, target_index: int) -> int:
    # MIPS branch 相对于延迟槽之后的下一条：PC+4 + imm*4。
    imm = target_index - (at_index + 1)
    return i_type(op, REG[rs], REG[rt], imm)


def jump(address: int) -> int:
    return (0x02 << 26) | ((address >> 2) & 0x03FFFFFF)


def make_handler(core_size: int, mode: str = "copy") -> bytes:
    if mode == "default":
        return b""
    if mode == "noop":
        return struct.pack("<2I", jump(RETURN_RAM), 0)
    if mode != "copy":
        raise ValueError(f"未知 handler mode: {mode}")
    swap_address = 0x801E0000 + core_size * 32
    if swap_address >> 16 != 0x801E:
        raise ValueError("CORE 换页起点越出 0x801Exxxx")

    words = [
        lhu("a0", 2, "s5"),       # section 文件偏移
        lui("v0", 0x8010),
        addu("a0", "a0", "v0"),
        addiu("a0", "a0", -8),    # file+8 映射到 RAM+0
        lw("a2", 0, "a0"),        # 页包头：byte_count
        addiu("a0", "a0", 4),
        0,  # beq -> done
        lui("a1", 0x801E),
        ori("a1", "a1", swap_address & 0xFFFF),
        lw("v0", 0, "a0"),
        addiu("a0", "a0", 4),
        addiu("a2", "a2", -4),
        sw("v0", 0, "a1"),
        0,  # bne -> loop
        addiu("a1", "a1", 4),
        jump(RETURN_RAM),
        0,
    ]
    words[6] = branch(0x04, "a2", "zero", 6, 15)
    words[13] = branch(0x05, "a2", "zero", 13, 9)
    return struct.pack("<%dI" % len(words), *words)


def patch(source: Path, destination: Path, core_size: int,
          mode: str = "copy") -> bytes:
    data = bytearray(source.read_bytes())
    handler = make_handler(core_size, mode)
    if handler and any(data[HANDLER_OFF:HANDLER_OFF + len(handler)]):
        raise ValueError(f"ADV 注入区 {HANDLER_OFF:#x} 不为空")
    old_dispatch = struct.unpack_from("<I", data, DISPATCH_OFF)[0]
    if old_dispatch != RETURN_RAM:
        raise ValueError(
            f"FF5B 原分发项异常: {old_dispatch:#x} != {RETURN_RAM:#x}")
    if data[LENGTH_OFF] != 4:
        raise ValueError(f"FF5B 原长度异常: {data[LENGTH_OFF]}")

    if handler:
        data[HANDLER_OFF:HANDLER_OFF + len(handler)] = handler
        struct.pack_into("<I", data, DISPATCH_OFF, HANDLER_RAM)
    # 当前哨兵把原地的 4-byte FF20 替换为 4-byte FF5B。copy/noop/default
    # 三种模式必须使用同一个记录长度，否则所谓 no-op 对照会额外跳过后面
    # 8 字节脚本，测试到的是 VM 错位而不是 handler 行为。
    record_length = 4
    data[LENGTH_OFF] = record_length
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)

    reread = destination.read_bytes()
    assert reread[HANDLER_OFF:HANDLER_OFF + len(handler)] == handler
    expected_dispatch = HANDLER_RAM if handler else RETURN_RAM
    assert struct.unpack_from("<I", reread, DISPATCH_OFF)[0] == expected_dispatch
    assert reread[LENGTH_OFF] == record_length
    return handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-size", type=int, default=DEFAULT_CORE)
    parser.add_argument("--mode", choices=("copy", "noop", "default"),
                        default="copy")
    parser.add_argument("--source", type=Path, default=ROOT / "extrac/ADV.BIN")
    parser.add_argument(
        "--output", type=Path, default=HERE / "out/ADV.dynamic-font.BIN")
    args = parser.parse_args()
    handler = patch(args.source.resolve(), args.output.resolve(),
                    args.core_size, args.mode)
    print(f"✅ ADV FF5B handler ({args.mode}): {len(handler)} bytes @ {HANDLER_RAM:#x}")
    print(f"   CORE={args.core_size}, swap RAM={0x801E0000 + args.core_size * 32:#x}")
    dispatch = HANDLER_RAM if handler else RETURN_RAM
    record_length = 4
    print(f"   dispatch {DISPATCH_RAM:#x} -> {dispatch:#x}, record length={record_length}")
    print(f"   输出: {args.output.resolve()}")


if __name__ == "__main__":
    main()
