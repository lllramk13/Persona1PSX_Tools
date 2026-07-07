#!/usr/bin/env python3
"""生成“全局非核心字形银行 + 64KB核心 FONT”。

扩展 FONT 文件布局：
  [page directory][page glyph-index lists][padding][520个非核心字形][padding][64KB核心FONT]

整文件加载到 ``BANK_BASE`` 后，最后64KB恰好从原地址0x801E0000开始，
因此原版所有绘字代码无需修改。FF5B 参数是 E0 section/page id，handler
查目录并从银行挑出本段字符复制到 slot CORE..。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from PIL import ImageFont

try:
    from .build_font import TTF, render_glyph
    from .dynamic_font import (DEFAULT_CORE, GLYPH_BYTES, PHYSICAL_SLOTS,
                               PINNED, TRANSLATIONS, load_corpus,
                               ranked_chars, render_base_font, select_core)
except ImportError:
    from build_font import TTF, render_glyph
    from dynamic_font import (DEFAULT_CORE, GLYPH_BYTES, PHYSICAL_SLOTS,
                              PINNED, TRANSLATIONS, load_corpus,
                              ranked_chars, render_base_font, select_core)


HERE = Path(__file__).resolve().parent
RAM_FONT = 0x801E0000
PAGE_COUNT = 224
DIRECTORY_BYTES = PAGE_COUNT * 4
GLYPH_DATA_OFFSET = 0x800
# 游戏按 FSIZE 的2048字节对齐长度读取。前缀必须整扇区，否则最后一个
# 扇区会越过0x801F0000并覆盖游戏状态区。
PREFIX_ALIGNMENT = 0x800
# 14px Source Han 对个别密集字的横竖连接会断裂；只对实机确认有问题的
# 字提高一档，避免整体字库尺寸变化。
GLYPH_SIZE_OVERRIDES = {"赫": 16}


def align(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def build(output: Path, core_size: int, translations: Path) -> dict:
    frequency, sections, translated = load_corpus(translations)
    core = select_core(frequency, core_size)
    core_set = set(core)
    noncore = [ch for ch in ranked_chars(frequency) if ch not in core_set]
    bank_index = {ch: index for index, ch in enumerate(noncore)}
    swap_capacity = PHYSICAL_SLOTS - core_size

    if len(noncore) > 0xFFFF:
        raise ValueError("非核心字超过u16索引")
    pages = {}
    lists = bytearray()
    directory = bytearray(DIRECTORY_BYTES)
    for section in range(PAGE_COUNT):
        chars = sections.get(section, set())
        rare = sorted(chars - core_set,
                      key=lambda ch: (bank_index[ch], ord(ch)))
        if len(rare) > swap_capacity:
            raise ValueError(
                f"E0 section {section}: 换页字 {len(rare)} > {swap_capacity}")
        list_offset = DIRECTORY_BYTES + len(lists)
        if list_offset > 0xFFFF:
            raise ValueError("页面索引表偏移超过u16")
        struct.pack_into("<HH", directory, section * 4,
                         list_offset, len(rare))
        for ch in rare:
            lists.extend(struct.pack("<H", bank_index[ch]))
        pages[str(section)] = {
            "page_id": section,
            "count": len(rare),
            "chars": "".join(rare),
            "list_offset": list_offset,
            "bank_indices": [bank_index[ch] for ch in rare],
            "first_slot": core_size,
        }

    if DIRECTORY_BYTES + len(lists) > GLYPH_DATA_OFFSET:
        raise ValueError(
            f"目录+索引 {DIRECTORY_BYTES + len(lists):#x} 超过 "
            f"glyph offset {GLYPH_DATA_OFFSET:#x}")
    fonts = {14: ImageFont.truetype(str(TTF), 14)}
    for size in set(GLYPH_SIZE_OVERRIDES.values()):
        fonts[size] = ImageFont.truetype(str(TTF), size)
    glyph_bank = b"".join(
        render_glyph(ch, fonts[GLYPH_SIZE_OVERRIDES.get(ch, 14)])
        for ch in noncore)
    used_prefix = GLYPH_DATA_OFFSET + len(glyph_bank)
    prefix_size = align(used_prefix, PREFIX_ALIGNMENT)
    bank_base = RAM_FONT - prefix_size
    glyph_base = bank_base + GLYPH_DATA_OFFSET
    # 已有快照共同全零候选：0x801DAC52..0x801E0000。
    if bank_base < 0x801DAC52:
        raise ValueError(
            f"字形银行起点 {bank_base:#x} 越出已观测20.9KB空洞")

    prefix = bytearray(prefix_size)
    prefix[:DIRECTORY_BYTES] = directory
    prefix[DIRECTORY_BYTES:DIRECTORY_BYTES + len(lists)] = lists
    prefix[GLYPH_DATA_OFFSET:GLYPH_DATA_OFFSET + len(glyph_bank)] = glyph_bank

    output.mkdir(parents=True, exist_ok=True)
    core_font_path = output / "FONT.dynamic.core.BIN"
    core_font = render_base_font(core, core_font_path)
    expanded_path = output / "FONT.dynamic.bank-expanded.BIN"
    expanded = bytes(prefix) + core_font
    expanded_path.write_bytes(expanded)
    (output / "glyph_bank.BIN").write_bytes(glyph_bank)
    codetable = {
        str(slot): (core[slot] if slot < core_size else "")
        for slot in range(PHYSICAL_SLOTS)
    }
    (output / "codetable_bank_core.json").write_text(
        json.dumps(codetable, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    manifest = {
        "schema": 2,
        "mode": "resident_glyph_bank",
        "physical_slots": PHYSICAL_SLOTS,
        "glyph_bytes": GLYPH_BYTES,
        "core_size": core_size,
        "swap_capacity": swap_capacity,
        "translated_entries": translated,
        "global_unique_chars": len(frequency),
        "core_chars": "".join(core),
        "noncore_chars": "".join(noncore),
        "noncore_count": len(noncore),
        "bank_base": f"0x{bank_base:08X}",
        "glyph_base": f"0x{glyph_base:08X}",
        "directory_bytes": DIRECTORY_BYTES,
        "glyph_data_offset": GLYPH_DATA_OFFSET,
        "page_index_bytes": len(lists),
        "prefix_size": prefix_size,
        "font_ram": f"0x{RAM_FONT:08X}",
        "expanded_font": expanded_path.name,
        "expanded_font_bytes": len(expanded),
        "expanded_font_sha256": hashlib.sha256(expanded).hexdigest(),
        "core_font": core_font_path.name,
        "core_font_sha256": hashlib.sha256(core_font).hexdigest(),
        "glyph_bank_sha256": hashlib.sha256(glyph_bank).hexdigest(),
        "glyph_size_overrides": GLYPH_SIZE_OVERRIDES,
        "pages": {"E0": pages},
        "max_page": max(
            ({"section": int(s), "count": p["count"]}
             for s, p in pages.items()), key=lambda row: row["count"]),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-size", type=int, default=DEFAULT_CORE)
    parser.add_argument("--translations", type=Path, default=TRANSLATIONS)
    parser.add_argument(
        "--output", type=Path, default=HERE / "out/dynamic_font_bank")
    args = parser.parse_args()
    manifest = build(args.output.resolve(), args.core_size,
                     args.translations.resolve())
    print(
        f"✅ 常驻字形银行: core={manifest['core_size']}, "
        f"bank={manifest['noncore_count']} glyphs")
    print(
        f"   bank {manifest['bank_base']} / glyph {manifest['glyph_base']} / "
        f"prefix={manifest['prefix_size']:#x}")
    print(
        f"   max page: section {manifest['max_page']['section']} / "
        f"{manifest['max_page']['count']} glyphs")
    print(f"   expanded FONT: {manifest['expanded_font_bytes']} bytes")


if __name__ == "__main__":
    main()
