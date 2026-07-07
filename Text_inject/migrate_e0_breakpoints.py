#!/usr/bin/env python3
"""按修正后的 E 段坐标，为所有已翻译 E0 span 生成 breakpoint。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "Text"), str(ROOT / "Text_inject")]

import decode  # noqa: E402
import dump  # noqa: E402
import encode  # noqa: E402
from ebin_rebuild import EFile  # noqa: E402


TABLE = ROOT / "Text" / "translations.json"
REPORT = ROOT / "Text" / "e0_breakpoint_migration.json"
BREAK_RE = re.compile(r"⟪B\d+⟫")
CTRL_LEFT_RE = re.compile(r"⟦(\d+)⟧([^\u27e6]*)$")


def source_for_span(ef, section, span_index, ctrl, codetable):
    sec = ef.section(section)
    start, end = ef.sec_spans[section][span_index]
    pairs = decode.decode_with_offsets(sec.raw, start, end, ctrl)
    token_at = {offset: index for index, (offset, _token) in enumerate(pairs)}
    targets = sorted(t for t in sec.text_targets if start < t < end)
    nonexact = [target for target in targets if target not in token_at]
    if nonexact:
        return None, targets, nonexact
    break_before = {
        token_at[target]: [f"⟪B{number}⟫"]
        for number, target in enumerate(targets)
    }
    _jp, masked, _codes = encode.tokens_to_masked(
        [token for _offset, token in pairs], codetable, ctrl, break_before)
    return masked, targets, []


def place_markers(source: str, zh: str, count: int) -> str:
    out = BREAK_RE.sub("", zh)
    for number in range(count):
        marker = f"⟪B{number}⟫"
        pos = source.index(marker)
        left = CTRL_LEFT_RE.search(source[:pos])
        if left is None or left.group(2).strip():
            raise ValueError(f"{marker}: 边界不在控制码后的句首")
        anchor = f"⟦{left.group(1)}⟧"
        if out.count(anchor) != 1:
            raise ValueError(f"{marker}: 锚点 {anchor} 在译文中不唯一")
        out = out.replace(anchor, anchor + marker)
    return out


def main() -> None:
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    ef = EFile(ROOT / "extrac" / "ADV" / "E0.BIN")
    ctrl = dump.load_format()["ctrl"]["efile"]
    codetable = dump.load_codetable()
    migrated = 0
    breakpoint_count = 0
    skipped = []

    for section, spans in ef.sec_spans.items():
        for span_index, _span in enumerate(spans):
            line_id = f"ADV/E0.BIN#{section}:{span_index}"
            item = table.get(line_id)
            if not item or not item.get("zh"):
                continue
            source, targets, nonexact = source_for_span(
                ef, section, span_index, ctrl, codetable)
            if nonexact:
                skipped.append({
                    "id": line_id,
                    "reason": "target_inside_token",
                    "targets": [f"0x{target:X}" for target in nonexact],
                })
                continue
            if BREAK_RE.sub("", item["jp"]) != BREAK_RE.sub("", source):
                skipped.append({"id": line_id, "reason": "source_mismatch"})
                continue
            try:
                zh = place_markers(source, item["zh"], len(targets))
            except ValueError as exc:
                skipped.append({"id": line_id, "reason": str(exc)})
                continue
            numbers = [int(n) for n in re.findall(r"⟪B(\d+)⟫", zh)]
            if numbers != list(range(len(targets))):
                raise ValueError(f"{line_id}: breakpoint 顺序错误")
            table[line_id] = {"jp": source, "zh": zh}
            migrated += 1
            breakpoint_count += len(targets)

    TABLE.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    REPORT.write_text(json.dumps({
        "migrated": migrated,
        "breakpoints": breakpoint_count,
        "skipped": skipped,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"✅ E0 自动迁移 {migrated} 块 / {breakpoint_count} 个 breakpoint")
    print(f"⚠ 跳过 {len(skipped)} 块：{', '.join(row['id'] for row in skipped)}")


if __name__ == "__main__":
    main()
