#!/usr/bin/env python3
"""把 E0 各事件入口的 FF20 原地替换为 FF5B(page_id)。"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

try:
    from .ebin_rebuild import EFile, FIXED_PREFIX
except ImportError:
    from ebin_rebuild import EFile, FIXED_PREFIX


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FF20 = b"\xFF\x20\x00\x00"


def entry_triggers(section) -> list[dict]:
    refs: dict[int, list[int]] = {}
    for site, target in section.ptr_sites:
        if site < FIXED_PREFIX and section.raw[target:target + 4] == FF20:
            refs.setdefault(target, []).append(site)
    return [
        {"offset": target, "pointer_sites": sorted(sites)}
        for target, sites in sorted(refs.items())
    ]


def inject(source: Path, manifest_path: Path, output: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("mode") != "resident_glyph_bank":
        raise ValueError("不是 resident_glyph_bank manifest")
    ef = EFile(source)
    sections = [bytearray(ef.data[a:b]) for a, b in ef.sections]
    rows = []

    for key, page in sorted(manifest["pages"]["E0"].items(),
                            key=lambda item: int(item[0])):
        section_id = int(key)
        if page["count"] == 0:
            continue
        model = ef.section(section_id)
        triggers = entry_triggers(model)
        if not triggers:
            raise ValueError(
                f"E0 section {section_id}: {page['count']}个换页字但无安全FF20入口")
        record = struct.pack("<BBH", 0xFF, 0x5B, section_id)
        for trigger in triggers:
            at = trigger["offset"]
            if sections[section_id][at:at + 4] != FF20:
                raise AssertionError(
                    f"E0 section {section_id} +{at:#x}: 入口不是FF20")
            sections[section_id][at:at + 4] = record
        rows.append({
            "section": section_id,
            "page_chars": page["chars"],
            "page_glyphs": page["count"],
            "triggers": triggers,
        })

    result = ef.data[:0x800] + b"".join(map(bytes, sections))
    if len(result) != len(ef.data):
        raise AssertionError("触发点注入改变了 E0 大小")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(result)
    reread = EFile(output)
    if reread.sections != ef.sections or result[:0x800] != ef.data[:0x800]:
        raise AssertionError("触发点注入改变了 section 目录")

    report = {
        "schema": 1,
        "mode": "resident_glyph_bank_triggers",
        "source": str(source),
        "output": str(output),
        "manifest": str(manifest_path),
        "size": len(result),
        "patched_sections": len(rows),
        "trigger_count": sum(len(row["triggers"]) for row in rows),
        "sections": rows,
        "sha256": hashlib.sha256(result).hexdigest(),
    }
    output.with_suffix(output.suffix + ".trigger-report.json").write_text(
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
        default=HERE / "out/dynamic_font_bank/manifest.json")
    parser.add_argument(
        "--output", type=Path,
        default=HERE / "out/E0.dynamic-bank-triggers.BIN")
    args = parser.parse_args()
    report = inject(args.source.resolve(), args.manifest.resolve(),
                    args.output.resolve())
    print(
        f"✅ E0 bank触发点: {report['patched_sections']} sections / "
        f"{report['trigger_count']} FF5B")
    print(f"   E0大小与目录不变: {report['size']} bytes")


if __name__ == "__main__":
    main()
