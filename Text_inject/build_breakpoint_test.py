#!/usr/bin/env python3
"""只注入已人工对齐 breakpoint 的小批 E0 译文并生成测试盘。"""
from __future__ import annotations

import json
from pathlib import Path

from ebin_rebuild import EFile
from unit_encode import encode_span_units
import build_disc


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BATCH = HERE / "translations" / "e0_breakpoint_test.json"
TRANSLATIONS = ROOT / "Text" / "translations.json"
CODETABLE = ROOT / "Codetable" / "codetable_zh.json"
OUT = HERE / "out" / "E0.patched.BIN"
PUNCT_FIX = str.maketrans({":": "：", "?": "？"})  # TEMP：当前重排式测试码表


def main() -> None:
    batch = json.loads(BATCH.read_text(encoding="utf-8"))
    translations = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
    ef = EFile(ROOT / "extrac" / "ADV" / "E0.BIN")
    replacements: dict[int, dict[int, bytes]] = {}

    for line_id, item in batch.items():
        prefix = "ADV/E0.BIN#"
        if not line_id.startswith(prefix):
            raise ValueError(f"不是 E0 ID: {line_id}")
        section, span_index = map(int, line_id[len(prefix):].split(":"))
        zh = translations[line_id]["zh"] if item.get("from_translations") else item["zh"]
        zh = zh.translate(PUNCT_FIX)
        encoded = encode_span_units(ef, section, span_index, zh, CODETABLE)
        section_replacements = replacements.setdefault(section, {})
        overlap = set(section_replacements) & set(encoded)
        if overlap:
            raise ValueError(f"{line_id}: unit 重复替换 {sorted(map(hex, overlap))}")
        section_replacements.update(encoded)
        print(f"  {line_id}: {len(encoded)} units")

    rebuilt = ef.rebuild(replacements)
    if len(rebuilt) != len(ef.data):
        raise ValueError(
            f"E0 大小变化 {len(ef.data)} -> {len(rebuilt)}；当前测试盘只支持等长文件")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_bytes(rebuilt)
    print(f"✅ E0 测试补丁: {OUT} ({len(rebuilt)} bytes)")
    build_disc.build_test_disc()


if __name__ == "__main__":
    main()
