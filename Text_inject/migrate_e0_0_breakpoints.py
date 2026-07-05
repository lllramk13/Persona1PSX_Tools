#!/usr/bin/env python3
"""一次性人工对齐 ADV/E0.BIN#0:0 的 45 个 breakpoint。"""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TABLE = ROOT / "Text" / "translations.json"
REPORT = ROOT / "Text" / "e0_breakpoint_report.json"
LINE_ID = "ADV/E0.BIN#0:0"

# (现有中文唯一片段, 插入 breakpoint 后的片段)
EDITS = [
    ("你脑子不会进水了吧?", "你脑子不会进水了吧⟪B0⟫?"),
    ("敢不敢打赌啊，马克", "敢不敢打赌啊，⟪B1⟫马克"),
    ("如何呢?", "如何⟪B2⟫呢?"),
    ("我就押上杉这边!", "我就押上杉这边⟪B3⟫!"),
    ("Brown了哦", "Brown⟪B4⟫了哦"),
    ("一个两个都搞什么啊?", "一个两个都搞什么⟪B5⟫啊?"),
    ("你们押谁那边啊?", "你们押谁那边⟪B6⟫啊?"),
    ("这种愚蠢的问题⟦31⟧别拿来问我", "这种愚蠢的问题⟦31⟧⟪B7⟫别拿来问我"),
    ("你们随便吧", "你们⟪B8⟫随便吧"),
    ("不好相处⟦39⟧", "不好相处⟪B9⟫⟦39⟧"),
    ("不过你肯定是押我这边吧", "不过你肯定是押我这边⟪B10⟫吧"),
    ("押谁那边?⟦45⟧", "押谁那边?⟪B11⟫⟦45⟧"),
    ("一个两个的都不正常⟦48⟧", "一个两个的都不正常⟪B12⟫⟦48⟧"),
    ("到时候可别后悔哦～", "到时候可别后悔⟪B13⟫哦～"),
    ("那么，开始吧!", "那么，开始⟪B14⟫吧!"),
    ("那就从我开始咯", "那就从我开始⟪B15⟫咯"),
    ("快来到我身边吧", "快来到我身边⟪B16⟫吧"),
    ("快点来到我身边吧!", "快点来到我身边吧⟪B17⟫!"),
    ("跟着做这种事啊...", "跟着做这种事啊⟪B18⟫..."),
    ("吃自助餐、吃自助餐!", "吃自助餐、吃自助⟪B19⟫餐!"),
    ("快点来吧", "快点⟪B20⟫来吧"),
    ("请您来到我们身边...", "请您来到我们身边⟪B21⟫..."),
    ("...啊呀?", "...⟪B22⟫啊呀?"),
    ("我很像个笨蛋吗!", "我很像个笨蛋吗⟪B23⟫!"),
    ("看来是我赌赢了", "看来是我赌赢⟪B24⟫了"),
    ("那就快叫老师过来吧", "那就快叫老师⟪B25⟫过来吧"),
    ("你倒是拿出点干劲来啊", "你倒是拿出点干劲⟪B26⟫来啊"),
    ("真是够会死缠烂打的啊", "真是够会死缠烂打的⟪B27⟫啊"),
    ("后面...快看后面", "后面...快看⟪B28⟫后面"),
    ("现在要说什么都太晚...", "现在要说什么都太晚⟪B29⟫..."),
    ("马克:我去!!", "马克:我去⟪B30⟫!!"),
    ("真、真的假的?", "真、真⟪B31⟫的假的?"),
    ("真是令人吃惊⟦130⟧", "真是令人吃⟪B32⟫惊⟦130⟧"),
    ("虽然和之前不太一样就是了...", "虽然和之前不太一样就是了⟪B33⟫..."),
    ("救救...救救我...", "救救...救救我⟪B34⟫..."),
    ("马克:怎、怎么了!", "马克:怎、怎么了⟪B35⟫!"),
    ("南条:什么?这到底!?", "南条:什么?这到底⟪B36⟫!?"),
    ("真是越来越有趣了呢!", "真是越来越有趣了呢⟪B37⟫!"),
    ("什么啊～!!", "什么啊～⟪B38⟫!!"),
    ("南条:呃啊!?", "南条:呃啊⟪B39⟫!?"),
    ("性格直率⟦157⟧", "性格直率⟪B40⟫⟦157⟧"),
    ("不折不扣的理性主义者⟦162⟧", "不折不扣的理性主义者⟪B41⟫⟦162⟧"),
    ("深受大家的信赖⟦166⟧", "深受大家的信赖⟪B42⟫⟦166⟧"),
    ("但是又没什么本事的人⟦171⟧", "但是又没什么本事的人⟪B43⟫⟦171⟧"),
    ("公认的惹事精辣妹⟦176⟧", "公认的惹事精辣妹⟪B44⟫⟦176⟧"),
]


def main() -> None:
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    source = next(row["source"] for row in report["entries"] if row["id"] == LINE_ID)
    zh = table[LINE_ID]["zh"]
    if "⟪B0⟫" in zh:
        raise SystemExit(f"{LINE_ID} 已含 breakpoint，拒绝重复迁移")
    for old, new in EDITS:
        count = zh.count(old)
        if count != 1:
            raise ValueError(f"锚文本应恰好出现一次，实际 {count}: {old!r}")
        zh = zh.replace(old, new)
    numbers = [int(n) for n in re.findall(r"⟪B(\d+)⟫", zh)]
    if numbers != list(range(45)):
        raise ValueError(f"breakpoint 顺序错误: {numbers}")
    table[LINE_ID] = {"jp": source, "zh": zh}
    TABLE.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    print(f"✅ {LINE_ID}: 已人工对齐 45 个 breakpoint")


if __name__ == "__main__":
    main()
