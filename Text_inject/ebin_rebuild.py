#!/usr/bin/env python3
"""E*.BIN 段重建器：文本任意伸缩 + 绝对指针重定位。

依据 ADV.BIN 反汇编实锤的引擎事实（详见 README §3.2b）：

  · 段整体加载到 RAM 0x80100000；段内一切交叉引用都是 4 字节对齐的
    绝对 RAM 指针（0x8010xxxx），没有相对偏移。
  · ADV.BIN 代码只硬编码段偏移 < 0x10C8（段头 0x1F8 + 元数据区）。
    [0, 0x10C8) 是不可移动的固定前缀；0x10C8 之后只经指针访问，可自由重排。
  · 脚本 = 4 字节对齐记录 `FF op ...`，长度查 ADV.BIN 的表（RAM 0x800BAC90，
    文件偏移 0x568F0）。跳转/文本指针操作数：FF22/23/55/58 在 +4，
    FF38/3E/49 在 +8。FF21 = 脚本结束（后面通常直接排文本）。
  · 文本被 FF55/FF58 等指针按"单元"引用；reader 的大 span 实为多个背靠背
    单元。重建时按指针目标切分单元，逐单元替换。
  · 文本流里可能偶然出现像指针的字节串（`80 xx`/`10` 组合），因此
    只在文本 span 之外做指针重定位。

重建不变量：
  1. 固定前缀 [0, 0x10C8) 原样保留（其中的指针字仍参与重定位）。
  2. 替换单元的新字节数 ≡ 原字节数 (mod 4)，保证后续脚本记录 4 对齐。
  3. 所有非文本区域逐字节保留，只有指针字按新布局改写。
  4. 指向未替换区域任意内部的指针可精确映射；指向替换单元内部的指针
     目前只支持单元起点（其余情况报错，待 token 级映射）。
  5. 段尾去零重填充到扇区倍数；全无改动时逐字节等于原文件。

用法：
    python3 ebin_rebuild.py --verify            # 四个 E 文件 byte-identity 自测
    python3 ebin_rebuild.py --units E0 0        # 列出某段的文本单元
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "Text"))
import containers  # noqa: E402

RAM = 0x80100000
SECTOR = 0x800
FIXED_PREFIX = 0x10C8          # ADV.BIN 硬编码偏移的上界（lui 0x8010 普查）
ADV_LEN_TABLE_OFF = 0x568F0    # RAM 0x800BAC90
END_OP = 0x21
# opcode → 指针操作数在记录内的偏移
SCRIPT_PTR_OPS = {0x22: 4, 0x23: 4, 0x55: 4, 0x58: 4, 0x38: 8, 0x3E: 8, 0x49: 8}


def load_len_table(adv_path: Path | None = None) -> bytes:
    path = adv_path or (ROOT / "extrac" / "ADV.BIN")
    data = path.read_bytes()
    return data[ADV_LEN_TABLE_OFF:ADV_LEN_TABLE_OFF + 0x90]


def split_sections(data: bytes) -> list[tuple[int, int]]:
    """扇区 0 的 u16 目录 → [(start, end)] 文件偏移。"""
    ptrs, last = [], 0
    for o in range(0, SECTOR, 2):
        v = struct.unpack_from("<H", data, o)[0]
        if v <= last or v * SECTOR >= len(data):
            break
        ptrs.append(v)
        last = v
    if not ptrs or ptrs[0] != 1:
        raise ValueError("不是有效的 E 文件：u16 扇区目录缺失")
    return [(p * SECTOR, ptrs[i + 1] * SECTOR if i + 1 < len(ptrs) else len(data))
            for i, p in enumerate(ptrs)]


class Section:
    """单个段的解析模型：文本单元 + 指针字清单。"""

    def __init__(self, raw: bytes, spans: list[tuple[int, int]]):
        self.raw = raw
        self.size = len(raw)
        self.spans = sorted(spans)                  # reader 的文本 span（段内偏移）
        self.ptr_sites = self._scan_pointers()      # [(site_off, target_off)] 全部像指针的字
        self.text_targets = self._text_targets()    # 只有真 FF55/FF58 引用的文本单元起点
        self.units = self._split_units()            # [(start, end)] 可替换文本单元

    # -- 解析 --------------------------------------------------------------

    def _in_span(self, off: int) -> bool:
        return any(a <= off < b for a, b in self.spans)

    def _scan_pointers(self) -> list[tuple[int, int]]:
        """全段 4 对齐扫描 0x8010xxxx 字；文本 span 内的是文字巧合，跳过。"""
        sites = []
        for o in range(0, self.size - 3, 4):
            if self._in_span(o):
                continue
            v = struct.unpack_from("<I", self.raw, o)[0]
            if RAM <= v < RAM + self.size:
                sites.append((o, v - RAM))
        return sites

    def _text_targets(self) -> set[int]:
        """真正的文本单元起点 = FF55(显示文本)/FF58(存文本指针) 记录的指针操作数。

        _scan_pointers 会把段头结构表、脚本区里巧合成 0x8010xxxx 的字也收进来（重定位
        时全都要修），但它们不是文本单元边界——用它们切会把词剁成单字碎片
        （っ/て/ね）。FF55/FF58 的指针操作数固定在记录 +4，记录 4 字节对齐。
        """
        tgts = set()
        for site, _ in self.ptr_sites:
            head = site - 4                          # 指针在 +4，故记录头在 site-4
            if (head >= 0 and head % 4 == 0 and self.raw[head] == 0xFF
                    and self.raw[head + 1] in (0x55, 0x58)):
                tgts.add(struct.unpack_from("<I", self.raw, site)[0] - RAM)
        return tgts

    def _split_units(self) -> list[tuple[int, int]]:
        """reader span 只按真文本指针（FF55/FF58 目标）切成可替换单元。"""
        targets = sorted(self.text_targets)
        units = []
        for a, b in self.spans:
            cuts = [a] + [t for t in targets if a < t < b] + [b]
            units.extend((cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1))
        return units

    # -- 重建 --------------------------------------------------------------

    def rebuild(self, replacements: dict[int, bytes]) -> bytes:
        """replacements: {unit_start: new_bytes} → 新的段内容（未做扇区填充）。

        new_bytes 是完整的文本单元编码（含控制码），本函数负责 mod-4 补齐。
        """
        unknown = set(replacements) - {a for a, _ in self.units}
        if unknown:
            raise ValueError(f"未知文本单元起点: {sorted(hex(u) for u in unknown)}")

        # 1) 布局：固定前缀 + 有序块（verbatim / unit），计算新偏移
        blocks = []                                  # (old_start, old_end, new_bytes|None)
        pos = FIXED_PREFIX if self.size > FIXED_PREFIX else self.size
        boundaries = [u for u in self.units if u[0] >= pos]
        for a, b in boundaries:
            if a > pos:
                blocks.append((pos, a, None))
            new = replacements.get(a)
            if new is not None:
                pad = (b - a - len(new)) % 4
                new = new + b"\x00" * pad
            blocks.append((a, b, new))
            pos = b
        if pos < self.size:
            blocks.append((pos, self.size, None))

        out = bytearray(self.raw[:FIXED_PREFIX] if self.size > FIXED_PREFIX
                        else self.raw)
        block_map = []                               # (old_start, old_end, new_start)
        for a, b, new in blocks:
            block_map.append((a, b, len(out)))
            out.extend(new if new is not None else self.raw[a:b])
        replaced = {a for a, b, new in blocks if new is not None}

        # 2) 旧偏移 → 新偏移。target 落在被替换单元内部时返回 None = 噪声指针，
        #    跳过重定位、原样保留其旧值（文本只被 FF55/FF58 引用且都指向单元起点；
        #    落进被换文本内部的 0x8010xxxx 一定是段头结构/文字巧合，不是真文本指针）。
        def remap(off: int, *, is_site: bool) -> "int | None":
            if off < FIXED_PREFIX:
                return off
            for a, b, ns in block_map:
                if a <= off < b:
                    if a in replaced and off != a:
                        if is_site:                  # site 在 span 外，绝不该落进被换单元
                            raise ValueError(
                                f"指针 site {off:#x} 落在被替换单元内部（不应发生）")
                        return None                  # 噪声目标：跳过
                    return ns + (off - a)
            raise ValueError(f"指针目标 {off:#x} 无法映射")

        # 3) 重定位所有指针字（site 位置先映射到新布局；目标是被换单元内部的跳过）
        for site, target in self.ptr_sites:
            new_target = remap(target, is_site=False)
            if new_target is None:                   # 噪声指针：verbatim 已复制其旧值，不动
                continue
            struct.pack_into("<I", out, remap(site, is_site=True), RAM + new_target)
        return bytes(out)


class EFile:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.sections = split_sections(self.data)
        spans = containers.read_efile(self.data)
        self.sec_spans: dict[int, list[tuple[int, int]]] = {
            i: [] for i in range(len(self.sections))}
        for scene, ts, te in spans:
            base = self.sections[scene][0]
            self.sec_spans[scene].append((ts - base, te - base))

    def section(self, index: int) -> Section:
        s0, s1 = self.sections[index]
        return Section(self.data[s0:s1], self.sec_spans[index])

    def rebuild(self, replacements: dict[int, dict[int, bytes]]) -> bytes:
        """replacements: {section_index: {unit_start: new_bytes}} → 新 E 文件。"""
        new_secs = []
        for i, (s0, s1) in enumerate(self.sections):
            sec = Section(self.data[s0:s1], self.sec_spans[i])
            body = sec.rebuild(replacements.get(i, {}))
            # 段尾去零，再填充到扇区倍数
            content = len(body.rstrip(b"\x00"))
            sectors = max(1, -(-content // SECTOR))
            if sectors * SECTOR < content:
                raise AssertionError
            new_secs.append(body[:content] + b"\x00" * (sectors * SECTOR - content))
            if len(new_secs[-1]) < len(body.rstrip(b"\x00")):
                raise AssertionError
        # 目录：首段从扇区 1 开始
        directory = []
        sector_pos = 1
        for body in new_secs:
            directory.append(sector_pos)
            sector_pos += len(body) // SECTOR
        if any(v > 0xFFFF for v in directory):
            raise ValueError("段目录超出 u16")
        head = bytearray(self.data[:SECTOR])
        for i, v in enumerate(directory):
            struct.pack_into("<H", head, i * 2, v)
        # 目录之后、原表尾部如果有非递增哨兵值，保持原样（read 到非递增即停）
        return bytes(head) + b"".join(new_secs)


def verify_identity() -> bool:
    ok = True
    for name in ["E0", "E1", "E2", "E3"]:
        path = ROOT / "extrac" / "ADV" / f"{name}.BIN"
        ef = EFile(path)
        rebuilt = ef.rebuild({})
        same = rebuilt == ef.data
        n_units = sum(len(Section(ef.data[a:b], ef.sec_spans[i]).units)
                      for i, (a, b) in enumerate(ef.sections))
        n_ptrs = sum(len(Section(ef.data[a:b], ef.sec_spans[i]).ptr_sites)
                     for i, (a, b) in enumerate(ef.sections))
        print(f"{name}: {len(ef.sections)} sections, {n_units} text units, "
              f"{n_ptrs} pointer words, identity={'✅' if same else '❌'}")
        ok &= same
    return ok


def main() -> None:
    if "--verify" in sys.argv:
        sys.exit(0 if verify_identity() else 1)
    if "--units" in sys.argv:
        i = sys.argv.index("--units")
        name, sec_idx = sys.argv[i + 1], int(sys.argv[i + 2])
        ef = EFile(ROOT / "extrac" / "ADV" / f"{name}.BIN")
        sec = ef.section(sec_idx)
        print(f"{name} section {sec_idx}: size {sec.size:#x}, "
              f"{len(sec.units)} units, {len(sec.ptr_sites)} pointer words")
        for a, b in sec.units:
            head = sec.raw[a:min(a + 12, b)].hex(" ")
            print(f"  unit +{a:#06x}..+{b:#06x} ({b - a:4d}B)  {head}")
        return
    print(__doc__)


if __name__ == "__main__":
    main()
