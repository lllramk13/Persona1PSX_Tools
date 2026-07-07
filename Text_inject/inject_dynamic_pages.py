#!/usr/bin/env python3
"""把动态字形页和 FF5B 触发点原地写入 E0，绝不移动 section。

安全触发点定义：section 固定前缀 [0, FIXED_PREFIX) 内的指针所指向的
4-byte ``FF 20 00 00``。这些是主入口/事件入口；复杂 section 可能有多个，
全部替换可保证不同事件路径都在显示文本前换页。重复 memcpy 是安全的。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

try:
    from .ebin_rebuild import EFile, FIXED_PREFIX, pointer_to_file
except ImportError:
    from ebin_rebuild import EFile, FIXED_PREFIX, pointer_to_file


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FF20 = b"\xFF\x20\x00\x00"


def entry_triggers(section) -> list[dict]:
    """返回固定前缀指针引用的唯一 FF20 入口。"""
    refs: dict[int, list[int]] = {}
    for site, target in section.ptr_sites:
        if site < FIXED_PREFIX and section.raw[target:target + 4] == FF20:
            refs.setdefault(target, []).append(site)
    return [
        {"offset": target, "pointer_sites": sorted(sites)}
        for target, sites in sorted(refs.items())
    ]


def inject(source: Path, manifest_path: Path, output: Path,
           pages_root: Path | None = None) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest["physical_slots"] != 2048 or manifest["glyph_bytes"] != 32:
        raise ValueError("manifest 字库规格不是 P1 2048×32")
    if manifest.get("allocation", {}).get("strategy") != "section_padding_constrained":
        raise ValueError("拒绝注入未经过 section padding 约束的 manifest")

    pages_root = pages_root or manifest_path.parent
    ef = EFile(source)
    page_table = manifest["pages"]["E0"]
    sections = [bytearray(ef.data[a:b]) for a, b in ef.sections]
    report_sections = []

    for key, page in sorted(page_table.items(), key=lambda item: int(item[0])):
        index = int(key)
        if index >= len(sections):
            raise ValueError(f"manifest section {index} 超出 E0")
        if page["count"] == 0:
            continue
        raw = sections[index]
        model = ef.section(index)
        logical_end = pointer_to_file(4, struct.unpack_from("<I", raw, 4)[0])
        page_offset = page["page_offset"]
        if page_offset > 0xFFFF:
            raise ValueError(
                f"E0 section {index}: 页包偏移 {page_offset:#x} 放不进 FF5B u16")
        pack = (pages_root / page["pack"]).read_bytes()
        if len(pack) != page["count"] * manifest["glyph_bytes"]:
            raise ValueError(f"E0 section {index}: 页包长度与 manifest 不符")
        if hashlib.sha256(pack).hexdigest() != page["sha256"]:
            raise ValueError(f"E0 section {index}: 页包 sha256 不符")
        payload = struct.pack("<I", len(pack)) + pack
        if page_offset + len(payload) > len(raw):
            raise ValueError(
                f"E0 section {index}: 页包需 {len(payload)} bytes，"
                f"连续零区仅 {page['padding_bytes']}")
        if any(raw[page_offset:page_offset + len(payload)]):
            raise ValueError(f"E0 section {index}: padding 目标区不是全零")

        triggers = entry_triggers(model)
        if not triggers:
            raise ValueError(f"E0 section {index}: 有换页字但找不到安全 FF20 入口")
        record = struct.pack("<BBH", 0xFF, 0x5B, page_offset)
        for trigger in triggers:
            at = trigger["offset"]
            if raw[at:at + 4] != FF20:
                raise AssertionError(f"E0 section {index} +{at:#x}: FF20 已变化")
            raw[at:at + 4] = record
        raw[page_offset:page_offset + len(payload)] = payload
        # header[1] 必须仍指向游戏原逻辑终点，页包只是旁载数据。
        if pointer_to_file(4, struct.unpack_from("<I", raw, 4)[0]) != logical_end:
            raise AssertionError(f"E0 section {index}: 逻辑终点被修改")
        report_sections.append({
            "section": index,
            "page_offset": page_offset,
            "page_chars": page["chars"],
            "page_glyphs": page["count"],
            "page_bytes": len(payload),
            "padding_bytes": page["padding_bytes"],
            "triggers": triggers,
        })

    head = ef.data[:0x800]
    result = head + b"".join(bytes(raw) for raw in sections)
    if len(result) != len(ef.data):
        raise AssertionError("注入后 E0 大小改变")
    # 所有 section 起点/终点必须逐项不变。
    verified = EFile.__new__(EFile)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    reread = EFile(output)
    if reread.sections != ef.sections:
        raise AssertionError("注入后 section 目录改变")
    if result[:0x800] != ef.data[:0x800]:
        raise AssertionError("注入后 E0 扇区目录改变")

    report = {
        "schema": 1,
        "source": str(source),
        "output": str(output),
        "manifest": str(manifest_path),
        "source_size": len(ef.data),
        "output_size": len(result),
        "section_count": len(ef.sections),
        "patched_sections": len(report_sections),
        "trigger_count": sum(len(row["triggers"]) for row in report_sections),
        "sections": report_sections,
        "sha256": hashlib.sha256(result).hexdigest(),
    }
    report_path = output.with_suffix(output.suffix + ".dynamic-report.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source", type=Path,
        default=HERE / "out/E0.dynamic-sentinel.prepage.BIN")
    parser.add_argument(
        "--manifest", type=Path,
        default=HERE / "out/dynamic_font_constrained/manifest.json")
    parser.add_argument(
        "--output", type=Path,
        default=HERE / "out/E0.dynamic-pages-inplace.BIN")
    args = parser.parse_args()
    report = inject(args.source.resolve(), args.manifest.resolve(),
                    args.output.resolve())
    print(
        f"✅ E0 动态页原地注入: {report['patched_sections']} sections / "
        f"{report['trigger_count']} FF5B triggers")
    print(f"   大小不变: {report['source_size']} bytes")
    print(f"   输出: {report['output']}")


if __name__ == "__main__":
    main()
