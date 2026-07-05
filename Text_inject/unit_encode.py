#!/usr/bin/env python3
"""T2 粒度桥接（接力手册 §8.2）——目前只有第一块地基：偏移精确 tokenizer。

问题背景（详见会话/手册 §2.3）：一段翻译是"大块"(reader span)，但游戏用 FF55 指针
按"小块"(unit)引用文本，一段里有好几个 unit。要把中文切成对应的小块，必须知道
"某个字节偏移 = 第几个 token(字/控制码)"。decode() 走字节是精确的，但它只返回 token、
丢掉了每个 token 的字节位置。本模块 = decode() 原样，外加"记下每个 token 的起始偏移"。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "Text"))


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
    out = []
    i = start
    while i < end:
        b = data[i]
        off = i                                         # ← 关键：先记住起点
        if b == 0xFF:                                   # 控制码：长度查 ctrl 表(默认 2)
            code = data[i + 1] if i + 1 < len(data) else 0
            length = ctrl.get(code, (None, 2))[1]
            params = bytes(data[i + 2:i + length])
            out.append((off, ("ctrl", code, params)))
            i += length
        elif 0x80 <= b <= 0x87:                         # 两字节逃逸字：slot=(b-0x80)*256+lo
            lo = data[i + 1] if i + 1 < len(data) else 0
            out.append((off, ("char", (b - 0x80) * 256 + lo)))
            i += 2
        else:                                           # 单字节字：slot 就是这个字节
            out.append((off, ("char", b)))
            i += 1
    return out


def _demo():
    """跑 `python3 unit_encode.py` 看这个工具到底做了什么、以及它修好了什么。"""
    import decode as D
    import dump
    from ebin_rebuild import EFile

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

    # 这一段里，游戏的 FF55/FF58 单元边界（真实要切的地方）
    f55 = set()
    for site, tgt in sec.ptr_sites:
        h = site - 4
        if h >= 0 and h % 4 == 0 and sec.raw[h] == 0xFF and sec.raw[h + 1] in (0x55, 0x58):
            f55.add(tgt)
    boundaries = sorted(t for t in f55 if a < t < b)

    # 精确偏移集合 vs 我之前那套"估算宽度"的偏移集合
    exact_offs = {off for off, _ in pairs}
    guess_offs, o = set(), a
    for _, tok in pairs:
        guess_offs.add(o)
        o += (1 if tok[1] <= 0x7F else 2) if tok[0] == "char" else 2 + len(tok[2])

    print(f"\n这段有 {len(boundaries)} 个真单元边界(FF55/58)。检查它们落不落在某个字的起点上：")
    for t in boundaries:
        ch = "?"
        for off, tok in pairs:
            if off == t and tok[0] == "char":
                ch = ct.get(tok[1], f"{{{tok[1]}}}")
                break
        print(f"    边界 {t:#x}: 精确法命中={t in exact_offs}  估算法命中={t in guess_offs}"
              f"   那个字≈{ch!r}")


if __name__ == "__main__":
    _demo()
