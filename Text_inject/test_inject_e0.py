#!/usr/bin/env python3
"""快速插入测试:把已翻的 E0 中文回插进游戏数据(先不管 2048 槽上限)。

流程:
  1. 从 translations.json 收集 ADV/E0.BIN 有 zh 的条目;codes/span 从 all_text.json join。
  2. masked zh → markup(⟦n⟧ 还原成 codes;字面 \\n → 真换行;空格 → {0})。
  3. 收集所有中文字:码表已有的复用其槽,没有的分配空闲槽 + 渲染 16×16 1bpp 字形写进 FONT.BIN。
  4. 按容量预筛(编码后 ≤ 原 span),用 encode.apply_patch 原位回插 → E0.patched.BIN。
  5. dump 回来 + 渲染 PNG,证明中文进去了。

产物在 Text_inject/out/。这是一次性验证脚本,不是正式管道。
"""
import json
import re
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "Text"))
import encode, dump, decode as D              # noqa: E402

OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
FONT_SRC = ROOT / "extrac" / "FONT.BIN"
E0_SRC = ROOT / "extrac" / "ADV" / "E0.BIN"
TTF_PATH = "/mnt/c/Windows/Fonts/simhei.ttf"
MARK = re.compile(r"⟦(\d+)⟧")
TAG = re.compile(r"<[^>]*>")
SLOT = re.compile(r"\{(\d+)\}")


def restore(masked, codes):
    """⟦n⟧ → codes[n];字面 \\n → 真换行;其余(<tag>/{slot})原样。"""
    def sub(m):
        c = codes[int(m.group(1))]
        return "\n" if c == "\\n" else c
    return MARK.sub(sub, masked).replace(" ", "{0}")   # 空格 → 空白槽 {0}


def literal_chars(markup):
    """markup 里的字面字(去掉 <tag>/{slot}/换行)。"""
    s = TAG.sub("", markup)
    s = SLOT.sub("", s)
    return [c for c in s if c != "\n"]


def render_glyph(ch, ttf):
    """渲染一个字成 P1 的 16×16 1bpp / 32 字节(2字节一行,MSB 在左)。"""
    img = Image.new("L", (16, 16), 0)
    ImageDraw.Draw(img).text((0, -1), ch, fill=255, font=ttf)
    g = bytearray(32)
    for y in range(16):
        for x in range(16):
            if img.getpixel((x, y)) > 96:
                g[y * 2 + x // 8] |= 1 << (7 - (x % 8))
    return bytes(g)


def main():
    tr = json.loads((ROOT / "Text" / "translations.json").read_text("utf-8"))
    allt = {e["id"]: e for e in
            json.loads((ROOT / "Text" / "all_text.json").read_text("utf-8"))["entries"]}
    orig_ct = {int(k): v for k, v in
               json.loads((ROOT / "Codetable" / "codetable_og.json").read_text("utf-8")).items()}

    # 1) E0 有 zh 的条目 → markup
    items = []                                          # (local_id, markup)
    for fid, v in tr.items():
        if not fid.startswith("ADV/E0.BIN#") or not v.get("zh"):
            continue
        e = allt.get(fid)
        if not e:
            continue
        markup = restore(v["zh"], e["codes"])
        items.append((fid.split("#", 1)[1], markup))
    print(f"E0 待回插条目: {len(items)}")

    # 2) 收集字面字,分类:码表已有(复用) / 缺(新造)
    rev = {c: s for s, c in ct.items() if c and len(c) == 1}
    used_chars = set()
    for _, markup in items:
        used_chars.update(literal_chars(markup))
    used_chars.discard(" ")
    reused = {c for c in used_chars if c in rev}
    missing = sorted(c for c in used_chars if c not in rev and c != "\n")
    print(f"用到唯一字 {len(used_chars)};  码表已有(复用) {len(reused)};  缺(新造) {len(missing)}")

    # 3) 分配空闲槽给缺字:保护「复用字的槽」+「codes 里 {n} 引用的槽」;
    #    优先给单字节可编码的槽(省字节,更容易塞进原 span)。
    protect = {rev[c] for c in reused} | {0}
    for _, markup in items:
        protect.update(int(m) for m in SLOT.findall(markup))
    single = [s for s in range(1, 0x80) if s not in protect]          # 真单字节(1字节),优先
    rest = [s for s in range(0x80, 2048) if s not in protect]         # 逃逸(2字节)
    free = single + rest
    if len(missing) > len(free):
        print(f"⚠ 缺字 {len(missing)} > 空闲槽 {len(free)},截断(测试用)")
        missing = missing[:len(free)]

    ttf = ImageFont.truetype(TTF_PATH, 16)
    font = bytearray(FONT_SRC.read_bytes())
    assign = {}
    for ch, slot in zip(missing, free):
        assign[ch] = slot
        ct[slot] = ch
        font[slot * 32:(slot + 1) * 32] = render_glyph(ch, ttf)
    # 复用字(码表已有的:假名/标点/共用汉字)也用同一套字体重绘,避免和新字混排违和
    # ——纯改字形,不动槽位/码表,不影响编码。simhei 画不出的字保留原版位图。
    reused_redrawn = 0
    for slot, c in list(ct.items()):
        if isinstance(c, str) and len(c) == 1 and c in reused:
            g = render_glyph(c, ttf)
            if any(g):
                font[slot * 32:(slot + 1) * 32] = g
                reused_redrawn += 1
    print(f"复用字重绘 {reused_redrawn} 个(统一字体)")
    (OUT / "FONT.patched.BIN").write_bytes(font)
    ct_path = OUT / "codetable_patched.json"
    ct_path.write_text(json.dumps({str(k): v for k, v in ct.items()}, ensure_ascii=False, indent=1), "utf-8")
    print(f"新造字形 {len(assign)} 个 → FONT.patched.BIN;码表已更新")

    # 4) 容量预筛(编码 ≤ 原 span),写 patch,回插
    cfg = dump.load_format(); ctrl = cfg["ctrl"]["efile"]
    fit, toolong = [], []
    for local_id, markup in items:
        try:
            toks = encode.markup_to_tokens(markup, ct, ctrl)
            enc = encode.encode_tokens(toks)
        except encode.EncodeError as ex:
            toolong.append((local_id, f"编码失败:{ex}")); continue
        e = allt[f"ADV/E0.BIN#{local_id}"]; cap = e["span"][1] - e["span"][0]
        (fit if len(enc) <= cap else toolong).append(
            (local_id, markup) if len(enc) <= cap else (local_id, f"{len(enc)}>{cap}"))
    print(f"能原位塞下: {len(fit)};  超长/失败(跳过): {len(toolong)}")

    patch = OUT / "e0_patch.json"
    patch.write_text(json.dumps({"lines": [{"id": i, "zh": z} for i, z in fit]}, ensure_ascii=False), "utf-8")
    n, fmt = encode.apply_patch(E0_SRC, patch, OUT / "E0.patched.BIN", codetable_path=str(ct_path))
    print(f"✅ 回插 {n} 条 → out/E0.patched.BIN(容器复扫通过)")

    # 5) dump 一条验证 + 渲染 PNG
    if fit:
        lid = fit[0][0]
        e = allt[f"ADV/E0.BIN#{lid}"]; s, en = e["span"]
        data = (OUT / "E0.patched.BIN").read_bytes()
        txt = D.tokens_to_text(D.decode(data, s, en, ctrl), ct, ctrl)
        print(f"\n验证 {lid} 从 patched.BIN 解出:\n{txt[:120]}")
        render_line(txt, font, OUT / "preview.png")
        print(f"渲染预览 → out/preview.png")


def render_line(text, font, path):
    """用 patched 字库把一行画出来(遇控制码/换行折行)。"""
    rev = {}
    ct = {int(k): v for k, v in json.loads((Path(__file__).resolve().parent / "out" / "codetable_patched.json").read_text("utf-8")).items()}
    for s, c in ct.items():
        rev.setdefault(c, s)
    chars = [c for c in text if c not in "\n"][:200]
    cols = 24
    rows = (len(chars) + cols - 1) // cols
    img = Image.new("L", (cols * 16, max(1, rows) * 16), 0)
    for i, c in enumerate(chars):
        slot = rev.get(c)
        if slot is None:
            continue
        g = font[slot * 32:(slot + 1) * 32]
        ox, oy = (i % cols) * 16, (i // cols) * 16
        for y in range(16):
            for x in range(16):
                if (g[y * 2 + x // 8] >> (7 - (x % 8))) & 1:
                    img.putpixel((ox + x, oy + y), 255)
    img.save(path)


if __name__ == "__main__":
    main()
