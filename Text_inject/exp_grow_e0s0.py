#!/usr/bin/env python3
"""任意长度回插的首个实机实验：E0 段 0 文本加长 8 字节。

在开场第一句对白单元（+0x1D00，マーク:「ペルソナ様」…）尾部插入
"0123"（4 个原字库数字，8 字节），用 ebin_rebuild 重建段 0：
  · 后续 ~50 个文本单元与全部脚本记录整体后移 8 字节；
  · 97 个段内绝对指针（含选项 resume 点、FF55 目标、段头 content-end）重定位；
  · 段 0 内容 0x259C+8 仍 < 0x2800，段扇区数不变 → E0.BIN 总大小不变，
    可直接用等长 LBA 写盘。
静态自检后写测试盘（只补 E0，字库用原盘的，因为插入的是原字库数字）。

预期实机现象：开场 Mark 的第一句话里出现 "上杉0123?"（0123 是插入的
标记），其后所有对话、选项分支照常。若后续对话/选项错乱 = 有指针未收录。

用法: python3 exp_grow_e0s0.py
"""
import json
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "Text"))
import containers  # noqa: E402
import decode as D  # noqa: E402
import dump  # noqa: E402
from ebin_rebuild import EFile, RAM  # noqa: E402
import build_test_disc as disc  # noqa: E402

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)
SEC = 0
UNIT = 0x1D00
INSERT = bytes.fromhex("80c0 80c1 80c2 80c3".replace(" ", ""))  # "0123"


def main() -> None:
    ef = EFile(ROOT / "extrac" / "ADV" / "E0.BIN")
    sec = ef.section(SEC)
    unit = next(((a, b) for a, b in sec.units if a == UNIT), None)
    if unit is None:
        sys.exit(f"段 {SEC} 里没有起点 {UNIT:#x} 的文本单元")
    a, b = unit
    new_unit = sec.raw[a:b] + INSERT
    rebuilt = ef.rebuild({SEC: {a: new_unit}})

    # --- 静态自检 -----------------------------------------------------------
    if len(rebuilt) != len(ef.data):
        sys.exit(f"文件大小变了: {len(ef.data)} → {len(rebuilt)}（本实验必须等长）")
    # 其他段逐字节不变
    for i, (s0, s1) in enumerate(ef.sections):
        if i != SEC and rebuilt[s0:s1] != ef.data[s0:s1]:
            sys.exit(f"段 {i} 意外被改动")
    # 重 dump：span 数量一致，除目标块外文本一致，目标块多出 "0123"
    ct = {int(k): v for k, v in json.loads(
        (ROOT / "Codetable" / "codetable_og.json").read_text("utf-8")).items()}
    ctrl = dump.load_format()["ctrl"]["efile"]
    old_spans = containers.read_efile(ef.data)
    new_spans = containers.read_efile(rebuilt)
    if len(old_spans) != len(new_spans):
        sys.exit(f"span 数变了: {len(old_spans)} → {len(new_spans)}")
    changed = []
    for (sc0, a0, b0), (sc1, a1, b1) in zip(old_spans, new_spans):
        if sc0 != sc1:
            sys.exit("span 场景序号错位")
        t0 = D.tokens_to_text(D.decode(ef.data, a0, b0, ctrl), ct, ctrl)
        t1 = D.tokens_to_text(D.decode(rebuilt, a1, b1, ctrl), ct, ctrl)
        if t0 != t1:
            changed.append((sc0, t0, t1))
    if len(changed) != 1:
        sys.exit(f"预期只有 1 个 span 变化，实际 {len(changed)}")
    sc, t0, t1 = changed[0]
    if t1.replace("0123", "") != t0:
        sys.exit("变化的 span 不是纯插入 0123")
    print(f"✅ 静态自检通过：仅场景 {sc} 插入 '0123'，其余 {len(old_spans)-1} 个 span 原样")
    idx = t1.find("0123")
    print(f"   插入点上下文: …{t1[max(0, idx-18):idx+10]}…")

    # 段 0 指针重定位抽查：新旧指针目标处的 8 字节内容一致
    s0, s1 = ef.sections[SEC]
    moved = 0
    for site, target in sec.ptr_sites:
        old_v = struct.unpack_from("<I", ef.data, s0 + site)[0]
        new_site = site if site < a else site + len(INSERT)
        new_v = struct.unpack_from("<I", rebuilt, s0 + new_site)[0]
        old_rel, new_rel = old_v - RAM, new_v - RAM
        if ef.data[s0+old_rel:s0+old_rel+8] != rebuilt[s0+new_rel:s0+new_rel+8]:
            sys.exit(f"指针 @+{site:#x} 重定位后目标内容不一致")
        moved += (new_v != old_v)
    print(f"✅ 段 0 指针 {len(sec.ptr_sites)} 个全部校验，其中 {moved} 个已移位")

    out_bin = OUT / "E0.grown.BIN"
    out_bin.write_bytes(rebuilt)
    print(f"→ {out_bin}")

    # --- 写测试盘（只补 E0；字库用原盘） ------------------------------------
    name, lba = "ADV/E0.BIN", 87074
    original = (ROOT / "extrac" / "ADV" / "E0.BIN").read_bytes()
    on_disc = disc.read_user_data(disc.DISC_SOURCE, lba, len(original))
    if on_disc != original:
        sys.exit("原盘 LBA 校验失败")
    target = disc.DISC_DIR / "Persona (Japan) (Rev 1) [E0-grow-test].bin"
    import shutil
    shutil.copyfile(disc.DISC_SOURCE, target)
    disc.write_user_data(target, lba, rebuilt)
    if disc.read_user_data(target, lba, len(rebuilt)) != rebuilt:
        sys.exit("写回后校验失败")
    print(f"✅ 测试盘: {target}")
    print("   实机预期：开场 Mark 第一句对白出现「上杉0123?」，其后对话与选项照常。")


if __name__ == "__main__":
    main()
