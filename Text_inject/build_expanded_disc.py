#!/usr/bin/env python3
"""把变大的 E0.BIN 追加到原盘末尾，并同步重定位两套索引。

目标是 DuckStation 测试盘：和 build_disc.py 一样不重算 EDC/ECC。
"""
from __future__ import annotations

import argparse
import shutil
import struct
from pathlib import Path

import build_disc


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RAW = 2352
USER = 2048
USER_OFF = 24
E0_NAME = "\\ADV\\E0.BIN;1"


def read_user(path: Path, lba: int) -> bytes:
    with path.open("rb") as stream:
        stream.seek(lba * RAW + USER_OFF)
        data = stream.read(USER)
    if len(data) != USER:
        raise ValueError(f"LBA {lba} 读取不完整")
    return data


def write_user(path: Path, lba: int, data: bytes) -> None:
    if len(data) != USER:
        raise ValueError("写入的用户数据必须恰好 2048 字节")
    with path.open("r+b") as stream:
        stream.seek(lba * RAW + USER_OFF)
        stream.write(data)


def read_extent(path: Path, lba: int, size: int) -> bytes:
    sectors = (size + USER - 1) // USER
    return b"".join(read_user(path, lba + i) for i in range(sectors))[:size]


def bcd(value: int) -> int:
    if not 0 <= value <= 99:
        raise ValueError(f"BCD 越界: {value}")
    return (value // 10) << 4 | value % 10


def set_sector_address(sector: bytearray, lba: int) -> None:
    frames = lba + 150
    minute, rest = divmod(frames, 75 * 60)
    second, frame = divmod(rest, 75)
    sector[12:15] = bytes((bcd(minute), bcd(second), bcd(frame)))


def append_mode2_file(output: Path, payload: bytes, template: bytes) -> int:
    if len(payload) % USER:
        raise ValueError("E0 大小必须是 2048 的整数倍")
    if len(template) != RAW or template[:12] != b"\x00" + b"\xff" * 10 + b"\x00":
        raise ValueError("追加扇区模板不是 raw CD 扇区")
    first_lba = output.stat().st_size // RAW
    with output.open("ab") as stream:
        for index in range(len(payload) // USER):
            sector = bytearray(template)
            set_sector_address(sector, first_lba + index)
            start = index * USER
            sector[USER_OFF:USER_OFF + USER] = payload[start:start + USER]
            stream.write(sector)
    return first_lba


def dir_entries(path: Path, lba: int, size: int):
    data = read_extent(path, lba, size)
    pos = 0
    while pos < len(data):
        length = data[pos]
        if length == 0:
            pos = (pos // USER + 1) * USER
            continue
        record = data[pos:pos + length]
        name_len = record[32]
        name = record[33:33 + name_len]
        yield name, record, lba + pos // USER, pos % USER
        pos += length


def find_iso_record(path: Path, components: tuple[bytes, ...]):
    pvd = read_user(path, 16)
    root = pvd[156:190]
    lba = struct.unpack_from("<I", root, 2)[0]
    size = struct.unpack_from("<I", root, 10)[0]
    for depth, component in enumerate(components):
        hit = None
        for name, record, sector_lba, within in dir_entries(path, lba, size):
            if name == component:
                hit = (record, sector_lba, within)
                break
        if hit is None:
            raise ValueError(f"ISO 目录中找不到 {component!r}")
        record, sector_lba, within = hit
        if depth + 1 == len(components):
            return record, sector_lba, within
        if not (record[25] & 0x02):
            raise ValueError(f"{component!r} 不是目录")
        lba = struct.unpack_from("<I", record, 2)[0]
        size = struct.unpack_from("<I", record, 10)[0]
    raise AssertionError


def patch_iso_record(output: Path, new_lba: int, new_size: int) -> None:
    record, record_lba, within = find_iso_record(output, (b"ADV", b"E0.BIN;1"))
    old_lba = struct.unpack_from("<I", record, 2)[0]
    old_size = struct.unpack_from("<I", record, 10)[0]
    if old_lba != 87074 or old_size != (ROOT / "extrac/ADV/E0.BIN").stat().st_size:
        raise ValueError(
            f"ISO E0 原目录项不符: LBA={old_lba}, size={old_size}")
    sector = bytearray(read_user(output, record_lba))
    struct.pack_into("<I", sector, within + 2, new_lba)
    struct.pack_into(">I", sector, within + 6, new_lba)
    struct.pack_into("<I", sector, within + 10, new_size)
    struct.pack_into(">I", sector, within + 14, new_size)
    write_user(output, record_lba, bytes(sector))


def patch_volume_size(output: Path, sector_count: int) -> None:
    pvd = bytearray(read_user(output, 16))
    struct.pack_into("<I", pvd, 80, sector_count)
    struct.pack_into(">I", pvd, 84, sector_count)
    write_user(output, 16, bytes(pvd))


def load_index():
    names = [name.decode("ascii") for name in
             (ROOT / "extrac/FNAME.DAT").read_bytes().split(b"\0") if name]
    fsect = bytearray((ROOT / "extrac/FSECT.DAT").read_bytes())
    fsize = bytearray((ROOT / "extrac/FSIZE.DAT").read_bytes())
    return names, fsect, fsize


def patch_game_index(output: Path, new_lba: int, new_size: int) -> None:
    names, fsect, fsize = load_index()
    index = names.index(E0_NAME)
    struct.pack_into("<I", fsect, index * 4, new_lba)
    struct.pack_into("<I", fsize, index * 4, new_size)
    for name, payload in (("\\FSECT.DAT;1", fsect), ("\\FSIZE.DAT;1", fsize)):
        table_index = names.index(name)
        original_lba = struct.unpack_from(
            "<I", (ROOT / "extrac/FSECT.DAT").read_bytes(), table_index * 4)[0]
        sector = bytearray(read_user(output, original_lba))
        sector[:len(payload)] = payload
        write_user(output, original_lba, bytes(sector))


def apply_fixed_patch(output: Path, name: str, lba: int,
                      original: Path, patched: Path) -> None:
    if not patched.is_file():
        raise FileNotFoundError(f"缺少 {name} 补丁: {patched}")
    old = original.read_bytes()
    new = patched.read_bytes()
    if len(old) != len(new):
        raise ValueError(f"{name} 固定补丁不等长")
    # ADV.BIN 的 ISO 文件长不是 2048 的整数倍；只覆盖真实文件字节，保留
    # 最后一个扇区中属于下一个文件/填充区的数据。
    if read_extent(output, lba, len(old)) != old:
        raise ValueError(f"{name} 原盘 LBA 校验失败")
    sectors = (len(new) + USER - 1) // USER
    for index in range(sectors):
        sector = bytearray(read_user(output, lba + index))
        start = index * USER
        chunk = new[start:start + USER]
        sector[:len(chunk)] = chunk
        write_user(output, lba + index, bytes(sector))
    if read_extent(output, lba, len(new)) != new:
        raise ValueError(f"{name} 补丁回读失败")


def build(output: Path, e0_path: Path, *, font_path: Path | None = None,
          adv_path: Path | None = None, patch_slps: bool = True) -> Path:
    source = build_disc.DISC_SOURCE
    if output.resolve() == source.resolve():
        raise ValueError("拒绝覆盖原盘")
    e0 = e0_path.read_bytes()
    print(f"复制干净原盘: {source.name}")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)

    if patch_slps:
        apply_fixed_patch(output, "SLPS_005.00", 1314,
                          ROOT / "extrac/SLPS_005.00", HERE / "out/SLPS.patched.BIN")
    apply_fixed_patch(output, "FONT.BIN", 602,
                      ROOT / "extrac/FONT.BIN",
                      font_path or HERE / "out/FONT.patched.BIN")
    if adv_path is not None:
        apply_fixed_patch(output, "ADV.BIN", 635,
                          ROOT / "extrac/ADV.BIN", adv_path)

    with source.open("rb") as stream:
        stream.seek(87074 * RAW)
        template = stream.read(RAW)
    new_lba = append_mode2_file(output, e0, template)
    new_sectors = output.stat().st_size // RAW
    patch_iso_record(output, new_lba, len(e0))
    patch_volume_size(output, new_sectors)
    patch_game_index(output, new_lba, len(e0))

    reread = build_disc.read_user_data(output, new_lba, len(e0))
    if reread != e0:
        raise ValueError("新 LBA 的 E0 回读不一致")
    record, _record_lba, _within = find_iso_record(output, (b"ADV", b"E0.BIN;1"))
    if (struct.unpack_from("<I", record, 2)[0] != new_lba or
            struct.unpack_from("<I", record, 10)[0] != len(e0)):
        raise ValueError("ISO E0 目录项回读不一致")

    print(f"✅ E0 重定位: LBA 87074 -> {new_lba}, {len(e0)} bytes")
    print(f"✅ FSECT/FSIZE + ISO9660 目录项 + 卷扇区数已更新")
    print(f"✅ 扩展测试盘: {output} ({new_sectors} sectors)")
    print("⚠ 未重算 EDC/ECC；当前产物面向 DuckStation 测试")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--e0", default=str(HERE / "out/E0.safe.patched.BIN"),
        help="扩展 E0.BIN 路径")
    parser.add_argument(
        "--output", default=str(HERE / "out/Persona-ZH-E0-safe-expanded.bin"),
        help="输出 raw 2352-byte 测试盘")
    parser.add_argument("--font", help="自定义等长 FONT.BIN")
    parser.add_argument("--adv", help="自定义等长 ADV.BIN")
    parser.add_argument("--no-slps", action="store_true",
                        help="不合入当前 SLPS 测试补丁")
    args = parser.parse_args()
    build(Path(args.output).resolve(), Path(args.e0).resolve(),
          font_path=Path(args.font).resolve() if args.font else None,
          adv_path=Path(args.adv).resolve() if args.adv else None,
          patch_slps=not args.no_slps)


if __name__ == "__main__":
    main()
