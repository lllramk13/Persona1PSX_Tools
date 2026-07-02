#!/usr/bin/env python3
"""
gen_index.py — 从 ISO9660 目录树重建 Persona(PSX) 的 FSECT.DAT / FSIZE.DAT 扁平索引。

心法: FSECT/FSIZE 只是 ISO9660 目录的一份冗余缓存 —— 每个文件的 extent LBA + 扇区对齐大小。
      改盘后不必自己算布局(那会被子目录的目录区块坑到)，重扫成品盘的目录树、
      按 FNAME.DAT 的顺序把答案抄成两张表即可。

用法:
    python gen_index.py <iso.bin> --verify      # 与 extrac/ 现有 .DAT 逐字节对拍(自检)
    python gen_index.py <iso.bin> --out DIR      # 生成 FSECT.DAT/FSIZE.DAT 到 DIR
    python gen_index.py <iso.bin> --patch        # 把两表原位灌回 ISO(仅用户数据, 供模拟器)
"""
import argparse, struct, sys
from pathlib import Path

RAW  = 2352   # PSX 原始扇区字节数
USER = 2048   # 其中 ISO9660 用户数据字节数


class Disc:
    """把 raw 2352 字节/扇区的 .bin 当成'按 LBA 可寻址'的 ISO9660 卷来读写。"""

    def __init__(self, path):
        self.path = Path(path)
        self.data = bytearray(self.path.read_bytes())
        self.nsec = len(self.data) // RAW
        self.off  = self._detect_user_offset()          # 里程碑 A

    def _detect_user_offset(self):
        # PVD 固定在 LBA16, 头部有魔法串 "CD001"; 它前 1 字节即用户数据起点
        # (Mode2 Form1 -> 24, Mode1 -> 16)。不硬编, 让盘自己告诉你。
        sec = self.data[16 * RAW: 17 * RAW]
        p = sec.find(b"CD001")
        if p < 0:
            sys.exit("未找到 CD001：不是标准 ISO9660，或扇区不是 2352 字节")
        return p - 1

    def read(self, lba):
        """读 LBA 号逻辑扇区的 2048 用户字节。 偏移 = LBA*2352 + DATA_OFF。"""
        base = lba * RAW + self.off
        return bytes(self.data[base:base + USER])

    def read_extent(self, lba, size):
        """读一段可能跨多扇区的数据(目录本身也可能 >2048)。"""
        n = (size + USER - 1) // USER
        return b"".join(self.read(lba + i) for i in range(n))[:size]

    def write_user(self, lba, payload):
        """只覆盖该扇区的 2048 用户数据区，不动 sync/header/ECC。"""
        assert len(payload) <= USER
        base = lba * RAW + self.off
        self.data[base:base + len(payload)] = payload


# --- ISO9660 目录记录字段 (偏移相对每条记录起点) ---
def _rec_lba(r):   return struct.unpack_from("<I", r, 2)[0]    # +2  extent LBA (小端)
def _rec_size(r):  return struct.unpack_from("<I", r, 10)[0]   # +10 数据字节长 (小端)
def _rec_isdir(r): return bool(r[25] & 0x02)                   # +25 flags: 0x02=目录
def _rec_name(r):  return r[33:33 + r[32]]                     # +32 名长, +33 名字


def scan_files(disc):
    """遍历整棵目录树 -> {反斜杠全路径(文件带;1): (lba, 真实字节数)}。"""
    files = {}

    def walk(lba, size, prefix):
        ext = disc.read_extent(lba, size)
        pos = 0
        while pos < len(ext):
            rlen = ext[pos]
            if rlen == 0:                              # 记录长 0 = 本扇区到底了
                pos = (pos // USER + 1) * USER         # 跳到下个扇区边界
                continue
            rec = ext[pos:pos + rlen]
            pos += rlen
            name = _rec_name(rec)
            if name in (b"\x00", b"\x01"):             # "." / ".." 跳过
                continue
            path = prefix + "\\" + name.decode("ascii", "replace")
            if _rec_isdir(rec):
                walk(_rec_lba(rec), _rec_size(rec), path)      # 子目录 -> 递归
            else:
                files[path] = (_rec_lba(rec), _rec_size(rec))  # 文件 -> 记账

    # PVD(LBA16) 用户数据里, 根目录记录固定在偏移 156, 长 34 字节
    root = disc.read(16)[156:156 + 34]
    walk(_rec_lba(root), _rec_size(root), "")           # 里程碑 B/C/D
    return files


def build_tables(files, fnames):
    """按 FNAME 顺序生成 (fsect_bytes, fsize_bytes)。缺任何一条即报错。"""
    sect, size, missing = [], [], []
    for nm in fnames:
        hit = files.get(nm)
        if hit is None:
            missing.append(nm); sect.append(0); size.append(0)
            continue
        lba, byts = hit
        sect.append(lba)                               # FSECT = 原样 LBA
        size.append(((byts + USER - 1) // USER) * USER)  # FSIZE = 扇区对齐大小
    if missing:
        sys.exit(f"FNAME 有 {len(missing)} 条在盘上找不到: {missing[:8]}")
    fmt = "<%dI" % len(fnames)
    return struct.pack(fmt, *sect), struct.pack(fmt, *size)


def read_fnames(fname_dat):
    blob = Path(fname_dat).read_bytes()
    return [n.decode("ascii", "replace") for n in blob.split(b"\x00") if n]


def _show_diff(fnames, gen, real, label):
    g = struct.unpack("<%dI" % len(fnames), gen)
    r = struct.unpack("<%dI" % len(fnames), real)
    mm = [i for i in range(len(fnames)) if g[i] != r[i]]
    print(f"  {label} 不匹配 {len(mm)} 条: " +
          "; ".join(f"{fnames[i]} 实{r[i]}/生{g[i]}" for i in mm[:6]))


def main():
    ap = argparse.ArgumentParser(description="重建 Persona(PSX) FSECT/FSIZE 扁平索引")
    ap.add_argument("iso", help="PSX .bin (raw 2352 字节/扇区)")
    ap.add_argument("--extrac", default=str(Path(__file__).parent / "extrac"),
                    help="放 FNAME/FSECT/FSIZE.DAT 的目录 (默认 ./extrac)")
    ap.add_argument("--verify", action="store_true", help="与现有 .DAT 逐字节对拍")
    ap.add_argument("--out", help="把生成的 FSECT.DAT/FSIZE.DAT 写到此目录")
    ap.add_argument("--patch", action="store_true",
                    help="把两表原位灌回 ISO(仅用户数据, 供模拟器验证)")
    args = ap.parse_args()

    extrac = Path(args.extrac)
    fnames = read_fnames(extrac / "FNAME.DAT")
    disc   = Disc(args.iso)
    print(f"[{disc.path.name}] {disc.nsec} 扇区, DATA_OFF={disc.off}")

    files = scan_files(disc)
    print(f"目录树扫到 {len(files)} 文件; FNAME 索引 {len(fnames)} 条")

    fsect, fsize = build_tables(files, fnames)          # 里程碑 E

    if args.verify:
        real_s = (extrac / "FSECT.DAT").read_bytes()
        real_z = (extrac / "FSIZE.DAT").read_bytes()
        print(f"FSECT 逐字节匹配: {fsect == real_s}")
        print(f"FSIZE 逐字节匹配: {fsize == real_z}")
        if fsect != real_s: _show_diff(fnames, fsect, real_s, "FSECT")
        if fsize != real_z: _show_diff(fnames, fsize, real_z, "FSIZE")

    if args.out:
        outd = Path(args.out); outd.mkdir(parents=True, exist_ok=True)
        (outd / "FSECT.DAT").write_bytes(fsect)
        (outd / "FSIZE.DAT").write_bytes(fsize)
        print(f"已写 {outd}/FSECT.DAT + FSIZE.DAT")

    if args.patch:
        # 两表自身也是盘上文件, LBA 从刚扫的目录树取; 390 条大小不变 -> 不移位, 原位覆盖
        for name, payload in (("\\FSECT.DAT;1", fsect), ("\\FSIZE.DAT;1", fsize)):
            lba, _ = files[name]
            for i in range(0, len(payload), USER):
                disc.write_user(lba + i // USER, payload[i:i + USER])
        disc.path.write_bytes(disc.data)
        print(f"已原位灌回 {disc.path.name} 的 FSECT/FSIZE.DAT")


if __name__ == "__main__":
    main()
