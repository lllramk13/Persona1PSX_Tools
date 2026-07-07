#!/usr/bin/env python3
"""T2 粒度桥接：带 FF55/FF58 breakpoint 的 span → unit 字节。

翻译仍保留完整 reader span 上下文，``⟪B0⟫`` 等零宽标记指出中文里的
unit 切点。编码时删除标记、逐段编码，并把结果交给 ``ebin_rebuild``。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "Text"))
import decode as D  # noqa: E402
import dump  # noqa: E402
import encode as E  # noqa: E402
from ebin_rebuild import EFile  # noqa: E402

BREAK_RE = re.compile(r"⟪B(\d+)⟫")

def decode_with_offsets(data, start, end, ctrl):
    """和 Text/decode.decode() 逐字节完全一致地走一遍，但每个 token 额外带上它在
    文件里的起始字节偏移。

    返回 [(offset, token)]，token 形式与 decode 相同：
        ("char", slot)          一个字
        ("ctrl", code, params)  一个控制码

    唯一和 decode 的区别就是那行 `off = i`：在消费这个 token **之前**记住它从哪个
    字节开始。因为 i 每次是按**真实消费的字节数**(1 / 2 / length)推进的，所以 off
    永远精确——不需要"一个字大概几字节"的估算，也就不会数偏。
    """
    return D.decode_with_offsets(data, start, end, ctrl)


def span_boundaries(ef: EFile, section: int, span_index: int) -> list[int]:
    """返回指定 reader span 内的 FF55/FF58 unit 起点（段内偏移）。"""
    start, end = ef.sec_spans[section][span_index]
    sec = ef.section(section)
    return sorted(target for target in sec.text_targets if start < target < end)


def _wrap_runs(tokens, width: int):
    """每个相邻控制码之间的可见字符按 width 自动插 FF03。"""
    out = []
    cells = 0
    for token in tokens:
        if token[0] == "ctrl":
            if token[1] == 0x03:
                continue                    # 移除原日文换行，下面按中文宽度重排
            out.append(token)
            cells = 0
            continue
        if cells >= width:
            out.append(("ctrl", 0x03, b""))
            cells = 0
        out.append(token)
        cells += 1
    return out


def _close_padding_counts(tokens):
    """原文每个 FF01 后连续 slot 0 的数量。不同恢复点会跨过不同 padding。"""
    counts = []
    for index, token in enumerate(tokens):
        if token[0] != "ctrl" or token[1] != 0x01:
            continue
        count = 0
        while (index + 1 + count < len(tokens) and
               tokens[index + 1 + count] == ("char", 0)):
            count += 1
        counts.append(count)
    return counts


def _clone_close_padding(tokens, counts, count_index):
    """删掉译文自带的 FF01 后空格，按原文对应 FF01 精确恢复。"""
    out = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        out.append(token)
        index += 1
        if token[0] != "ctrl" or token[1] != 0x01:
            continue
        while index < len(tokens) and tokens[index] == ("char", 0):
            index += 1
        if count_index >= len(counts):
            raise E.EncodeError("译文 FF01 数量超过原文")
        out.extend([("char", 0)] * counts[count_index])
        count_index += 1
    return out, count_index


def encode_span_units(ef: EFile, section: int, span_index: int, zh: str,
                      codetable_path=None, wrap_width: int | None = None) -> dict[int, bytes]:
    """把带 ``⟪Bn⟫`` 的中文编码为 ``{unit_start: bytes}``。"""
    sec = ef.section(section)
    start, end = ef.sec_spans[section][span_index]
    boundaries = span_boundaries(ef, section, span_index)
    E.validate_breakpoints(zh, len(boundaries))

    ctrl = dump.load_format()["ctrl"]["efile"]
    original = D.decode(sec.raw, start, end, ctrl)
    _, _, codes = E.tokens_to_masked(original, dump.load_codetable(), ctrl)
    markup = E.restore_masked(zh, codes).replace(r"\n", "\n")

    pieces = BREAK_RE.split(markup)
    texts = pieces[::2]  # 捕获的 B 编号位于奇数项
    starts = [start] + boundaries
    if len(texts) != len(starts):
        raise E.EncodeError("breakpoint 切分数量与 unit 数量不一致")

    codetable = E.load_codetable(codetable_path)
    replacements = {}
    combined = []
    close_pads = _close_padding_counts(original)
    close_index = 0
    for unit_start, text in zip(starts, texts):
        tokens = E.markup_to_tokens(text, codetable, ctrl)
        if wrap_width is not None:
            tokens = _wrap_runs(tokens, wrap_width)
        tokens, close_index = _clone_close_padding(tokens, close_pads, close_index)
        combined.extend(tokens)
        replacements[unit_start] = E.encode_tokens(tokens)

    if close_index != len(close_pads):
        raise E.EncodeError(
            f"译文只消费 {close_index}/{len(close_pads)} 个 FF01 padding 记录")

    keep = lambda seq: [item for item in E._meaningful_controls(seq, ctrl)
                        if item[0] != 0x03]
    if keep(combined) != keep(original):
        raise E.EncodeError("译文控制码或参数与原 span 不一致")
    return replacements


def _anchor_marker(source: str, zh: str, marker: str) -> str | None:
    """marker 紧邻控制码占位符时，把它自动搬到中文的同一侧。"""
    pos = source.index(marker)
    left = re.search(r"⟦(\d+)⟧$", source[:pos])
    right = re.match(r"⟦(\d+)⟧", source[pos + len(marker):])
    if left:
        anchor = left.group(0)
        if zh.count(anchor) == 1:
            return zh.replace(anchor, anchor + marker)
    if right:
        anchor = right.group(0)
        if zh.count(anchor) == 1:
            return zh.replace(anchor, marker + anchor)
    return None


def migration_report(all_text_path: Path, translations_path: Path, out_path: Path) -> dict:
    """为已有 E0 译文生成 breakpoint 自动建议与人工待办，不修改原表。"""
    extracted = json.loads(all_text_path.read_text(encoding="utf-8"))
    translations = json.loads(translations_path.read_text(encoding="utf-8"))
    by_id = {entry["id"]: entry for entry in extracted["entries"]}
    rows = []
    auto = manual = 0
    for line_id, item in translations.items():
        if not line_id.startswith("ADV/E0.BIN#") or not item.get("zh"):
            continue
        entry = by_id.get(line_id)
        if not entry or not entry.get("breakpoints"):
            continue
        source = entry["masked"]
        old_source = item["jp"]
        clean_source = BREAK_RE.sub("", source)
        row = {
            "id": line_id,
            "source": source,
            "old_zh": item["zh"],
            "breakpoints": entry["breakpoints"],
        }
        if clean_source != old_source:
            row["status"] = "source_mismatch"
            rows.append(row)
            manual += 1
            continue
        proposed = item["zh"]
        unresolved = []
        for number in range(len(entry["breakpoints"])):
            marker = f"⟪B{number}⟫"
            moved = _anchor_marker(source, proposed, marker)
            if moved is None:
                unresolved.append(marker)
            else:
                proposed = moved
        row["proposed_zh"] = proposed
        row["unresolved"] = unresolved
        row["status"] = "manual" if unresolved else "auto"
        manual += bool(unresolved)
        auto += not unresolved
        rows.append(row)
    payload = {"auto": auto, "manual": manual, "entries": rows}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    return payload


def _demo():
    """跑 `python3 unit_encode.py` 看这个工具到底做了什么、以及它修好了什么。"""
    cfg = dump.load_format()
    ctrl = cfg["ctrl"]["efile"]
    ct = dump.load_codetable()
    ef = EFile(ROOT / "extrac" / "ADV" / "E0.BIN")

    SEC = 3                                             # 挑一段小的做演示
    sec = ef.section(SEC)
    a, b = ef.sec_spans[SEC][1]                         # 段3 的第 2 个 span（#3:1）
    pairs = decode_with_offsets(sec.raw, a, b, ctrl)

    print(f"E0 段{SEC} span[{a:#x},{b:#x}) 前 8 个 token，看'字节偏移 → 第几个字'：")
    for off, tok in pairs[:8]:
        shown = ct.get(tok[1], f"{{{tok[1]}}}") if tok[0] == "char" else f"<ctrl FF{tok[1]:02X}>"
        print(f"    字节 {off:#06x}  →  {shown}")

    boundaries = sorted(t for t in sec.text_targets if a < t < b)
    starts = {off for off, _ in pairs}
    inside = [t for t in boundaries if t not in starts]
    print(f"\nFF55/58 内部入口 {len(boundaries)} 个；其中 {len(inside)} 个落在 token 内部。")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report-e0", metavar="JSON",
                        help="输出已有 E0 译文的 breakpoint 迁移报告")
    parser.add_argument("--all-text", default=str(ROOT / "Text" / "all_text.json"))
    parser.add_argument("--translations", default=str(ROOT / "Text" / "translations.json"))
    args = parser.parse_args()
    if args.report_e0:
        result = migration_report(Path(args.all_text), Path(args.translations),
                                  Path(args.report_e0))
        print(f"E0 breakpoint：自动 {result['auto']}，需人工 {result['manual']}，"
              f"报告 {args.report_e0}")
    else:
        _demo()


if __name__ == "__main__":
    main()
