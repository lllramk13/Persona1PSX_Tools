#!/usr/bin/env python3
"""逐段对齐一条翻译的 jp / zh，帮你定位控制码错位从哪开始。

原理：jp 和 zh 应有相同数量的 ⟦n⟧，且第 k 段文字（第 k 个占位符之前的那段）
语义对应。把两边按 ⟦n⟧ 切开并排显示，第一处「段号错位 / 内容对不上」就是
控制码被丢/多的地方。

用法：
    python3 Text/align_entry.py ADV/E0.BIN#144:0
    python3 Text/align_entry.py ADV/E0.BIN#144:0 --from 250   # 只看第250段起
"""
import argparse
import json
import re
from pathlib import Path

TABLE = Path(__file__).resolve().parent / "translations.json"
MARK = re.compile(r"⟦(\d+)⟧")


def segments(text):
    """切成 [(段文字, 该段后面的占位符号或 None)]。"""
    out, last, i = [], 0, 0
    for m in MARK.finditer(text):
        out.append((text[last:m.start()], int(m.group(1))))
        last = m.end()
    tail = text[last:]
    if tail:
        out.append((tail, None))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("id")
    ap.add_argument("--from", dest="start", type=int, default=0)
    args = ap.parse_args()

    tr = json.loads(TABLE.read_text("utf-8"))
    if args.id not in tr:
        raise SystemExit(f"找不到 {args.id}")
    v = tr[args.id]
    jp_seg = segments(v["jp"])
    zh_seg = segments(v["zh"])

    # 结构对齐：比较"空段序列"（连续控制码=空段）。号能对上但内容平移时，
    # 空/非空的模式会先分叉——那才是控制码被丢/多的真正位置。
    jp_pat = [not s[0].strip() for s in jp_seg if s[1] is not None]
    zh_pat = [not s[0].strip() for s in zh_seg if s[1] is not None]
    struct_drift = None
    for i in range(min(len(jp_pat), len(zh_pat))):
        if jp_pat[i] != zh_pat[i]:
            struct_drift = i
            break
    if struct_drift is not None:
        print(f"★ 结构错位点：约第 {struct_drift} 个控制码附近"
              f"（日文那段{'是空段' if jp_pat[struct_drift] else '有文字'}，"
              f"中文{'是空段' if zh_pat[struct_drift] else '有文字'}）")
        print(f"  → 用 --from {max(0,struct_drift-3)} 看这附近\n")
    jp_n = len(MARK.findall(v["jp"]))
    zh_n = len(MARK.findall(v["zh"]))
    print(f"{args.id}   日文占位符 {jp_n} 个, 中文 {zh_n} 个  "
          f"({'一致' if jp_n==zh_n else '★不一致，差 %+d★' % (zh_n-jp_n)})\n")
    print(f"{'#':>4} | {'日文段（⟦n⟧前）':<34} | 中文段")
    print("-" * 78)
    drift_flagged = False
    for i in range(max(len(jp_seg), len(zh_seg))):
        j = jp_seg[i] if i < len(jp_seg) else ("", None)
        z = zh_seg[i] if i < len(zh_seg) else ("", None)
        if i < args.start:
            continue
        jmark = f"⟦{j[1]}⟧" if j[1] is not None else "—"
        zmark = f"⟦{z[1]}⟧" if z[1] is not None else "—"
        # 段号错位标记：两边该段后的占位符号不同 = 从这开始漂了
        flag = ""
        if j[1] != z[1] and not drift_flagged:
            flag = "  ◀── 错位从这开始"
            drift_flagged = True
        jt = (j[0][:30] + jmark).replace("\n", "\\n")
        zt = (z[0][:30] + zmark).replace("\n", "\\n")
        print(f"{i:>4} | {jt:<34} | {zt}{flag}")
    if jp_n == zh_n:
        print("\n✅ 占位符数量一致。")
    else:
        print(f"\n⚠ 从「◀──」那段起，中文的 ⟦n⟧ 与日文对不上了。"
              f"往那附近看，是{'漏了' if zh_n<jp_n else '多了'}控制码。")


if __name__ == "__main__":
    main()
