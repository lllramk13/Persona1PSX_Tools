#!/usr/bin/env python3
"""翻译体检：控制符占位符 + 重复句一致性。回插前跑一遍，别把错误带进游戏。

三类检查：
  1. 占位符错误：zh 的 ⟦n⟧ 必须与 jp 的 ⟦n⟧ 集合完全一致（0..N-1 各一次）。
     漏一个 = 少了一个控制码（换行/关框/换色…），回插时 restore_masked 会挂或
     渲染错乱。报告缺哪个、多哪个。
  2. 重复句冲突：同一 jp 出现在多个 id 却填了不同 zh → 需人工统一（尤其人名）。
  3. 可扩散：某 jp 已有唯一译文，但同句还有 id 空着 → sync_json.py 能自动填。

用法：
    python3 Text/check_translations.py                 # 全表
    python3 Text/check_translations.py --scope ADV/E0  # 只看 E0（前缀匹配）
    python3 Text/check_translations.py --names          # 额外扫疑似人名不一致
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TABLE = HERE / "translations.json"
MARK = re.compile(r"⟦(\d+)⟧")
BREAK = re.compile(r"⟪B(\d+)⟫")


def placeholder_report(jp: str, zh: str):
    """返回 (缺失列表, 多余列表)；空表示占位符一致。"""
    jp_n = [int(m) for m in MARK.findall(jp)]
    zh_n = [int(m) for m in MARK.findall(zh)]
    want = set(range(len(jp_n)))                    # jp 应是 0..N-1
    got = zh_n
    missing = sorted(want - set(got))
    extra = sorted(n for n in got if n not in want or got.count(n) > 1)
    # 顺序/重复也算错：严格要求 zh 升序恰好 0..N-1
    ordered_ok = (got == sorted(want))
    return missing, extra, ordered_ok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", default="", help="只检查 id 前缀匹配的条目，如 ADV/E0")
    ap.add_argument("--names", action="store_true", help="额外扫疑似人名不一致")
    args = ap.parse_args()

    table = json.loads(TABLE.read_text("utf-8"))
    items = [(k, v) for k, v in table.items()
             if v.get("zh") and k.startswith(args.scope)]
    print(f"检查范围: {args.scope or '全表'}；已填 zh {len(items)} 条\n")

    # 1) 占位符
    ph_errors = []
    for k, v in items:
        missing, extra, ordered_ok = placeholder_report(v["jp"], v["zh"])
        if missing or extra or not ordered_ok:
            ph_errors.append((k, missing, extra, ordered_ok, v))
    print(f"① 占位符错误: {len(ph_errors)} 条")
    for k, missing, extra, ordered_ok, v in ph_errors[:30]:
        detail = []
        if missing:
            detail.append(f"缺 {['⟦%d⟧' % n for n in missing]}")
        if extra:
            detail.append(f"多/重复 {['⟦%d⟧' % n for n in extra]}")
        if not ordered_ok and not missing and not extra:
            detail.append("顺序不对（应升序 0..N-1）")
        print(f"   {k}: {'; '.join(detail)}")
        print(f"      zh: {v['zh'][:70]}")
    if len(ph_errors) > 30:
        print(f"   …还有 {len(ph_errors)-30} 条")

    bp_errors = []
    for k, v in items:
        wanted = [int(n) for n in BREAK.findall(v["jp"])]
        got = [int(n) for n in BREAK.findall(v["zh"])]
        if got != wanted:
            bp_errors.append((k, wanted, got))
    print(f"\n② breakpoint 错误: {len(bp_errors)} 条")
    for k, wanted, got in bp_errors[:30]:
        print(f"   {k}: 期望 {wanted}，实际 {got}")

    # 3) 重复句冲突
    jp_to_zh = defaultdict(set)
    jp_to_ids = defaultdict(list)
    for k, v in items:
        jp_to_zh[v["jp"]].add(v["zh"])
        jp_to_ids[v["jp"]].append(k)
    conflicts = {jp: zhs for jp, zhs in jp_to_zh.items() if len(zhs) > 1}
    print(f"\n③ 同句多译（需统一）: {len(conflicts)} 组")
    for jp, zhs in list(conflicts.items())[:20]:
        print(f"   日文: {jp[:50]}  （{len(jp_to_ids[jp])} 处）")
        for z in list(zhs):
            print(f"      → {z[:50]}")
        print(f"      id: {', '.join(jp_to_ids[jp][:6])}")

    # 3) 可扩散
    filled_jp = {v["jp"] for _, v in items}
    all_items = [(k, v) for k, v in table.items() if k.startswith(args.scope)]
    fanout = [k for k, v in all_items
              if not v.get("zh") and v["jp"] in filled_jp and v["jp"] not in conflicts]
    print(f"\n④ 可自动扩散填充的空条目: {len(fanout)}（跑 sync_json.py --apply 自动填）")

    # 4) 可选：疑似人名不一致（同一说话人前缀 "X:" 的不同译名）
    if args.names:
        print("\n⑤ 疑似人名不一致（同一 jp 说话人前缀译法不同已并入③；此处略）")

    n_bad = len(ph_errors) + len(bp_errors) + len(conflicts)
    print(f"\n{'='*40}")
    if n_bad == 0:
        print("✅ 无占位符/breakpoint 错误、无同句冲突。可以进回插。")
        sys.exit(0)
    print(f"⚠ 共 {len(ph_errors)} 处占位符错误 + {len(bp_errors)} 处 breakpoint 错误 "
          f"+ {len(conflicts)} 组同句冲突需修。")
    sys.exit(1)


if __name__ == "__main__":
    main()
