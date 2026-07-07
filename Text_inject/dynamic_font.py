#!/usr/bin/env python3
"""P1 动态字库：生成固定核心字库和 E0 分 section 换页包。

物理字库仍是 2048 槽 / 64KB：
  slot 0..CORE-1      全局核心字
  slot CORE..2047     进入 E0 section 时动态覆盖

这个工具只产生数据，不修改当前 codetable_zh.json/FONT.patched.BIN。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

try:
    from .build_font import TTF, render_glyph
except ImportError:
    from build_font import TTF, render_glyph

from PIL import ImageFont

try:
    from .ebin_rebuild import EFile, pointer_to_file
except ImportError:
    from ebin_rebuild import EFile, pointer_to_file


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TRANSLATIONS = ROOT / "Text/translations.json"
PHYSICAL_SLOTS = 2048
GLYPH_BYTES = 32
DEFAULT_CORE = 1536
PLACEHOLDER_RE = re.compile(r"⟦\d+⟧|⟪B\d+⟫")

# 这些字会用在数值、人名或通用 UI，即使当前译文频率低也固定在核心区。
PINNED = (
    " 0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    "，。！？：；、…·・「」『』【】（）()[]<>+-/%&'”“~～"
)


def visible_text(markup: str) -> str:
    """只去掉翻译占位符；译文里的 ``<额外游戏>`` 是真文字，不删。"""
    return PLACEHOLDER_RE.sub("", markup).replace(r"\n", "").replace("\n", "")


def load_corpus(path: Path):
    table = json.loads(path.read_text(encoding="utf-8"))
    frequency = Counter()
    e0_sections: dict[int, set[str]] = defaultdict(set)
    translated = 0
    for line_id, item in table.items():
        zh = item.get("zh", "")
        if not zh:
            continue
        translated += 1
        text = visible_text(zh)
        frequency.update(text)
        if line_id.startswith("ADV/E0.BIN#"):
            section = int(line_id.split("#", 1)[1].split(":", 1)[0])
            e0_sections[section].update(text)
    return frequency, e0_sections, translated


def ranked_chars(frequency: Counter) -> list[str]:
    """频率降序 + Unicode 升序，确保同一输入永远产生同一码表。"""
    return sorted(frequency, key=lambda ch: (-frequency[ch], ord(ch)))


def select_core(frequency: Counter, core_size: int) -> list[str]:
    if not 2 <= core_size < PHYSICAL_SLOTS:
        raise ValueError(f"CORE 必须在 2..{PHYSICAL_SLOTS - 1}")
    result = [" "]
    seen = {" "}
    for ch in PINNED:
        if ch not in seen:
            result.append(ch)
            seen.add(ch)
    for ch in ranked_chars(frequency):
        if ch not in seen:
            result.append(ch)
            seen.add(ch)
        if len(result) == core_size:
            break
    if len(result) < core_size:
        raise ValueError(f"语料只能提供 {len(result)} 个核心字")
    return result


def load_section_capacities(path: Path) -> dict[int, dict[str, int]]:
    """读取每个 section 逻辑终点后的原有 padding 容量。

    页包格式需要 4-byte 长度头，之后每字32字节。容量只使用文件本来就有的
    扇区 padding；绝不增长 section，因为实机已证实增长会破坏后续装载状态。
    """
    ef = EFile(path)
    result = {}
    for section, (start, end) in enumerate(ef.sections):
        raw = ef.data[start:end]
        if len(raw) < 8:
            raise ValueError(f"E0 section {section}: 小于8字节")
        logical_end = pointer_to_file(4, struct.unpack_from("<I", raw, 4)[0])
        if not 8 <= logical_end <= len(raw):
            raise ValueError(
                f"E0 section {section}: 逻辑终点 {logical_end:#x} 越界")
        # 逻辑终点之后偶尔仍有非零尾部数据，不能全部视为 padding。只使用
        # 最大的4字节对齐连续零区，保证 handler 的 lw 和页包头均对齐。
        runs = []
        pos = logical_end
        while pos < len(raw):
            if raw[pos] != 0:
                pos += 1
                continue
            start = pos
            while pos < len(raw) and raw[pos] == 0:
                pos += 1
            aligned = (start + 3) & ~3
            usable = (pos - aligned) & ~3
            if usable > 0:
                runs.append((usable, aligned))
        padding, page_offset = max(runs, default=(0, logical_end))
        result[section] = {
            "physical_bytes": len(raw),
            "logical_end": logical_end,
            "physical_tail_bytes": len(raw) - logical_end,
            "page_offset": page_offset,
            "padding_bytes": padding,
            # padding不足4字节时，只允许该段不换页（所有字进核心）。
            "glyph_capacity": max(0, (padding - 4) // GLYPH_BYTES),
        }
    return result


def select_core_constrained(frequency: Counter,
                            sections: dict[int, set[str]],
                            capacities: dict[int, dict[str, int]],
                            core_size: int) -> tuple[list[str], dict]:
    """选择固定大小核心字，使每段非核心字数不超过其 padding 容量。

    等价地从全集里挑出 ``len(universe)-core_size`` 个换页字。候选优先选择
    低频、且较少出现在紧张 section 的字符；每次选择都检查所有相关 section
    的剩余容量。算法确定性，输入不变时槽位布局不变。
    """
    if not 2 <= core_size < PHYSICAL_SLOTS:
        raise ValueError(f"CORE 必须在 2..{PHYSICAL_SLOTS - 1}")
    pinned_order = list(dict.fromkeys(PINNED))
    pinned = set(pinned_order)
    universe = set(frequency) | pinned
    if len(universe) < core_size:
        raise ValueError(f"语料和固定字符只有 {len(universe)} 字，无法填满 CORE")
    if len(pinned) > core_size:
        raise ValueError(f"固定字符 {len(pinned)} 超过 CORE={core_size}")

    char_sections: dict[str, set[int]] = defaultdict(set)
    section_caps = {}
    for section, chars in sections.items():
        cap = min(capacities[section]["glyph_capacity"],
                  PHYSICAL_SLOTS - core_size)
        section_caps[section] = cap
        for ch in chars:
            char_sections[ch].add(section)

    def pressure(ch: str) -> Fraction:
        return sum((Fraction(1, section_caps[s] + 1)
                    for s in char_sections[ch]), Fraction())

    required_noncore = len(universe) - core_size
    rare_count: dict[int, int] = defaultdict(int)
    noncore = set()
    candidates = sorted(
        universe - pinned,
        key=lambda ch: (pressure(ch), frequency[ch], ord(ch)))
    for ch in candidates:
        touched = char_sections[ch]
        if all(rare_count[s] < section_caps[s] for s in touched):
            noncore.add(ch)
            for section in touched:
                rare_count[section] += 1
            if len(noncore) == required_noncore:
                break
    if len(noncore) != required_noncore:
        tight = sorted(
            ((s, rare_count[s], section_caps[s]) for s in sections),
            key=lambda row: (row[2] - row[1], row[0]))[:12]
        raise ValueError(
            f"CORE={core_size} 无法满足全部 padding：只能分配 "
            f"{len(noncore)}/{required_noncore} 个换页字；紧张段={tight}")

    core_set = universe - noncore
    core = []
    seen = set()
    for ch in pinned_order + ranked_chars(frequency) + sorted(core_set):
        if ch in core_set and ch not in seen:
            core.append(ch)
            seen.add(ch)
    if len(core) != core_size:
        raise AssertionError(f"约束核心字数 {len(core)} != {core_size}")

    report = {
        "strategy": "section_padding_constrained",
        "layout_source": None,
        "universe_chars": len(universe),
        "noncore_chars": len(noncore),
        "constrained_sections": len(sections),
        "max_page_chars": max(rare_count.values(), default=0),
        "full_sections": sum(
            rare_count[s] == section_caps[s] for s in sections),
    }
    return core, report


def render_base_font(core: list[str], destination: Path) -> bytes:
    font = ImageFont.truetype(str(TTF), 14)
    data = bytearray(PHYSICAL_SLOTS * GLYPH_BYTES)
    for slot, ch in enumerate(core):
        data[slot * GLYPH_BYTES:(slot + 1) * GLYPH_BYTES] = render_glyph(ch, font)
    destination.write_bytes(data)
    return bytes(data)


def build(output: Path, core_size: int, translations: Path,
          layout_e0: Path | None = None) -> dict:
    frequency, sections, translated = load_corpus(translations)
    capacities = load_section_capacities(layout_e0) if layout_e0 else None
    if capacities:
        core, allocation = select_core_constrained(
            frequency, sections, capacities, core_size)
        allocation["layout_source"] = str(layout_e0)
    else:
        core = select_core(frequency, core_size)
        allocation = {"strategy": "frequency", "layout_source": None}
    core_set = set(core)
    rank = {ch: index for index, ch in enumerate(ranked_chars(frequency))}
    swap_capacity = PHYSICAL_SLOTS - core_size

    output.mkdir(parents=True, exist_ok=True)
    pages_dir = output / "pages/E0"
    pages_dir.mkdir(parents=True, exist_ok=True)

    base_font_path = output / "FONT.dynamic.base.BIN"
    base_font = render_base_font(core, base_font_path)
    codetable = {
        str(slot): (core[slot] if slot < core_size else "")
        for slot in range(PHYSICAL_SLOTS)
    }
    (output / "codetable_dynamic_core.json").write_text(
        json.dumps(codetable, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    font = ImageFont.truetype(str(TTF), 14)
    pages = {}
    maximum = (None, -1)
    for section in sorted(sections):
        rare = sorted(
            sections[section] - core_set,
            key=lambda ch: (rank.get(ch, 1 << 30), ord(ch)))
        if len(rare) > swap_capacity:
            raise ValueError(
                f"E0 section {section}: 换页字 {len(rare)} > 容量 {swap_capacity}")
        pack = b"".join(render_glyph(ch, font) for ch in rare)
        pack_name = f"section_{section:03d}.bin"
        (pages_dir / pack_name).write_bytes(pack)
        pages[str(section)] = {
            "count": len(rare),
            "bytes": len(pack),
            "chars": "".join(rare),
            "first_slot": core_size,
            "pack": f"pages/E0/{pack_name}",
            "sha256": hashlib.sha256(pack).hexdigest(),
        }
        if capacities:
            capacity = capacities[section]
            stored_bytes = 0 if not rare else 4 + len(pack)
            pages[str(section)].update({
                "page_offset": capacity["page_offset"],
                "padding_bytes": capacity["padding_bytes"],
                "padding_capacity_glyphs": capacity["glyph_capacity"],
                "stored_bytes": stored_bytes,
                "fits_padding": stored_bytes <= capacity["padding_bytes"],
            })
        if len(rare) > maximum[1]:
            maximum = (section, len(rare))

    total_chars = sum(frequency.values())
    core_hits = sum(count for ch, count in frequency.items() if ch in core_set)
    manifest = {
        "schema": 1,
        "physical_slots": PHYSICAL_SLOTS,
        "glyph_bytes": GLYPH_BYTES,
        "core_size": core_size,
        "swap_capacity": swap_capacity,
        "swap_ram": f"0x{0x801E0000 + core_size * GLYPH_BYTES:08X}",
        "translated_entries": translated,
        "global_unique_chars": len(frequency),
        "weighted_core_coverage": core_hits / total_chars if total_chars else 1.0,
        "core_chars": "".join(core),
        "core_font": base_font_path.name,
        "core_font_sha256": hashlib.sha256(base_font).hexdigest(),
        "ttf": str(TTF),
        "allocation": allocation,
        "e0_max_page": {"section": maximum[0], "count": maximum[1]},
        "pages": {"E0": pages},
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    # 数据不变量：每个 section 的所有译文字都必须在 core 或它的 page。
    for section, chars in sections.items():
        page_chars = set(pages[str(section)]["chars"])
        missing = chars - core_set - page_chars
        if missing:
            raise AssertionError(f"E0 section {section} 缺字: {missing}")
        if capacities and not pages[str(section)]["fits_padding"]:
            raise AssertionError(f"E0 section {section} 页包放不进原 padding")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--core-size", type=int, default=DEFAULT_CORE)
    parser.add_argument("--translations", type=Path, default=TRANSLATIONS)
    parser.add_argument(
        "--layout-e0", type=Path,
        help="按这个 E0 的 section padding 约束核心字；不指定则仅按词频")
    parser.add_argument(
        "--output", type=Path, default=HERE / "out/dynamic_font")
    args = parser.parse_args()
    manifest = build(args.output.resolve(), args.core_size,
                     args.translations.resolve(),
                     args.layout_e0.resolve() if args.layout_e0 else None)
    maximum = manifest["e0_max_page"]
    print(
        f"✅ 动态字库 CORE={manifest['core_size']} / "
        f"SWAP={manifest['swap_capacity']}")
    print(
        f"   当前译文 {manifest['global_unique_chars']} 字，"
        f"核心加权覆盖率 {manifest['weighted_core_coverage']:.2%}")
    print(
        f"   E0 最大换页: section {maximum['section']} / "
        f"{maximum['count']} 字 ({maximum['count'] * GLYPH_BYTES} bytes)")
    print(f"   核心分配: {manifest['allocation']['strategy']}")
    print(f"   输出: {args.output.resolve()}")


if __name__ == "__main__":
    main()
