#!/usr/bin/env python3
"""快速插入测试:按字频重排字库后，把已翻的 E0 中文回插进游戏数据。

流程:
  1. 从 translations.json 收集 ADV/E0.BIN 有 zh 的条目;codes/span 从 all_text.json join。
  2. masked zh → markup(⟦n⟧ 还原成 codes;字面 \\n → 真换行;空格 → {0})。
  3. 统计 E0 译文的字频：高频字放进可单字节编码的槽，其余放高位槽。
     这是实验码表，会覆盖原日文字槽；未翻译文本可能乱码，不可作为最终全游戏码表。
  4. 渲染 16×16 1bpp 字形并写入新的 FONT.BIN。
  5. 按容量预筛(编码后 ≤ 原 span),用 encode.apply_patch 原位回插 → E0.patched.BIN。
  6. dump 回来 + 渲染 PNG,证明中文进去了。

产物在 Text_inject/out/。这是一次性验证脚本,不是正式管道。
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "Text"))
import encode, dump, decode as D              # noqa: E402
from build_test_disc import build_test_disc   # noqa: E402

OUT = HERE / "out"; OUT.mkdir(exist_ok=True)
FONT_SRC = ROOT / "extrac" / "FONT.BIN"
E0_SRC = ROOT / "extrac" / "ADV" / "E0.BIN"
TTF_PATH = HERE / "wqy-zenhei.ttc"
MARK = re.compile(r"⟦(\d+)⟧")
TAG = re.compile(r"<[^>]*>")
SLOT = re.compile(r"\{(\d+)\}")
LITERAL_LT = "\uE000"
LITERAL_GT = "\uE001"


def restore(masked, codes):
    """⟦n⟧ → codes[n];字面 \\n → 真换行;其余(<tag>/{slot})原样。"""
    # translations.json 的控制码只能来自 ⟦n⟧；译文自己写的尖括号一定是
    # 画面文字。先藏起来，避免还原控制码后与 <wait>/<clear> 等标签混淆。
    masked = masked.replace("<", LITERAL_LT).replace(">", LITERAL_GT)
    restored = encode.restore_masked(masked, codes)
    return restored.replace(r"\n", "\n").replace(" ", "{0}")


def literal_chars(markup):
    """markup 里的字面字(去掉 <tag>/{slot}/换行)。"""
    s = TAG.sub("", markup)
    s = SLOT.sub("", s)
    s = s.replace(LITERAL_LT, "<").replace(LITERAL_GT, ">")
    return [c for c in s if c != "\n"]


def materialize_literal_angles(markup, codetable):
    """把译文的字面 ``<``/``>`` 变成 ``{slot}``，绕开控制标签语法。"""
    reverse = {}
    for slot, text in codetable.items():
        if text in ("<", ">"):
            old = reverse.get(text)
            if old is None or (len(encode.encode_slot(slot)), slot) < (
                    len(encode.encode_slot(old)), old):
                reverse[text] = slot
    if "<" not in reverse or ">" not in reverse:
        raise encode.EncodeError("实验码表没有给字面尖括号分配槽位")
    return (markup.replace(LITERAL_LT, f"{{{reverse['<']}}}")
                  .replace(LITERAL_GT, f"{{{reverse['>']}}}"))


def build_frequency_codetable(orig_ct, items):
    """为本次 E0 实验生成码表。

    slot 0 和译文显式引用的 ``{slot}`` 必须钉死。其余译文字按出现频率排序，
    高频字优先进入所有可单字节编码的空槽；这样 encoder 会为它们选 1 字节编码。

    这是“整张实验字库”，不是增量补字：为了保证每个译文字都有确定槽位，会覆盖
    一部分原日文字形。因此只适合验证字频编码和已翻译 E0，不保证未翻译内容正常。
    """
    frequency = Counter()
    pinned_slots = {0}
    for _, markup in items:
        frequency.update(literal_chars(markup))
        pinned_slots.update(int(m) for m in SLOT.findall(markup))

    if any(not 0 <= slot < 2048 for slot in pinned_slots):
        bad = sorted(slot for slot in pinned_slots if not 0 <= slot < 2048)
        raise encode.EncodeError(f"译文显式引用越界字槽: {bad}")

    # 显式 {slot} 的原字形不能被重排；如果它恰好也是字面输入，直接复用。
    pinned_chars = {
        text for slot, text in orig_ct.items()
        if slot in pinned_slots and isinstance(text, str) and len(text) == 1
    }
    ranked = [ch for ch, _ in sorted(
        frequency.items(), key=lambda item: (-item[1], ord(item[0]))
    ) if ch not in pinned_chars]

    single_slots = [
        slot for slot in range(1, 2048)
        if slot not in pinned_slots and len(encode.encode_slot(slot)) == 1
    ]
    high_slots = [
        slot for slot in range(1, 2048)
        if slot not in pinned_slots and len(encode.encode_slot(slot)) == 2
    ]
    # 每个译文字先分一个稳定的双字节槽；最高频字再复制一份到单字节槽。
    # 这样每处高频字都能在 1/2 字节之间切换，可把整条文本精确撑回原 span，
    # 避免 encode.apply_patch 用大量 00 填充导致游戏继续读取空白字形。
    if len(ranked) > len(high_slots):
        raise encode.EncodeError(
            f"译文字形 {len(ranked)} 个，但高位槽只有 {len(high_slots)} 个")

    ct = dict(orig_ct)
    high_assignments = {}
    for ch, slot in zip(ranked, high_slots):
        ct[slot] = ch
        high_assignments[ch] = slot

    single_assignments = {}
    for ch, slot in zip(ranked, single_slots):
        ct[slot] = ch
        single_assignments[ch] = slot

    return (ct, frequency, high_assignments, single_assignments,
            pinned_slots)


def make_exact_length(markup, codetable, ctrl, capacity, high_assignments):
    """在高频字的 1/2 字节重复槽之间选择，使编码恰好等于原 span。"""
    tokens = encode.markup_to_tokens(markup, codetable, ctrl)
    encoded_len = len(encode.encode_tokens(tokens))
    if encoded_len > capacity:
        raise encode.EncodeError(f"最短编码仍超长: {encoded_len}>{capacity}")

    need = capacity - encoded_len
    work = list(tokens)
    for index, token in enumerate(work):
        if need == 0:
            break
        if token[0] != "char" or len(encode.encode_slot(token[1])) != 1:
            continue
        ch = codetable.get(token[1], "")
        high_slot = high_assignments.get(ch)
        if high_slot is None:
            continue
        work[index] = ("char", high_slot)
        need -= 1

    if need:
        raise encode.EncodeError(
            f"译文即使用全双字节仍比原 span 短 {need} 字节，无法无填充等长")
    return encode.tokens_to_markup(work, codetable, ctrl)


def render_glyph(ch, ttf):
    """渲染一个字成 P1 的 16×16 1bpp / 32 字节(2字节一行,MSB 在左)。"""
    img = Image.new("L", (16, 16), 0)
    draw = ImageDraw.Draw(img)
    left, top, right, bottom = draw.textbbox((0, 0), ch, font=ttf)
    x = (16 - (right - left)) // 2 - left
    y = (16 - (bottom - top)) // 2 - top
    draw.text((x, y), ch, fill=255, font=ttf)
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

    # 2) E0 实验码表：真正按出现频率，而不是按 Unicode 顺序占用单字节槽。
    ct, frequency, high_assign, single_assign, pinned = \
        build_frequency_codetable(orig_ct, items)
    items = [(local_id, materialize_literal_angles(markup, ct))
             for local_id, markup in items]
    single_hits = sum(frequency[ch] for ch in single_assign)
    print(
        f"用到唯一字 {len(frequency)}; 高位字槽 {len(high_assign)}; "
        f"单字节高频字 {len(single_assign)} 个，覆盖 {single_hits} 次出现")
    print("⚠ 实验码表覆盖了原日文字槽：未翻译文本可能乱码，只用于验证 E0 字频方案")

    ttf = ImageFont.truetype(TTF_PATH, 12)
    font = bytearray(FONT_SRC.read_bytes())
    rendered_slots = {}
    for ch, slot in high_assign.items():
        rendered_slots[slot] = ch
    for ch, slot in single_assign.items():
        rendered_slots[slot] = ch
    for slot, ch in rendered_slots.items():
        font[slot * 32:(slot + 1) * 32] = render_glyph(ch, ttf)
    (OUT / "FONT.patched.BIN").write_bytes(font)
    ct_path = OUT / "codetable_patched.json"
    ct_path.write_text(json.dumps({str(k): v for k, v in ct.items()}, ensure_ascii=False, indent=1), "utf-8")
    allocation = {
        "warning": "E0 experimental frequency layout; untranslated Japanese may be corrupted",
        "unique_chars": len(frequency),
        "single_byte_occurrences": single_hits,
        "pinned_slots": sorted(pinned),
        "assignments": [
            {"char": ch, "slot": slot, "bytes": len(encode.encode_slot(slot)),
             "count": frequency[ch]}
            for slot, ch in sorted(rendered_slots.items())
        ],
    }
    (OUT / "font_allocation.json").write_text(
        json.dumps(allocation, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"渲染字槽 {len(rendered_slots)} 个 → FONT.patched.BIN; 分配报告 → font_allocation.json")

    # 4) 容量预筛(编码 ≤ 原 span),写 patch,回插
    cfg = dump.load_format(); ctrl = cfg["ctrl"]["efile"]
    fit, toolong = [], []
    encoded_total = capacity_total = 0
    for local_id, markup in items:
        e = allt[f"ADV/E0.BIN#{local_id}"]; cap = e["span"][1] - e["span"][0]
        try:
            markup = make_exact_length(
                markup, ct, ctrl, cap, high_assign)
            toks = encode.markup_to_tokens(markup, ct, ctrl)
            enc = encode.encode_tokens(toks)
        except encode.EncodeError as ex:
            toolong.append((local_id, f"编码失败:{ex}")); continue
        encoded_total += len(enc)
        capacity_total += cap
        (fit if len(enc) <= cap else toolong).append(
            (local_id, markup) if len(enc) <= cap else (local_id, f"{len(enc)}>{cap}"))
    print(f"能原位塞下: {len(fit)};  超长/失败(跳过): {len(toolong)}")
    print(
        f"编码总量 {encoded_total} / 原 span {capacity_total} 字节 "
        f"(余量 {capacity_total - encoded_total:+d})")
    if toolong:
        print("未回插:")
        for local_id, reason in toolong[:20]:
            print(f"  {local_id}: {reason}")

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

    # FONT 和 E0 必须来自同一次码表构建；最后自动从干净原盘重建测试镜像。
    print("\n写回测试光盘镜像:")
    build_test_disc()


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
