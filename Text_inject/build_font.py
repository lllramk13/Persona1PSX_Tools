#!/usr/bin/env python3
"""B 步：把 E0 的中文字渲成 P1 字库。

FONT.BIN 字形格式（实测，见 README §1.4）：
  · 每个字 = 16×16 像素，1bpp（每像素 1 bit），共 32 字节；
  · 一行 16 像素 = 2 字节，最高位(bit7)是最左边的像素，1=有笔画/0=空；
  · 第 0 行在最前，共 16 行 → byte[row*2], byte[row*2+1]。

例：あ 的第 4 行点阵 `···█████········` 对应两字节 0b00011111, 0b00000000。
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from sync_json import read_json, write_json
from collections import defaultdict
import re
from opencc import OpenCC
import unicodedata

HERE = Path(__file__).resolve().parent
TTF = HERE / "wqy-zenhei.ttc"


def render_glyph(ch: str, font: ImageFont.FreeTypeFont) -> bytes:
    """把一个字渲成 P1 的 16×16 1bpp / 32 字节点阵。
    """
    img = Image.new("L", (16, 16), 0)
    draw = ImageDraw.Draw(img)
    l, t, r, b = draw.textbbox((0, 0), ch, font=font)
    w = r - l
    h = b - t
    x = ((16 - w) // 2 -l)
    y = ((16 - h) // 2 -t)
    draw.text((x, y), ch, fill=255, font=font)
    g = bytearray(32)
    for y in range(16):
        for x in range(16):
            if img.getpixel((x, y)) > 96:
                g[y*2 + x//8] |= 1 << (7 - (x%8))
    return bytes(g)


def count_word(section, path):
    """计算path文件里的section的每个汉字出现的次数
    """
    raw_data = read_json(path)
    counter = defaultdict(int)
    section = section.lower()
    for id_, entry in raw_data.items():
        if (
            not id_.lower().startswith(section)
            or not entry.get("zh")
            ):
            continue
        zh = re.sub(r"⟦\d+⟧", "", entry["zh"])
        for ch in zh:
            counter[ch] += 1
    return counter


def create_codetable_zh(counter, og):
    og_data = read_json(og)
    char_to_id = {v: k for k, v in og_data.items()}

    target_chars = set(counter.keys())
    protected_chars = set()

    for ch in target_chars:
        protected_chars.add(ch)
        protected_chars.add(unicode_normalize(to_traditional(ch)))

    free_slots = []

    for slot, old_ch in og_data.items():
        if old_ch not in protected_chars:
            free_slots.append(slot)

    for ch, count in sorted(counter.items(), key=lambda x: x[1], reverse=True):
        t = unicode_normalize(to_traditional(ch))

        if ch in char_to_id:
            continue

        elif t in char_to_id:
            slot = char_to_id[t]

            og_data[slot] = ch

            del char_to_id[t]
            char_to_id[ch] = slot

        else:
            if not free_slots:
                raise ValueError(f"没有空槽，无法写入字符：{ch}")

            slot = free_slots.pop(0)
            old_ch = og_data.get(slot)

            if old_ch in char_to_id:
                del char_to_id[old_ch]

            og_data[slot] = ch
            char_to_id[ch] = slot

    write_json(HERE.parent / "Codetable" / "codetable_zh.json", og_data)


def render_font(out_path=None):
    og = read_json(HERE.parent / "Codetable" / "codetable_og.json")
    zh = read_json(HERE.parent / "Codetable" / "codetable_zh.json")
    buf = bytearray((HERE.parent / "extrac" / "FONT.BIN").read_bytes())
    if len(buf) != 65536:
        raise ValueError(f"原 FONT.BIN 应为 65536 字节，实际 {len(buf)}")

    font = ImageFont.truetype(str(TTF), 14)
    changed = 0
    for slot, ch in zh.items():
        if og.get(slot) == ch:
            continue
        i = int(slot) * 32
        buf[i:i + 32] = render_glyph(ch, font)
        changed += 1

    print(f"重渲了 {changed} 个槽，字库仍 {len(buf)} 字节")
    if out_path:
        Path(out_path).write_bytes(buf)
    return bytes(buf)



def _show(glyph: bytes) -> None:
    """把 32 字节点阵画成字符画"""
    for row in range(16):
        b0, b1 = glyph[row * 2], glyph[row * 2 + 1]
        print("  " + f"{b0:08b}{b1:08b}".replace("0", "·").replace("1", "█"))


def _selftest() -> None:
    font = ImageFont.truetype(str(TTF), 14)
    for ch in "你好":
        g = render_glyph(ch, font)
        assert len(g) == 32, f"{ch}: 应是 32 字节，得到 {len(g)}"
        print(f"\n『{ch}』:")
        _show(g)


def to_simplified(s):
    return cc_t2s.convert(s)


def to_traditional(s):
    return cc_s2t.convert(s)


def unicode_normalize(s:str) -> str:
    return unicodedata.normalize("NFKC", s)


if __name__ == "__main__":
    counter = count_word("ADV/E0", HERE.parent / "Text" / "translations.json")
    for ch, count in sorted(counter.items(), key=lambda x:x[1], reverse=True)[:10]:
        print(ch, ":", count)
    print(len(counter))

    cc_t2s = OpenCC("t2s")
    cc_s2t = OpenCC("s2t")
    create_codetable_zh(counter, HERE.parent / "Codetable" / "codetable_og.json")

    out = HERE / "out"
    out.mkdir(exist_ok=True)
    render_font(out / "FONT.patched.BIN")