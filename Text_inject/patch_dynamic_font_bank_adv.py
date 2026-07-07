#!/usr/bin/env python3
"""给 ADV.BIN 注入“常驻字形银行”版 FF5B handler。"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

try:
    from .patch_dynamic_font_adv import (
        ADV_BASE, DISPATCH_OFF, DISPATCH_RAM, HANDLER_OFF, HANDLER_RAM,
        LENGTH_OFF, REG, RETURN_RAM, addiu, addu, branch, i_type, jump, lhu,
        lui, lw, ori, sw,
    )
except ImportError:
    from patch_dynamic_font_adv import (
        ADV_BASE, DISPATCH_OFF, DISPATCH_RAM, HANDLER_OFF, HANDLER_RAM,
        LENGTH_OFF, REG, RETURN_RAM, addiu, addu, branch, i_type, jump, lhu,
        lui, lw, ori, sw,
    )


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# 与 patch_dynamic_font_adv.REG 编号一致；这里额外使用 v1/a3/t0。
R = {"zero": 0, "v0": 2, "v1": 3, "a0": 4, "a1": 5,
     "a2": 6, "a3": 7, "t0": 8, "s5": 21}
REG.update(R)


def sll(rd: str, rt: str, shift: int) -> int:
    return (R[rt] << 16) | (R[rd] << 11) | (shift << 6)


def make_handler(bank_base: int, glyph_base: int, core_size: int) -> bytes:
    swap_address = 0x801E0000 + core_size * 32
    words = [
        lhu("a0", 2, "s5"),             # page id
        lui("a1", bank_base >> 16),
        ori("a1", "a1", bank_base & 0xFFFF),
        sll("a0", "a0", 2),            # directory entry = id*4
        addu("a0", "a0", "a1"),
        lhu("a2", 0, "a0"),             # u16 list offset
        lhu("a3", 2, "a0"),             # u16 glyph count
        addu("a0", "a1", "a2"),        # list pointer
        0,                                # beq count,zero,done
        lui("a1", swap_address >> 16),   # delay slot: destination
        ori("a1", "a1", swap_address & 0xFFFF),
        lui("t0", glyph_base >> 16),
        ori("t0", "t0", glyph_base & 0xFFFF),
        lhu("v0", 0, "a0"),             # loop: bank glyph index
        addiu("a0", "a0", 2),
        sll("v0", "v0", 5),
        addu("v0", "v0", "t0"),        # source = glyph_base + index*32
    ]
    for offset in range(0, 32, 4):
        words.extend((lw("v1", offset, "v0"), sw("v1", offset, "a1")))
    words.extend([
        addiu("a1", "a1", 32),
        addiu("a3", "a3", -1),
        0,                                # bne count,zero,loop
        0,                                # delay nop
        jump(RETURN_RAM),
        0,
    ])
    done = len(words) - 2
    words[8] = branch(0x04, "a3", "zero", 8, done)
    # loop starts at word 13; bne is four words before the final jump/nop pair.
    bne_at = len(words) - 4
    words[bne_at] = branch(0x05, "a3", "zero", bne_at, 13)
    return struct.pack(f"<{len(words)}I", *words)


def patch(source: Path, manifest_path: Path, output: Path) -> bytes:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("mode") != "resident_glyph_bank":
        raise ValueError("不是 resident_glyph_bank manifest")
    bank_base = int(manifest["bank_base"], 16)
    glyph_base = int(manifest["glyph_base"], 16)
    handler = make_handler(bank_base, glyph_base, manifest["core_size"])

    data = bytearray(source.read_bytes())
    if any(data[HANDLER_OFF:HANDLER_OFF + len(handler)]):
        raise ValueError(f"ADV 注入区 {HANDLER_OFF:#x} 不为空")
    old_dispatch = struct.unpack_from("<I", data, DISPATCH_OFF)[0]
    if old_dispatch != RETURN_RAM:
        raise ValueError(f"FF5B 原分发项异常: {old_dispatch:#x}")
    if data[LENGTH_OFF] != 4:
        raise ValueError(f"FF5B 原长度异常: {data[LENGTH_OFF]}")
    data[HANDLER_OFF:HANDLER_OFF + len(handler)] = handler
    struct.pack_into("<I", data, DISPATCH_OFF, HANDLER_RAM)
    data[LENGTH_OFF] = 4
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "extrac/ADV.BIN")
    parser.add_argument(
        "--manifest", type=Path,
        default=HERE / "out/dynamic_font_bank/manifest.json")
    parser.add_argument(
        "--output", type=Path,
        default=HERE / "out/ADV.dynamic-font-bank.BIN")
    args = parser.parse_args()
    handler = patch(args.source.resolve(), args.manifest.resolve(),
                    args.output.resolve())
    print(f"✅ ADV resident-bank FF5B handler: {len(handler)} bytes @ {HANDLER_RAM:#x}")
    print(f"   dispatch {DISPATCH_RAM:#x}, record length=4")


if __name__ == "__main__":
    main()
