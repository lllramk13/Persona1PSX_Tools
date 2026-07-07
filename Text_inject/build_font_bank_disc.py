#!/usr/bin/env python3
"""构建常驻字形银行测试盘：重定位扩展 FONT，其他补丁均等长原位写回。"""
from __future__ import annotations

import argparse
import shutil
import struct
from pathlib import Path

import build_disc
import build_expanded_disc as disc


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FONT_NAME = "\\FONT.BIN;1"


def append_payload(output: Path, payload: bytes, template: bytes) -> int:
    first_lba = output.stat().st_size // disc.RAW
    sectors = (len(payload) + disc.USER - 1) // disc.USER
    with output.open("ab") as stream:
        for index in range(sectors):
            sector = bytearray(template)
            disc.set_sector_address(sector, first_lba + index)
            start = index * disc.USER
            chunk = payload[start:start + disc.USER]
            sector[disc.USER_OFF:disc.USER_OFF + disc.USER] = b"\0" * disc.USER
            sector[disc.USER_OFF:disc.USER_OFF + len(chunk)] = chunk
            stream.write(sector)
    return first_lba


def patch_font_iso_record(output: Path, new_lba: int, new_size: int) -> None:
    record, record_lba, within = disc.find_iso_record(output, (b"FONT.BIN;1",))
    old_lba = struct.unpack_from("<I", record, 2)[0]
    old_size = struct.unpack_from("<I", record, 10)[0]
    if old_lba != 602 or old_size != (ROOT / "extrac/FONT.BIN").stat().st_size:
        raise ValueError(f"ISO FONT原目录项不符: LBA={old_lba}, size={old_size}")
    sector = bytearray(disc.read_user(output, record_lba))
    struct.pack_into("<I", sector, within + 2, new_lba)
    struct.pack_into(">I", sector, within + 6, new_lba)
    struct.pack_into("<I", sector, within + 10, new_size)
    struct.pack_into(">I", sector, within + 14, new_size)
    disc.write_user(output, record_lba, bytes(sector))


def patch_font_game_index(output: Path, new_lba: int, new_size: int) -> None:
    names, fsect, fsize = disc.load_index()
    index = names.index(FONT_NAME)
    struct.pack_into("<I", fsect, index * 4, new_lba)
    aligned_size = (new_size + disc.USER - 1) // disc.USER * disc.USER
    struct.pack_into("<I", fsize, index * 4, aligned_size)
    for name, payload in (("\\FSECT.DAT;1", fsect), ("\\FSIZE.DAT;1", fsize)):
        table_index = names.index(name)
        lba = struct.unpack_from(
            "<I", (ROOT / "extrac/FSECT.DAT").read_bytes(), table_index * 4)[0]
        if len(payload) > disc.USER:
            raise ValueError(f"{name} 超过单扇区，当前构建器未覆盖")
        sector = bytearray(disc.read_user(output, lba))
        sector[:len(payload)] = payload
        disc.write_user(output, lba, bytes(sector))


def build(output: Path, *, font: Path, slps: Path, adv: Path, e0: Path) -> Path:
    source = build_disc.DISC_SOURCE
    if output.resolve() == source.resolve():
        raise ValueError("拒绝覆盖原盘")
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, output)

    disc.apply_fixed_patch(output, "SLPS_005.00", 1314,
                           ROOT / "extrac/SLPS_005.00", slps)
    disc.apply_fixed_patch(output, "ADV.BIN", 635,
                           ROOT / "extrac/ADV.BIN", adv)
    disc.apply_fixed_patch(output, "ADV/E0.BIN", 87074,
                           ROOT / "extrac/ADV/E0.BIN", e0)

    payload = font.read_bytes()
    with source.open("rb") as stream:
        stream.seek(602 * disc.RAW)
        template = stream.read(disc.RAW)
    new_lba = append_payload(output, payload, template)
    patch_font_iso_record(output, new_lba, len(payload))
    patch_font_game_index(output, new_lba, len(payload))
    sector_count = output.stat().st_size // disc.RAW
    disc.patch_volume_size(output, sector_count)

    if disc.read_extent(output, new_lba, len(payload)) != payload:
        raise AssertionError("扩展 FONT 回读失败")
    record, _, _ = disc.find_iso_record(output, (b"FONT.BIN;1",))
    if (struct.unpack_from("<I", record, 2)[0] != new_lba or
            struct.unpack_from("<I", record, 10)[0] != len(payload)):
        raise AssertionError("FONT ISO目录回读失败")
    print(f"✅ 扩展FONT重定位: LBA 602 -> {new_lba}, {len(payload)} bytes")
    print(f"✅ SLPS/ADV/E0 等长补丁 + FSECT/FSIZE + ISO/PVD")
    print(f"✅ 测试盘: {output} ({sector_count} sectors)")
    print("⚠ 未重算EDC/ECC；仅用于DuckStation")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--font", type=Path,
        default=HERE / "out/dynamic_font_bank/FONT.dynamic.bank-expanded.BIN")
    parser.add_argument(
        "--slps", type=Path, default=HERE / "out/SLPS.font-bank.BIN")
    parser.add_argument(
        "--adv", type=Path, default=HERE / "out/ADV.dynamic-font-bank.BIN")
    parser.add_argument(
        "--e0", type=Path, default=HERE / "out/E0.dynamic-bank-triggers.BIN")
    parser.add_argument(
        "--output", type=Path,
        default=HERE / "out/Persona-dyn-resident-bank.bin")
    args = parser.parse_args()
    build(args.output.resolve(), font=args.font.resolve(),
          slps=args.slps.resolve(), adv=args.adv.resolve(), e0=args.e0.resolve())


if __name__ == "__main__":
    main()
