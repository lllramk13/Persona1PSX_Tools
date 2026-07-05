#!/usr/bin/env python3
"""从 DuckStation 存档里挖出 PS1 主内存，跨多个场景对比，找空闲 RAM。

背景：4096 字库要搬到一块"所有模式都空闲"的连续 128KB。静态反汇编找不到
（大缓冲区走计算地址，对 lui 扫描隐形）。可靠办法 = 运行时观测：在每个游戏
模式存一个 DuckStation 存档，本脚本解压出 2MB 主内存快照并逐字节对比。

判据：
  · 某字节在所有快照里始终 = 0x00       → 强候选（从没被初始化=空闲）
  · 某字节跨快照发生过变化               → 一定在用（可写状态）
  · 某字节恒定非零                       → 静态数据/代码（读但不写，别占）
报告 0x100000-0x1EFFFF（工作 RAM，含当前字库）里最大的"全 0"连续块。
≥128KB 且落在 0x1F0000 之下 = 新字库家的候选地址。

DuckStation 存档格式：magic "DUCCS" + 若干 zstd 段；最大的那段解出 ~3.7MB
完整机器状态，2MB 主 RAM 嵌在里面。用 extrac/FONT.BIN 的头部当锚点定位
（游戏把字库放在 RAM 偏移 0x1E0000）。

用法：
    # 1) DuckStation 里加载 P1 测试盘，在每个场景 F1/菜单存档，各存成不同 slot
    # 2) 把这些 .sav 收集起来（或直接指向 savestates 目录）
    python3 re_tools/ramhunt.py 场景1.sav 场景2.sav 场景3.sav ...
    python3 re_tools/ramhunt.py "/mnt/c/.../DuckStation/savestates/"*.sav
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import zstandard as zstd

ROOT = Path(__file__).resolve().parent.parent
FONT = (ROOT / "extrac" / "FONT.BIN").read_bytes()
RAM_SIZE = 0x200000            # PS1 主 RAM 2MB
FONT_RAM_OFF = 0x1E0000        # 字库在 RAM 里的偏移（= 基址 0x801E0000 低 21 位）
ANCHOR = FONT[:512]            # 用字库头部当锚点
SCAN_LO = 0x100000            # 报告区间：工作 RAM
SCAN_HI = 0x1F0000            # 到游戏状态区之前


def extract_ram(sav_path: Path) -> bytes:
    """从一个 DuckStation .sav 里解出 2MB 主内存。"""
    raw = sav_path.read_bytes()
    magic = bytes.fromhex("28b52ffd")
    # 取最大的 zstd 段（= 完整机器状态）
    starts, i = [], 0
    while True:
        j = raw.find(magic, i)
        if j < 0:
            break
        starts.append(j)
        i = j + 4
    dctx = zstd.ZstdDecompressor()
    best = b""
    for s in starts:
        try:
            out = dctx.stream_reader(io.BytesIO(raw[s:])).read(8 * 1024 * 1024)
        except zstd.ZstdError:
            continue
        if len(out) > len(best):
            best = out
    if len(best) < RAM_SIZE:
        raise ValueError(f"{sav_path.name}: 没解出 ≥2MB 的状态段")
    # 用字库头部锚定 RAM 基址
    hit = best.find(ANCHOR)
    if hit < 0:
        raise ValueError(
            f"{sav_path.name}: 找不到 FONT.BIN 锚点——"
            f"这是 P1 的存档吗？字库是否已加载（先进到有文字的场景再存）？")
    ram_base = hit - FONT_RAM_OFF
    if ram_base < 0 or ram_base + RAM_SIZE > len(best):
        raise ValueError(f"{sav_path.name}: 锚点位置异常 {hit:#x}")
    return best[ram_base:ram_base + RAM_SIZE]


def main() -> None:
    args = [Path(a) for a in sys.argv[1:] if a.endswith(".sav")]
    if len(args) < 2:
        sys.exit("需要至少 2 个 .sav（越多模式越准）。用法见文件头。")

    rams = []
    for p in args:
        try:
            rams.append((p.name, extract_ram(p)))
            print(f"✅ {p.name}: 解出 2MB 主内存")
        except ValueError as e:
            print(f"⚠  {e}")
    if len(rams) < 2:
        sys.exit("有效快照不足 2 个。")

    # 逐字节：跨所有快照是否始终为 0；是否发生过变化
    n = SCAN_HI - SCAN_LO
    always_zero = bytearray(b"\x01" * n)   # 1 = 迄今全 0
    ever_changed = bytearray(n)            # 1 = 变化过
    first = rams[0][1]
    for off in range(n):
        a = SCAN_LO + off
        b0 = first[a]
        z = (b0 == 0)
        ch = False
        for _, ram in rams[1:]:
            if ram[a] != b0:
                ch = True
            if ram[a] != 0:
                z = False
        if not z:
            always_zero[off] = 0
        if ch:
            ever_changed[off] = 1

    # 最大"全 0"连续块
    def runs(mask, want):
        out, s = [], None
        for off in range(n):
            if mask[off] == want and s is None:
                s = off
            elif mask[off] != want and s is not None:
                out.append((SCAN_LO + s, SCAN_LO + off, off - s))
                s = None
        if s is not None:
            out.append((SCAN_LO + s, SCAN_LO + n, n - s))
        return sorted(out, key=lambda r: -r[2])

    zero_runs = runs(always_zero, 1)
    print(f"\n=== 全 0 连续块（强候选，{len(rams)} 个快照都为 0）===")
    for lo, hi, sz in zero_runs[:12]:
        flag = "  ← ≥128KB!" if sz >= 0x20000 else ("  ← ≥64KB" if sz >= 0x10000 else "")
        print(f"  0x80{lo:06x} .. 0x80{hi:06x}  ({sz/1024:6.1f}KB){flag}")

    big = [r for r in zero_runs if r[2] >= 0x20000]
    print("\n=== 结论 ===")
    if big:
        lo, hi, sz = big[0]
        print(f"✅ 找到 ≥128KB 全 0 块：0x80{lo:06x} 起，可容纳 4096 字库")
        print(f"   方案 A：把字库基址 0x801E0000 改到 0x80{lo:06x}")
        print(f"   ⚠ 仅凭快照=可能漏了某个没测到的模式；务必再做哨兵实验确认")
    else:
        print("⚠ 没有 ≥128KB 全 0 块。可能：①漏了某些模式的快照 ②需方案 B(分离64KB)")
        print("  最大全 0 块见上表；也看看'恒定非零'区能否腾。")


if __name__ == "__main__":
    main()
