#!/usr/bin/env python3
"""第一个中文注入实验：把 E0 的 #3:1（夏美老师那段）整段换成中文。

目的（会话 2026-07-05）：验证"方法B"——ebin_rebuild 现在只按真 FF55/FF58 切单元
（动作1），并且把指向被替换单元内部的噪声指针跳过不动（动作2）。#3:1 这段内部
只有段头表的噪声切点、没有真 FF55，所以它是"一整块"，整段换掉即可。

实机要看两件事：
  ① 夏美那段是不是显示成中文（字库 + 编码 + 重建 全对）；
  ② 对话/选项有没有错乱 —— 若一切正常，说明那些噪声切点是"死的"，
     这招能推广到其余 ~240 段，粒度桥接问题大幅缩小。

⚠ 两个 TEMP 补丁（等 T1 append-只追加码表 / T2 正式编码器 落地后删）：
  · 译文半角 :? → 全角 ：？（现有 codetable_zh 把这两个标点重排成了全角）；
  · restore_masked 产出的字面 "\\n" → 真换行（encode.py 的 masked↔markup 接缝）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "Text"))

import decode as D          # noqa: E402
import dump                 # noqa: E402
import encode as E          # noqa: E402
from ebin_rebuild import EFile, split_sections   # noqa: E402
import build_disc as disc   # noqa: E402

OUT = HERE / "out"
SEC = 3
SPAN_IDX = 1                # #3:1
TR_ID = "ADV/E0.BIN#3:1"

# TEMP①：译文里的标点跟码表对齐（见 docstring）
PUNCT_FIX = str.maketrans({":": "：", "?": "？"})


def encode_zh_span(ef: EFile, sec_idx: int, span_idx: int, zh: str) -> tuple[int, bytes]:
    """把一条 span 粒度的译文编码成该单元的新字节。返回 (单元起点, 字节)。

    仅适用于"整段一块"（段内无真 FF55 边界）的 span；多单元切分是 T2 的正事。
    """
    sec = ef.section(sec_idx)
    a, b = ef.sec_spans[sec_idx][span_idx]
    units_in = [u for u in sec.units if a <= u[0] < b]
    if len(units_in) != 1 or units_in[0][0] != a:
        raise SystemExit(f"#{sec_idx}:{span_idx} 不是整段一块（有 {len(units_in)} 个单元），"
                         f"本实验只处理单块 span")

    ctrl = dump.load_format()["ctrl"]["efile"]
    _, _, codes = E.tokens_to_masked(D.decode(sec.raw, a, b, ctrl),
                                     dump.load_codetable(), ctrl)
    ct_zh = E.load_codetable(ROOT / "Codetable" / "codetable_zh.json")

    zh = zh.translate(PUNCT_FIX)                              # TEMP①
    markup = E.restore_masked(zh, codes).replace("\\n", "\n")  # TEMP②
    tokens = E.markup_to_tokens(markup, ct_zh, ctrl)
    return a, E.encode_tokens(tokens)


def main() -> None:
    ef = EFile(ROOT / "extrac" / "ADV" / "E0.BIN")
    zh = json.load(open(ROOT / "Text" / "translations.json"))[TR_ID]["zh"]

    a, zh_bytes = encode_zh_span(ef, SEC, SPAN_IDX, zh)
    print(f"#{SEC}:{SPAN_IDX} 中文编码 {len(zh_bytes)} 字节"
          f"（原 {ef.sec_spans[SEC][SPAN_IDX][1]-a} 字节）")

    rebuilt = ef.rebuild({SEC: {a: zh_bytes}})
    if len(rebuilt) != len(ef.data):
        raise SystemExit(f"文件大小变了 {len(ef.data)}→{len(rebuilt)}，超扇区配额，"
                         f"本实验要求等长（换一段更短的，或等 T6 镜像扩容）")
    # 其余段必须逐字节不变
    secs = split_sections(rebuilt)
    for i, (s0, s1) in enumerate(ef.sections):
        if i != SEC and rebuilt[secs[i][0]:secs[i][1]] != ef.data[s0:s1]:
            raise SystemExit(f"段 {i} 意外被改动")
    print(f"✅ E0 重建等长（{len(rebuilt)} 字节），其余 223 段逐字节不变")

    OUT.mkdir(exist_ok=True)
    (OUT / "E0.patched.BIN").write_bytes(rebuilt)
    print(f"→ {OUT / 'E0.patched.BIN'}")

    if not (OUT / "FONT.patched.BIN").is_file():
        raise SystemExit("缺 out/FONT.patched.BIN —— 先在有 opencc 的环境跑 build_font.py")

    # 出盘：build_disc 会把 out/ 里 FONT + E0 两个补丁写进同一张 [ZH-test].bin
    disc.build_test_disc()
    print("\n实机看：进夏美老师保健室那段，对白应为中文；把选项两边都点一遍看有没有错乱。")


if __name__ == "__main__":
    main()
