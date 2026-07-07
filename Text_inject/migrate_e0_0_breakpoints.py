#!/usr/bin/env python3
"""按修正后的 section-file/RAM 坐标重建 E0S0 的 45 个 breakpoint。"""
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
LINE_ID = "ADV/E0.BIN#0:0"
BREAK_RE = re.compile(r"⟪B\d+⟫")


def corrected_source() -> tuple[str, list[str]]:
    ef = EFile(ROOT / "extrac" / "ADV" / "E0.BIN")
    sec = ef.section(0)
    start, end = ef.sec_spans[0][0]
    ctrl = dump.load_format()["ctrl"]["efile"]
    pairs = decode.decode_with_offsets(sec.raw, start, end, ctrl)
    token_at = {offset: index for index, (offset, _token) in enumerate(pairs)}
    targets = sorted(t for t in sec.text_targets if start < t < end)
    break_before = {
        token_at[target]: [f"⟪B{number}⟫"]
        for number, target in enumerate(targets)
    }
    _jp, masked, _codes = encode.tokens_to_masked(
        [token for _offset, token in pairs], dump.load_codetable(), ctrl,
        break_before)
    return masked, [f"⟪B{number}⟫" for number in range(len(targets))]


def move_markers(source: str, zh: str, markers: list[str]) -> str:
    """每个新边界都在上一个 close 之后的句首；用唯一控制码占位符锚定。"""
    out = BREAK_RE.sub("", zh)
    for marker in markers:
        pos = source.index(marker)
        left = re.search(r"⟦(\d+)⟧[^⟦]*$", source[:pos])
        if left is None:
            raise ValueError(f"{marker}: 找不到左侧控制码锚点")
        anchor = left.group(0).split("\u27e7", 1)[0] + "⟧"
        if out.count(anchor) != 1:
            raise ValueError(f"{marker}: 锚点 {anchor} 在译文中不唯一")
        out = out.replace(anchor, anchor + marker)
    return out


def main() -> None:
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    item = table[LINE_ID]
    source, markers = corrected_source()
    if BREAK_RE.sub("", item["jp"]) != BREAK_RE.sub("", source):
        raise ValueError("修正坐标后的日文本体与翻译表不一致")
    zh = move_markers(source, item["zh"], markers)
    numbers = [int(n) for n in re.findall(r"⟪B(\d+)⟫", zh)]
    if numbers != list(range(len(markers))):
        raise ValueError(f"breakpoint 顺序错误: {numbers}")
    table[LINE_ID] = {"jp": source, "zh": zh}
    TABLE.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    print(f"✅ {LINE_ID}: {len(markers)} 个 breakpoint 已全部移到真实句首")


if __name__ == "__main__":
    main()
