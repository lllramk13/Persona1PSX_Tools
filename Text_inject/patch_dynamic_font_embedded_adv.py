#!/usr/bin/env python3
"""把 FF5B handler、页面目录和小型字形银行一起嵌入 ADV 尾部代码洞。"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

try:
    from .patch_dynamic_font_adv import (DISPATCH_OFF, HANDLER_OFF, HANDLER_RAM,
                                         LENGTH_OFF, RETURN_RAM)
    from .patch_dynamic_font_bank_adv import make_handler
except ImportError:
    from patch_dynamic_font_adv import (DISPATCH_OFF, HANDLER_OFF, HANDLER_RAM,
                                        LENGTH_OFF, RETURN_RAM)
    from patch_dynamic_font_bank_adv import make_handler


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def align(value: int, n: int) -> int:
    return (value + n - 1) // n * n


def patch(source: Path, manifest_path: Path, output: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root = manifest_path.parent
    if manifest["core_size"] < 1920:
        raise ValueError("嵌入式银行要求 CORE>=1920 才能装进ADV代码洞")

    directory_size = manifest["directory_bytes"]
    list_size = manifest["page_index_bytes"]
    prefix = (root / manifest["expanded_font"]).read_bytes()[:manifest["prefix_size"]]
    directory = prefix[:directory_size]
    lists = prefix[directory_size:directory_size + list_size]
    glyphs = (root / "glyph_bank.BIN").read_bytes()

    # 先按占位地址求handler长度，再决定紧随其后的银行地址。
    probe = make_handler(0x80000000, 0x80000000, manifest["core_size"])
    bank_rel = align(len(probe), 16)
    bank_ram = HANDLER_RAM + bank_rel
    glyph_rel = align(directory_size + list_size, 32)
    glyph_ram = bank_ram + glyph_rel
    handler = make_handler(bank_ram, glyph_ram, manifest["core_size"])
    if len(handler) != len(probe):
        raise AssertionError("handler长度不稳定")
    blob = bytearray(bank_rel + glyph_rel + len(glyphs))
    blob[:len(handler)] = handler
    blob[bank_rel:bank_rel + directory_size] = directory
    blob[bank_rel + directory_size:bank_rel + directory_size + list_size] = lists
    blob[bank_rel + glyph_rel:bank_rel + glyph_rel + len(glyphs)] = glyphs

    data = bytearray(source.read_bytes())
    cave_size = len(data) - HANDLER_OFF
    if len(blob) > cave_size:
        raise ValueError(f"ADV代码洞 {cave_size} bytes，需 {len(blob)}")
    if any(data[HANDLER_OFF:HANDLER_OFF + len(blob)]):
        raise ValueError("ADV尾部代码洞不为空")
    if struct.unpack_from("<I", data, DISPATCH_OFF)[0] != RETURN_RAM:
        raise ValueError("FF5B原分发表项异常")
    if data[LENGTH_OFF] != 4:
        raise ValueError("FF5B原长度不是4")
    data[HANDLER_OFF:HANDLER_OFF + len(blob)] = blob
    struct.pack_into("<I", data, DISPATCH_OFF, HANDLER_RAM)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)

    report = {
        "handler_ram": f"0x{HANDLER_RAM:08X}",
        "handler_bytes": len(handler),
        "bank_ram": f"0x{bank_ram:08X}",
        "glyph_ram": f"0x{glyph_ram:08X}",
        "glyph_count": manifest["noncore_count"],
        "blob_bytes": len(blob),
        "cave_bytes": cave_size,
        "spare_bytes": cave_size - len(blob),
    }
    output.with_suffix(output.suffix + ".embedded.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT / "extrac/ADV.BIN")
    parser.add_argument(
        "--manifest", type=Path,
        default=HERE / "out/dynamic_font_embedded/manifest.json")
    parser.add_argument(
        "--output", type=Path,
        default=HERE / "out/ADV.dynamic-font-embedded.BIN")
    args = parser.parse_args()
    report = patch(args.source.resolve(), args.manifest.resolve(),
                   args.output.resolve())
    print(
        f"✅ ADV嵌入式字形银行: {report['blob_bytes']}/{report['cave_bytes']} bytes, "
        f"spare={report['spare_bytes']}")
    print(f"   handler={report['handler_ram']} bank={report['bank_ram']} glyph={report['glyph_ram']}")


if __name__ == "__main__":
    main()
