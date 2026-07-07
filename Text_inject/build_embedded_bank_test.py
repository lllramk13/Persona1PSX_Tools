#!/usr/bin/env python3
"""构建 CORE=1920、字形银行嵌入 ADV 的开场实机测试盘。"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import build_disc
import build_expanded_disc
from build_dynamic_font_sentinel import section_codetable, translated_opening
from ebin_rebuild import EFile
from inject_dynamic_bank_triggers import inject as inject_triggers
from unit_encode import encode_span_units


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SOURCE_E0 = ROOT / "extrac/ADV/E0.BIN"


def build(output: Path, manifest_path: Path) -> Path:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    work = HERE / "out"
    codetable = section_codetable(
        manifest, 0, work / "dynamic_font_embedded/codetable_E0_section_000.json")
    original = EFile(SOURCE_E0)
    replacements0 = encode_span_units(
        original, 0, 0, translated_opening(), codetable)
    codetable3 = section_codetable(
        manifest, 3, work / "dynamic_font_embedded/codetable_E0_section_003.json")
    table = json.loads((ROOT / "Text/translations.json").read_text(encoding="utf-8"))
    zh3 = table["ADV/E0.BIN#3:0"]["zh"].translate(str.maketrans({
        ":": "：", "?": "？", "<": "【", ">": "】",
    }))
    # 正式译文使这个原本无尾部padding的section多8字节。实机验证盘删掉
    # 不影响剧情的罗马字注音，保持section物理大小与后续起点不变。
    zh3 = zh3.replace("吉野夏美（Yoshino Natsumi）", "吉野夏美")
    replacements3 = encode_span_units(original, 3, 0, zh3, codetable3)
    translated = original.rebuild({0: replacements0, 3: replacements3})
    if len(translated) != len(original.data):
        raise ValueError(
            f"section0测试文本导致E0变长: {len(original.data)} -> {len(translated)}")
    pretrigger = work / "E0.embedded-bank.pretrigger.BIN"
    pretrigger.write_bytes(translated)
    e0 = work / "E0.embedded-bank.BIN"
    inject_triggers(pretrigger, manifest_path, e0)

    source_disc = build_disc.DISC_SOURCE
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_disc, output)
    build_expanded_disc.apply_fixed_patch(
        output, "FONT.BIN", 602, ROOT / "extrac/FONT.BIN",
        manifest_path.parent / manifest["core_font"])
    build_expanded_disc.apply_fixed_patch(
        output, "ADV.BIN", 635, ROOT / "extrac/ADV.BIN",
        work / "ADV.dynamic-font-embedded.BIN")
    build_expanded_disc.apply_fixed_patch(
        output, "ADV/E0.BIN", 87074, SOURCE_E0, e0)
    if output.stat().st_size != source_disc.stat().st_size:
        raise AssertionError("嵌入式测试盘大小改变")
    print(f"✅ 嵌入式银行测试盘: {output}")
    print("   SLPS/FONT加载流程保持原版；FONT/ADV/E0均等长")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest", type=Path,
        default=HERE / "out/dynamic_font_embedded/manifest.json")
    parser.add_argument(
        "--output", type=Path,
        default=HERE / "out/Persona-dyn-embedded-bank.bin")
    args = parser.parse_args()
    build(args.output.resolve(), args.manifest.resolve())


if __name__ == "__main__":
    main()
