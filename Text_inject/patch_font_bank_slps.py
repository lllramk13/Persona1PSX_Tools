#!/usr/bin/env python3
"""把 FONT.BIN 加载目标从0x801E0000下移到字形银行起点。"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

try:
    from .patch_dynamic_font_adv import addiu, jump, lui, ori
except ImportError:
    from patch_dynamic_font_adv import addiu, jump, lui, ori


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TEXT_RAM = 0x80010000
FILE_PAYLOAD = 0x800
CALLSITE_OFF = 0x1BE0
JAL_OFF = 0x1BE8
DELAY_OFF = 0x1BEC
# file+0x3F438 起有 4208-byte 内部零洞；运行时 RAM 存档确认其前后正文
# 确实被加载。不要使用 EXE 末尾零填充（DuckStation 不为其建立可执行块）。
STUB_OFF = 0x3F440
STUB_RAM = TEXT_RAM + STUB_OFF - FILE_PAYLOAD
RETURN_RAM = TEXT_RAM + JAL_OFF - FILE_PAYLOAD


def patch(source: Path, manifest_path: Path, output: Path) -> bytes:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("mode") != "resident_glyph_bank":
        raise ValueError("不是 resident_glyph_bank manifest")
    bank_base = int(manifest["bank_base"], 16)
    data = bytearray(source.read_bytes())

    expected = struct.pack(
        "<4I",
        lui("a0", 0x8001),
        addiu("a0", "a0", 0x00B4),
        struct.unpack_from("<I", data, JAL_OFF)[0],
        lui("a1", 0x801E),
    )
    if data[CALLSITE_OFF:CALLSITE_OFF + 16] != expected:
        raise ValueError("FONT加载调用点与预期不符")

    stub_words = [
        lui("a0", 0x8001),
        addiu("a0", "a0", 0x00B4),
        lui("a1", bank_base >> 16),
        ori("a1", "a1", bank_base & 0xFFFF),
        jump(RETURN_RAM),
        0,
    ]
    stub = struct.pack("<6I", *stub_words)
    if any(data[STUB_OFF:STUB_OFF + len(stub)]):
        raise ValueError(f"SLPS stub区 file+{STUB_OFF:#x} 不为空")

    # 跳到stub设置a0/a1，再回原jal；原jal延迟槽必须清掉，避免重置a1。
    struct.pack_into("<2I", data, CALLSITE_OFF, jump(STUB_RAM), 0)
    struct.pack_into("<I", data, DELAY_OFF, 0)
    data[STUB_OFF:STUB_OFF + len(stub)] = stub
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return stub


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", type=Path, default=ROOT / "extrac/SLPS_005.00")
    parser.add_argument(
        "--manifest", type=Path,
        default=HERE / "out/dynamic_font_bank/manifest.json")
    parser.add_argument(
        "--output", type=Path, default=HERE / "out/SLPS.font-bank.BIN")
    args = parser.parse_args()
    stub = patch(args.source.resolve(), args.manifest.resolve(),
                 args.output.resolve())
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    print(f"✅ SLPS FONT加载目标: {manifest['bank_base']}")
    print(f"   stub {len(stub)} bytes @ {STUB_RAM:#x}, return {RETURN_RAM:#x}")


if __name__ == "__main__":
    main()
