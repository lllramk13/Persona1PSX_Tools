"""P1 可逆文本编码与安全原位回插。

常用流程::

    # 导出带完整控制码的可回插模板
    python3 encode.py ../extrac/ADV/E0.BIN --export e0_patch.json

    # 编辑模板中需要修改的行：把 zh 留空的行忽略，非空行用于回插
    python3 encode.py ../extrac/ADV/E0.BIN --patch e0_patch.json -o E0.patched.BIN

标记格式保留控制码参数，例如 ``<color:02>``；换行控制码写成真正的换行。
有歧义、缺字或多字符字形可显式写成 ``{717}``（字库 slot 十进制号）。

本工具只做“不移动指针”的原位回插：编码结果不得超过原 span。它始终写到新文件，
并在落盘前重新扫描容器、复读每个补丁，避免悄悄产出坏文件。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from pathlib import Path

import containers as C
import decode as D
import dump


SLOT_RE = re.compile(r"\{(\d+)\}")
TAG_RE = re.compile(r"<([A-Za-z0-9_?]+)(?::([0-9A-Fa-f]*))?>")
MARK_RE = re.compile(r"⟦(\d+)⟧")
NEWLINE_NAMES = {"nl", "newline"}


class EncodeError(ValueError):
    """输入文本无法安全编码。"""


def encode_slot(slot: int) -> bytes:
    """把 0..2047 字形槽编码成 P1 的字节形式。

    解码器会容忍原盘里少量裸 ``0x88-0xFE`` 字节，但实机文本渲染
    对这段裸字节并不按同号字形稳定显示。修改/新增文本时使用保守、
    与原版主要正文一致的写法：

    - 0x00-0x7F → 单字节
    - 0x80-0x7FF → 0x80-0x87 + low byte

    原文未修改回插由 ``apply_patch`` 直接保留原始 span 字节，因此不会
    因为这里的规范写法破坏 byte-exact 对照实验。
    """
    if not 0 <= slot < 2048:
        raise EncodeError(f"字形 slot 越界: {slot}")
    if slot <= 0x7F:
        return bytes((slot,))
    return bytes((0x80 + (slot >> 8), slot & 0xFF))


def encode_tokens(tokens) -> bytes:
    """token 的无损底层编码；用于全盘 round-trip 验证。"""
    out = bytearray()
    for token in tokens:
        if token[0] == "char":
            out += encode_slot(token[1])
        elif token[0] == "ctrl":
            _, code, params = token
            out += bytes((0xFF, code)) + bytes(params)
        else:
            raise EncodeError(f"未知 token: {token!r}")
    return bytes(out)


def _reverse_codetable(codetable):
    slots = defaultdict(list)
    for slot, text in codetable.items():
        if text:
            slots[text].append(slot)
    # 普通翻译输入遇到重复字形时，优先选编码最短、slot 最小的一个。
    preferred = {
        text: min(candidates, key=lambda s: (len(encode_slot(s)), s))
        for text, candidates in slots.items()
    }
    # 最长匹配让码表里的 "..." 这类多字符字形仍可直接输入。
    values = sorted(preferred, key=lambda s: (-len(s), s))
    return slots, preferred, values


def tokens_to_markup(tokens, codetable, ctrl, include_padding=False):
    """生成可由 :func:`markup_to_tokens` 完整读回的文本。"""
    slots, _, _ = _reverse_codetable(codetable)
    parts = []
    for index, token in enumerate(tokens):
        if token[0] == "char":
            slot = token[1]
            text = codetable.get(slot, "")
            # 重复映射必须钉死 slot，否则回编可能换到另一个字形。
            if (not text or len(slots[text]) != 1 or
                    any(ch in text for ch in "{}<>\r\n")):
                parts.append(f"{{{slot}}}")
            else:
                parts.append(text)
            continue

        _, code, params = token
        name = ctrl.get(code, (f"FF{code:02X}?", 2))[0]
        if name == "pad" and not include_padding:
            # TALK 的 span 常一直延伸到下一指针，尾部可能有大量 FFFF。
            # 只允许省略尾随 padding，中间 padding 仍须显式保留。
            if all(t[0] == "ctrl" and
                   ctrl.get(t[1], (None, 2))[0] == "pad"
                   for t in tokens[index:]):
                break
        if name in NEWLINE_NAMES and not params:
            parts.append("\n")
        else:
            suffix = f":{bytes(params).hex().upper()}" if params else ""
            parts.append(f"<{name}{suffix}>")
    return "".join(parts)


def tokens_to_masked(tokens, codetable, ctrl):
    """生成 P2EP 同款的 ``(jp, masked, codes)`` 翻译视图。

    ``jp`` 保留可逆控制码，``masked`` 只露出 ``⟦0⟧`` 占位符，
    ``codes[n]`` 是占位符对应的原始标记。空字形 slot 也作为布局码
    保护，避免翻译时被无意删除。
    """
    # TALK span 后面通常是对齐用 FFFF，不属于翻译内容。
    work = list(tokens)
    while (work and work[-1][0] == "ctrl" and
           ctrl.get(work[-1][1], (None, 2))[0] == "pad"):
        work.pop()

    raw_parts = []
    masked_parts = []
    codes = []

    def protect(code):
        index = len(codes)
        codes.append(code)
        raw_parts.append(code)
        masked_parts.append(f"⟦{index}⟧")

    for token in work:
        if token[0] == "char":
            slot = token[1]
            text = codetable.get(slot, "")
            if text:
                raw_parts.append(text)
                masked_parts.append(text)
            elif slot == 0:
                # slot 0 是空白字形，属于排版而非动态控制码。
                # 翻译视图里用普通空格更易读，也允许译者重新排版。
                raw_parts.append(" ")
                masked_parts.append(" ")
            else:
                protect(f"{{{slot}}}")
            continue

        name = ctrl.get(token[1], (f"FF{token[1]:02X}?", 2))[0]
        if name == "pad":
            continue
        if name in NEWLINE_NAMES and not token[2]:
            protect(r"\n")
        else:
            # 这里不调 tokens_to_markup：它为普通文本解析会建整张
            # 反向码表，全量导出时对每个控制码重建会非常慢。
            params = bytes(token[2])
            suffix = f":{params.hex().upper()}" if params else ""
            protect(f"<{name}{suffix}>")

    return "".join(raw_parts), "".join(masked_parts), codes


def restore_masked(text, codes):
    """校验并还原 ``⟦n⟧``；占位符必须恰好按 0..N-1 出现一次。"""
    numbers = [int(match.group(1)) for match in MARK_RE.finditer(text)]
    wanted = list(range(len(codes)))
    if numbers != wanted:
        raise EncodeError(f"占位符序列不一致: 期望 {wanted}，实际 {numbers}")
    return MARK_RE.sub(lambda match: codes[int(match.group(1))], text)


def markup_to_tokens(text, codetable, ctrl):
    """解析人类可编辑的可逆标记文本。"""
    _, preferred, values = _reverse_codetable(codetable)
    by_name = {}
    for code, (name, length) in ctrl.items():
        if name in by_name:
            raise EncodeError(f"控制码名字重复，无法反查: {name}")
        by_name[name] = (code, length)

    newline = [item for name, item in by_name.items() if name in NEWLINE_NAMES]
    out = []
    i = 0
    while i < len(text):
        if text[i] == "\r" and i + 1 < len(text) and text[i + 1] == "\n":
            i += 1
        if text[i] == "\n":
            if len(newline) != 1:
                raise EncodeError("本格式没有唯一的换行控制码")
            code, length = newline[0]
            if length != 2:
                raise EncodeError("带参数的换行控制码必须写成显式标签")
            out.append(("ctrl", code, b""))
            i += 1
            continue

        slot_match = SLOT_RE.match(text, i)
        if slot_match:
            slot = int(slot_match.group(1))
            encode_slot(slot)  # 立即校验范围
            out.append(("char", slot))
            i = slot_match.end()
            continue

        tag_match = TAG_RE.match(text, i)
        if tag_match:
            name, raw_params = tag_match.groups()
            if name not in by_name:
                # decoder 对未命名的两字节码使用 "FFxx?" 显示名。
                # 保留这个原始形式，使 TALK 生僻控制码也能往返。
                raw_code = re.fullmatch(r"FF([0-9A-Fa-f]{2})\?", name)
                if not raw_code:
                    raise EncodeError(
                        f"未知控制码标签 {tag_match.group(0)!r}，位置 {i}")
                code, length = int(raw_code.group(1), 16), 2
            else:
                code, length = by_name[name]
            raw_params = raw_params or ""
            if len(raw_params) % 2:
                raise EncodeError(f"控制码参数必须是偶数个十六进制字符: {tag_match.group(0)}")
            params = bytes.fromhex(raw_params)
            expected = length - 2
            if len(params) != expected:
                raise EncodeError(
                    f"<{name}> 需要 {expected} 字节参数，实际 {len(params)} 字节")
            out.append(("ctrl", code, params))
            i = tag_match.end()
            continue

        matched = None
        for value in values:
            if text.startswith(value, i):
                matched = value
                break
        if matched is None:
            ch = text[i]
            raise EncodeError(f"码表没有字符 {ch!r} (U+{ord(ch):04X})，位置 {i}")
        out.append(("char", preferred[matched]))
        i += len(matched)
    return out


def _meaningful_controls(tokens, ctrl):
    return [(t[1], bytes(t[2])) for t in tokens
            if t[0] == "ctrl" and ctrl.get(t[1], (None, 2))[0] != "pad"]


def _strip_filler(tokens, ctrl):
    tokens = list(tokens)
    while tokens:
        token = tokens[-1]
        if token[0] == "char" and token[1] == 0:
            tokens.pop()
        elif (token[0] == "ctrl" and
              ctrl.get(token[1], (None, 2))[0] == "pad"):
            tokens.pop()
        else:
            break
    return tokens


def _line_records(decoded):
    local = defaultdict(int)
    records = []
    for section, start, end, tokens in decoded:
        index = local[section]
        local[section] += 1
        records.append({
            "id": f"{section}:{index}",
            "section": section,
            "span": [start, end],
            "tokens": tokens,
        })
    return records


def export_template(source, destination, codetable_path=None):
    cfg = dump.load_format()
    fmt, decoded = dump.decode_file(str(source), cfg)
    codetable = load_codetable(codetable_path)
    ctrl = cfg["ctrl"].get(fmt, {})
    lines = []
    for record in _line_records(decoded):
        markup = tokens_to_markup(record["tokens"], codetable, ctrl)
        lines.append({
            "id": record["id"],
            "span": record["span"],
            "jp": markup,
            "zh": "",
        })
    payload = {"source": os.path.basename(source), "format": fmt, "lines": lines}
    Path(destination).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(lines), fmt


def load_codetable(path=None):
    if path is None:
        return dump.load_codetable()
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return {int(k): str(v).replace("\n", "") for k, v in raw.items()}


def _load_patches(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    lines = payload.get("lines", []) if isinstance(payload, dict) else payload
    if not isinstance(lines, list):
        raise EncodeError("补丁 JSON 必须是数组，或含 lines 数组的对象")
    patches = {}
    for item in lines:
        if not isinstance(item, dict) or "id" not in item:
            raise EncodeError("每个补丁必须是含 id/zh 的对象")
        text = item.get("zh", "")
        if text in (None, ""):
            continue
        line_id = str(item["id"])
        if line_id in patches:
            raise EncodeError(f"补丁 id 重复: {line_id}")
        patches[line_id] = str(text)
    return patches


def apply_patch(source, patch_path, destination, codetable_path=None):
    source = Path(source)
    destination = Path(destination)
    if source.resolve() == destination.resolve():
        raise EncodeError("拒绝覆盖源文件；请用 -o 指定一个新文件")

    cfg = dump.load_format()
    fmt, decoded = dump.decode_file(str(source), cfg)
    ctrl = cfg["ctrl"].get(fmt, {})
    codetable = load_codetable(codetable_path)
    records = {r["id"]: r for r in _line_records(decoded)}
    patches = _load_patches(patch_path)
    unknown = sorted(set(patches) - set(records))
    if unknown:
        raise EncodeError(f"补丁含未知 id: {', '.join(unknown[:8])}")
    if not patches:
        raise EncodeError("没有非空 zh，未生成文件")

    data = bytearray(source.read_bytes())
    expected = {}
    for line_id, markup in patches.items():
        record = records[line_id]
        new_tokens = markup_to_tokens(markup, codetable, ctrl)
        old_tokens = _strip_filler(record["tokens"], ctrl)
        new_clean_tokens = _strip_filler(new_tokens, ctrl)
        old_controls = _meaningful_controls(record["tokens"], ctrl)
        new_controls = _meaningful_controls(new_tokens, ctrl)
        if new_controls != old_controls:
            raise EncodeError(
                f"{line_id}: 控制码或参数与原文不一致\n"
                f"  原: {old_controls}\n  新: {new_controls}")

        start, end = record["span"]

        # 原文原样回插时必须逐字节保持不变。普通 token 只保存 slot，
        # 不保存原盘里这个 slot 是短写(如 e2)还是长写(如 80 e2)；
        # 如果重新 encode，会把一些等价外观规范化，导致控制码字节位置变化。
        # 因此“内容未变”的补丁直接保留原始 span 字节，作为最干净的对照实验。
        if new_clean_tokens == old_tokens:
            expected[(record["section"], start)] = old_tokens
            continue

        encoded = encode_tokens(new_tokens)
        capacity = end - start
        if len(encoded) > capacity:
            raise EncodeError(
                f"{line_id}: 编码后 {len(encoded)} 字节，原位空间只有 {capacity} 字节")

        if fmt == "talk":
            remainder = capacity - len(encoded)
            filler = (b"\x00" if remainder % 2 else b"") + b"\xFF\xFF" * (remainder // 2)
        else:
            filler = b"\x00" * (capacity - len(encoded))
        data[start:end] = encoded + filler
        expected[(record["section"], start)] = new_clean_tokens

        # 对刚写入的实际字节做第一层精确复读。
        reread = D.decode(data, start, start + len(encoded), ctrl)
        if reread != new_tokens:
            raise EncodeError(f"{line_id}: 写入后 token 复读不一致")

    # 第二层验证：从修改后的完整容器重新定位文本，而不是相信旧 span。
    reader = C.READERS[fmt]
    found = {}
    for section, start, end in reader(data):
        key = (section, start)
        if key in expected:
            found[key] = _strip_filler(D.decode(data, start, end, ctrl), ctrl)
    missing = set(expected) - set(found)
    if missing:
        raise EncodeError(f"完整容器复扫后找不到补丁文本: {sorted(missing)}")
    for key, wanted in expected.items():
        if found[key] != wanted:
            raise EncodeError(f"完整容器复扫内容不一致: section/start={key}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return len(patches), fmt


def main():
    parser = argparse.ArgumentParser(description="P1 可逆文本编码与安全原位回插")
    parser.add_argument("source", help="原始 TALK/E/D/SLPS 文件")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--export", metavar="JSON", help="导出可回插模板")
    action.add_argument("--patch", metavar="JSON", help="应用 zh 非空的补丁")
    parser.add_argument("-o", "--out", help="补丁输出文件（--patch 时必填）")
    parser.add_argument("--codetable", help="自定义 slot→字 JSON；默认 codetable_og.json")
    args = parser.parse_args()

    try:
        if args.export:
            count, fmt = export_template(args.source, args.export, args.codetable)
            print(f"已导出 {args.export}: {count} 条（{fmt}）")
        else:
            if not args.out:
                parser.error("--patch 必须同时指定 -o/--out")
            count, fmt = apply_patch(
                args.source, args.patch, args.out, args.codetable)
            print(f"已安全回插 {count} 条 → {args.out}（{fmt}，容器复扫通过）")
    except (EncodeError, ValueError, OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"[失败] {exc}")


if __name__ == "__main__":
    main()
