#!/usr/bin/env python3
"""构建 E0 开场测试盘，或产出待重定位的完整 E0 扩展文件。"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from ebin_rebuild import EFile
from unit_encode import encode_span_units
import build_disc


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TRANSLATIONS = ROOT / "Text" / "translations.json"
CODETABLE = ROOT / "Codetable" / "codetable_zh.json"
OUT = HERE / "out" / "E0.patched.BIN"
FULL_OUT = HERE / "out" / "E0.full.patched.BIN"
SAFE_OUT = HERE / "out" / "E0.safe.patched.BIN"
SLPS_OUT = HERE / "out" / "SLPS.patched.BIN"
SLPS_PATCH = HERE / "translations" / "slps_choice_test.json"
SMOKE_IDS = {"ADV/E0.BIN#0:0"}  # 已实机验证且不会撑大 E0 的开场块
PUNCT_FIX = str.maketrans({
    ":": "：", "?": "？", "<": "【", ">": "】",
})  # TEMP：当前重排式测试码表

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scope", choices=("smoke", "safe", "full"), default="smoke",
        help=("smoke=可玩开场测试盘；safe=排除含额外内部指针的 span；"
              "full=全部 262 块（逆向未完，仅供分析）"))
    parser.add_argument(
        "--disc-output", default=str(HERE / "out" / "Persona-ZH-smoke-test.bin"),
        help="smoke 测试盘输出路径")
    parser.add_argument(
        "--exclude-section", action="append", type=int, default=[],
        help="调试用：不注入指定 E0 section，可重复传入")
    parser.add_argument(
        "--artifact", help="safe/full E0 的自定义输出路径")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    translations = json.loads(TRANSLATIONS.read_text(encoding="utf-8"))
    ef = EFile(ROOT / "extrac" / "ADV" / "E0.BIN")
    replacements: dict[int, dict[int, bytes]] = {}

    included = 0
    skipped = []
    for line_id, item in translations.items():
        prefix = "ADV/E0.BIN#"
        if not line_id.startswith(prefix) or not item.get("zh"):
            continue
        if args.scope == "smoke" and line_id not in SMOKE_IDS:
            continue
        section, span_index = map(int, line_id[len(prefix):].split(":"))
        if section in args.exclude_section:
            skipped.append(line_id)
            continue
        span_start, span_end = ef.sec_spans[section][span_index]
        sec = ef.section(section)
        if args.scope == "safe":
            inside_targets = {
                target for _site, target in sec.ptr_sites
                if span_start <= target < span_end
            }
            if inside_targets - sec.text_targets:
                skipped.append(line_id)
                continue
        expected = sum(span_start < target < span_end
                       for target in sec.text_targets)
        marker_count = len(re.findall(r"⟪B\d+⟫", item["zh"]))
        if marker_count != expected:
            skipped.append(line_id)
            continue
        zh = item["zh"]
        zh = zh.translate(PUNCT_FIX)
        if line_id == "ADV/E0.BIN#0:0":
            # 当前测试字库征用了 ASCII 字槽；英文人名会按槽显示成汉字/假名。
            zh = zh.replace("Ayase", "绫濑").replace("エリー", "艾莉")
            zh = zh.replace("PERSONA大人", "人格面具大人")
            zh = zh.replace("Persona大人", "人格面具大人")
            zh = zh.replace("我也Bet⟦23⟧Brown", "我也押⟦23⟧布朗")
            zh = zh.replace("Fantastic!", "太棒了!")
            zh = zh.replace("Brown", "布朗")
            zh = zh.replace("⟦155⟧稻叶正男(いなばまさお)",
                            "⟦155⟧稻叶正男")
        try:
            encoded = encode_span_units(ef, section, span_index, zh, CODETABLE)
        except Exception as exc:
            raise type(exc)(f"{line_id}: {exc}") from exc
        section_replacements = replacements.setdefault(section, {})
        overlap = set(section_replacements) & set(encoded)
        if overlap:
            raise ValueError(f"{line_id}: unit 重复替换 {sorted(map(hex, overlap))}")
        section_replacements.update(encoded)
        included += 1

    print(f"  E0 纳入 {included} 个翻译 span，跳过 {len(skipped)} 个")
    if skipped:
        preview = ", ".join(skipped[:12])
        suffix = f" ... 另 {len(skipped) - 12} 个" if len(skipped) > 12 else ""
        print(f"  跳过: {preview}{suffix}")

    rebuilt = ef.rebuild(replacements)
    subprocess.run([
        sys.executable, str(ROOT / "Text" / "encode.py"),
        str(ROOT / "extrac" / "SLPS_005.00"),
        "--patch", str(SLPS_PATCH),
        "--codetable", str(CODETABLE),
        "-o", str(SLPS_OUT),
    ], check=True)
    artifact = {
        "smoke": OUT,
        "safe": SAFE_OUT,
        "full": FULL_OUT,
    }[args.scope]
    if args.artifact:
        artifact = Path(args.artifact).resolve()
    artifact.parent.mkdir(exist_ok=True)
    artifact.write_bytes(rebuilt)

    if args.scope != "smoke":
        print(
            f"✅ {args.scope} E0: {len(ef.data)} -> {len(rebuilt)} bytes；"
            f"已保存 {artifact}")
        return
    if len(rebuilt) != len(ef.data):
        raise ValueError(
            f"smoke E0 应为等长，实际 {len(ef.data)} -> {len(rebuilt)}")
    print(f"✅ E0 测试补丁: {OUT} ({len(rebuilt)} bytes)")
    build_disc.DISC_OUTPUT = Path(args.disc_output).resolve()
    build_disc.build_test_disc()


if __name__ == "__main__":
    main()
