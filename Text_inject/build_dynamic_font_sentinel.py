#!/usr/bin/env python3
"""构建 E0 section 0 动态字库哨兵盘。

只验证一条链路：FF5B 记录 -> ADV handler -> section 内字形包 -> FONT 换页区。
这不是全量汉化构建器；验证通过后再把同一布局推广到所有 section。
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import build_expanded_disc
from dynamic_font import DEFAULT_CORE, build as build_font_pages
from ebin_rebuild import (EFile, LOAD_SKIP, RAM, SECTOR, Section,
                          pointer_to_file)
from unit_encode import encode_span_units


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "out"
DYNAMIC = OUT / "dynamic_font"
TRANSLATIONS = ROOT / "Text" / "translations.json"
SOURCE_E0 = ROOT / "extrac" / "ADV" / "E0.BIN"
SECTION = 0
SPAN = 0


def section_codetable(manifest: dict, section: int, destination: Path) -> Path:
    table = {
        str(slot): (manifest["core_chars"][slot]
                    if slot < manifest["core_size"] else "")
        for slot in range(manifest["physical_slots"])
    }
    page = manifest["pages"]["E0"][str(section)]
    for index, char in enumerate(page["chars"]):
        table[str(page["first_slot"] + index)] = char
    destination.write_text(
        json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


def translated_opening() -> str:
    item = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))[
        "ADV/E0.BIN#0:0"]
    zh = item["zh"].translate(str.maketrans({
        ":": "：", "?": "？", "<": "【", ">": "】",
    }))
    # 与已经实机通过的 smoke 文本保持一致，避免英文/注音干扰哨兵结果。
    return (zh.replace("Ayase", "绫濑").replace("エリー", "艾莉")
            .replace("PERSONA大人", "人格面具大人")
            .replace("Persona大人", "人格面具大人")
            .replace("我也Bet⟦23⟧Brown", "我也押⟦23⟧布朗")
            .replace("Fantastic!", "太棒了!")
            .replace("Brown", "布朗")
            .replace("⟦155⟧稻叶正男(いなばまさお)",
                     "⟦155⟧稻叶正男"))


def add_page_record(raw: bytes, spans: list[tuple[int, int]], pack: bytes,
                    *, install_record: bool = True):
    del spans
    record_at = 0x12D0
    if raw[record_at:record_at + 4] != b"\xff\x20\0\0":
        raise ValueError("section 0 入口 FF20 与预期不符")

    # 页包放在逻辑终点之外，但仍位于扇区化 section 内；原脚本一个字节也不移动。
    logical_end = pointer_to_file(4, struct.unpack_from("<I", raw, 4)[0])
    if not (record_at < logical_end <= len(raw)):
        raise ValueError(
            f"section 逻辑终点异常: record={record_at:#x}, end={logical_end:#x}")
    if len(pack) % 4:
        raise ValueError("字形包长度必须是 4 的倍数")

    body = bytearray(raw[:logical_end])
    while len(body) % 4:
        body.append(0)
    pack_at = len(body)
    if pack_at > 0xFFFF:
        raise ValueError("页包偏移装不进 FF5B u16")
    body.extend(struct.pack("<I", len(pack)))
    body.extend(pack)
    new_logical_end = len(body)
    # 字形包是旁载数据，不属于游戏原有的逻辑结构。header[1] 必须继续指向
    # old logical_end；若把它扩到页包后面，section 卸载/切换代码可能把字模
    # 当结构解析。物理加载范围是否覆盖页包要由断点另行确认。
    if install_record:
        struct.pack_into("<BBH", body, record_at, 0xFF, 0x5B, pack_at)

    padded = bytes(body) + b"\0" * ((-len(body)) % SECTOR)
    if padded[pack_at + 4:pack_at + 4 + len(pack)] != pack:
        raise AssertionError("页包回读失败")
    if pointer_to_file(4, struct.unpack_from("<I", padded, 4)[0]) != logical_end:
        raise AssertionError("section 原逻辑结束指针被意外修改")
    return padded, {
        "record": record_at,
        "pack": pack_at,
        "pack_bytes": len(pack),
        "old_end": logical_end,
        "new_end": new_logical_end,
        "structure_end": logical_end,
        "record_installed": install_record,
    }


def replace_section(ef: EFile, index: int, replacement: bytes) -> bytes:
    sections = [ef.data[a:b] for a, b in ef.sections]
    sections[index] = replacement
    head = bytearray(ef.data[:SECTOR])
    sector_pos = 1
    for i, body in enumerate(sections):
        if len(body) % SECTOR:
            raise ValueError(f"section {i} 未按扇区对齐")
        struct.pack_into("<H", head, i * 2, sector_pos)
        sector_pos += len(body) // SECTOR
    struct.pack_into("<H", head, len(sections) * 2, sector_pos)
    return bytes(head) + b"".join(sections)


def build(output_e0: Path, core_size: int, *, install_record: bool = True,
          sentinel_glyphs: int | None = None) -> tuple[Path, dict]:
    manifest = build_font_pages(DYNAMIC, core_size, TRANSLATIONS)
    codetable = section_codetable(
        manifest, SECTION, DYNAMIC / "codetable_E0_section_000.json")

    original = EFile(SOURCE_E0)
    replacements = encode_span_units(
        original, SECTION, SPAN, translated_opening(), codetable)
    translated = original.rebuild({SECTION: replacements})
    prepage = OUT / "E0.dynamic-sentinel.prepage.BIN"
    prepage.write_bytes(translated)

    staged = EFile(prepage)
    a, b = staged.sections[SECTION]
    page_info = manifest["pages"]["E0"][str(SECTION)]
    pack = (DYNAMIC / page_info["pack"]).read_bytes()
    page_chars = page_info["chars"]
    if sentinel_glyphs is not None:
        if not 0 <= sentinel_glyphs <= len(page_chars):
            raise ValueError(
                f"sentinel glyphs {sentinel_glyphs} 超出 0..{len(page_chars)}")
        pack = pack[:sentinel_glyphs * 32]
        page_chars = page_chars[:sentinel_glyphs]
    new_section, layout = add_page_record(
        staged.data[a:b], staged.sec_spans[SECTION], pack,
        install_record=install_record)
    result = replace_section(staged, SECTION, new_section)
    output_e0.parent.mkdir(parents=True, exist_ok=True)
    output_e0.write_bytes(result)

    # 重新解析是最重要的静态门槛：目录、span 和内部指针至少仍自洽。
    verified = EFile(output_e0)
    if len(verified.sections) != len(original.sections):
        raise AssertionError("section 数量改变")
    if verified.data[verified.sections[SECTION][0] + layout["pack"] + 4:
                     verified.sections[SECTION][0] + layout["pack"] + 4
                     + len(pack)] != pack:
        raise AssertionError("最终 E0 页包位置错误")
    return output_e0, {**layout, "page_chars": page_chars}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-size", type=int, default=DEFAULT_CORE)
    parser.add_argument(
        "--e0-output", type=Path,
        default=OUT / "E0.dynamic-sentinel.BIN")
    parser.add_argument(
        "--disc-output", type=Path,
        default=OUT / "Persona-ZH-dynamic-font-sentinel.bin")
    parser.add_argument("--no-disc", action="store_true")
    parser.add_argument(
        "--keep-ff20", action="store_true",
        help="只追加旁载页包，保留原 FF20（section 扩容对照）")
    parser.add_argument(
        "--sentinel-glyphs", type=int,
        help="只携带前 N 个换页字；用于不增加 section 扇区的最小哨兵")
    args = parser.parse_args()

    e0, layout = build(args.e0_output.resolve(), args.core_size,
                       install_record=not args.keep_ff20,
                       sentinel_glyphs=args.sentinel_glyphs)
    record_name = "FF5B" if layout["record_installed"] else "原 FF20"
    print(
        f"✅ E0 动态页哨兵: {record_name} +{layout['record']:#x}, "
        f"pack +{layout['pack']:#x} / {layout['pack_bytes']} bytes")
    print(f"   section 0 换页字: {layout['page_chars']}")
    print(f"   E0: {SOURCE_E0.stat().st_size} -> {e0.stat().st_size} bytes")
    if not args.no_disc:
        build_expanded_disc.build(
            args.disc_output.resolve(), e0,
            font_path=DYNAMIC / "FONT.dynamic.base.BIN",
            adv_path=OUT / "ADV.dynamic-font.BIN",
            patch_slps=False)


if __name__ == "__main__":
    main()
