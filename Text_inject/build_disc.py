#!/usr/bin/env python3
"""把 P1 的等长补丁合进一张 2352-byte MODE2 测试盘（唯一写盘入口）。

各生产者（build_e0_font.py 的 render_font、E 段重建器…）只负责把 patched bin
落到 out/；本模块按 PATCHES 清单把它们全部写进同一张 [ZH-test].bin：始终从日版
Rev 1 原盘复制，写前用 extrac 原文件校验每个补丁的 LBA，写后逐字节回读。只替换
每扇区偏移 24 的 2048 字节用户数据，保留扇区头。out/ 里缺席的补丁自动跳过，可
增量合盘。当前不重算 EDC/ECC，目标是 DuckStation 测试盘。

补丁必须等长（字节数 == 原文件，即没撑破原扇区数）。哪天某文件需要更多扇区，
下面的等长断言会在这里报错，那正是接入“合盘/扩容层”（gen_index.py +
FSECT/FSIZE.DAT）的地方。也可当库用：read_user_data / write_user_data / DISC_SOURCE。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
REPO = ROOT.parent
DISC_DIR = REPO / "Game" / "P1_PSX"
DISC_SOURCE = DISC_DIR / "Megami Ibunroku - Persona - Be Your True Mind (Japan) (Rev 1).bin"
DISC_OUTPUT = Path(os.environ.get(
    "P1_DISC_OUTPUT",
    DISC_DIR / "Persona (Japan) (Rev 1) [ZH-test].bin"))

SECTOR_SIZE = 2352
USER_OFFSET = 24
USER_SIZE = 2048

PATCHES = (
    ("SLPS_005.00", 1314, ROOT / "extrac" / "SLPS_005.00",
     HERE / "out" / "SLPS.patched.BIN"),
    ("FONT.BIN", 602, ROOT / "extrac" / "FONT.BIN", HERE / "out" / "FONT.patched.BIN"),
    ("ADV/E0.BIN", 87074, ROOT / "extrac" / "ADV" / "E0.BIN", HERE / "out" / "E0.patched.BIN"),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_user_data(path: Path, lba: int, size: int) -> bytes:
    if size % USER_SIZE:
        raise ValueError(f"文件大小不是 {USER_SIZE} 的整数倍: {size}")
    out = bytearray()
    with path.open("rb") as stream:
        for index in range(size // USER_SIZE):
            stream.seek((lba + index) * SECTOR_SIZE + USER_OFFSET)
            chunk = stream.read(USER_SIZE)
            if len(chunk) != USER_SIZE:
                raise ValueError(f"镜像在 LBA {lba + index} 提前结束")
            out.extend(chunk)
    return bytes(out)


def write_user_data(path: Path, lba: int, data: bytes) -> None:
    if len(data) % USER_SIZE:
        raise ValueError(f"补丁大小不是 {USER_SIZE} 的整数倍: {len(data)}")
    with path.open("r+b") as stream:
        for index in range(len(data) // USER_SIZE):
            start = index * USER_SIZE
            stream.seek((lba + index) * SECTOR_SIZE + USER_OFFSET)
            stream.write(data[start:start + USER_SIZE])


def build_test_disc() -> Path:
    if not DISC_SOURCE.is_file():
        raise FileNotFoundError(f"找不到原盘: {DISC_SOURCE}")
    if DISC_SOURCE.stat().st_size % SECTOR_SIZE:
        raise ValueError("原盘大小不是 2352 字节扇区的整数倍")

    loaded = []
    skipped = []
    for name, lba, original_path, patched_path in PATCHES:
        if not patched_path.is_file():
            skipped.append(name)
            continue
        original = original_path.read_bytes()
        patched = patched_path.read_bytes()
        if len(original) != len(patched):
            raise ValueError(
                f"{name} 不是等长补丁: 原 {len(original)}，补丁 {len(patched)}")
        on_disc = read_user_data(DISC_SOURCE, lba, len(original))
        if on_disc != original:
            raise ValueError(
                f"{name} 的原盘 LBA 校验失败: disc={sha256(on_disc)}, "
                f"extrac={sha256(original)}")
        loaded.append((name, lba, patched))

    if skipped:
        print(f"⏭  跳过未产出的补丁: {', '.join(skipped)}（先运行对应生产者）")
    if not loaded:
        raise FileNotFoundError(
            "out/ 里没有任何补丁；先运行生产者（如 python3 build_e0_font.py）")

    # 每次从干净原盘重建，避免旧测试补丁残留或 FONT/E0 版本错配。
    shutil.copyfile(DISC_SOURCE, DISC_OUTPUT)
    for name, lba, patched in loaded:
        write_user_data(DISC_OUTPUT, lba, patched)
        reread = read_user_data(DISC_OUTPUT, lba, len(patched))
        if reread != patched:
            raise ValueError(f"{name} 写回后逐字节校验失败")
        print(
            f"  {name}: LBA {lba}, {len(patched)} bytes, "
            f"sha256={sha256(patched)[:16]}…")

    if DISC_OUTPUT.stat().st_size != DISC_SOURCE.stat().st_size:
        raise ValueError("测试镜像大小发生变化")
    print(f"✅ 测试镜像已重建: {DISC_OUTPUT}")
    print("⚠ 未重算 EDC/ECC；当前产物面向 DuckStation 测试")
    return DISC_OUTPUT


if __name__ == "__main__":
    build_test_disc()
