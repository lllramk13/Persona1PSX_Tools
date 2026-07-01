"""
P1 文本 dump 驱动
================
把三块拼起来：读 format.json 配置 → 按类型选容器 reader 定位每句 →
decode 解码 → 输出 JSON。

用法：
    python dump.py <文件路径>                  # JSON 打到 stdout
    python dump.py <文件路径> -o out.json      # 写文件
    python dump.py <文件路径> --render img.png # 顺带渲染字模图(不依赖码表,肉眼验证)

JSON 形如：
    { "file": "...", "format": "talk", "count": 983,
      "lines": [ {"id": "0:0", "section": 0, "span": [8192, 8239], "jp": "..."}, ... ] }
    span = 原文件里这句的字节区间 [start, end)，留给以后回插对位用。
    码表缺的字会显示成 {slot}（codetable_og.json 补齐即自动变真字）。
"""
import os
import json
import argparse

import decode as D
import containers as C

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, "..", "extrac", "FONT.BIN")
CODETABLE = os.path.join(HERE, "..", "Codetable", "codetable_og.json")


def load_format():
    """读 format.json，并把控制码表的 '0xFF' 字符串键转成 int。"""
    with open(os.path.join(HERE, "format.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["ctrl"] = {
        fmt: {int(k, 16): tuple(v) for k, v in table.items() if not k.startswith("_")}
        for fmt, table in cfg["ctrl"].items() if not fmt.startswith("_")
    }
    return cfg


def load_codetable():
    """读码表 slot→字。剔除字值里残留的换行(建表时的小瑕疵)。"""
    with open(CODETABLE, encoding="utf-8") as f:
        raw = json.load(f)
    return {int(k): v.replace("\n", "") for k, v in raw.items()}


def match_format(path, files_map):
    """按 format.json 的 files 映射(路径片段→格式)判类型。"""
    p = path.replace("\\", "/")
    for frag, fmt in files_map.items():
        if not frag.startswith("_") and frag in p:
            return fmt
    return None


def decode_file(path, cfg):
    """返回 (格式名, [(section, start, end, tokens), ...])。"""
    with open(path, "rb") as f:
        data = f.read()
    fmt = match_format(path, cfg.get("files", {})) or C.detect_type(data)
    reader = C.READERS.get(fmt)
    if reader is None:
        raise ValueError(f"未知文件类型: {path}")
    ctrl = cfg["ctrl"].get(fmt, {})
    decoded = [(section, s, e, D.decode(data, s, e, ctrl))
               for (section, s, e) in reader(data)]
    return fmt, decoded


def to_json(path, fmt, decoded, cfg, codetable):
    """组装成可序列化的 dict。"""
    ctrl = cfg["ctrl"].get(fmt, {})
    local = {}                                    # 每段内部计数，拼稳定 id
    lines = []
    for section, s, e, tokens in decoded:
        idx = local.get(section, 0)
        local[section] = idx + 1
        lines.append({
            "id": f"{section}:{idx}",
            "section": section,
            "span": [s, e],
            "jp": D.tokens_to_text(tokens, codetable, ctrl),
        })
    return {
        "file": os.path.basename(path),
        "format": fmt,
        "count": len(lines),
        "lines": lines,
    }


def render_png(decoded, out_path, max_lines=40):
    """从 FONT.BIN 把每句画成一行字模叠成一张图，不依赖码表，肉眼验证解码用。"""
    from PIL import Image, ImageDraw
    font = open(FONT, "rb").read()
    W = H = 16
    BPG = 32

    def glyph(slot, scale=2):
        g = font[slot * BPG:(slot + 1) * BPG]
        im = Image.new("L", (W, H), 0)
        for y in range(H):
            row = g[y * 2:y * 2 + 2]
            for x in range(W):
                if (row[x // 8] >> (7 - (x % 8))) & 1:
                    im.putpixel((x, y), 255)
        return im.resize((W * scale, H * scale), Image.NEAREST)

    sub = decoded[:max_lines]
    cell = W * 2
    width = 64 * (cell + 1) + 10
    canvas = Image.new("RGB", (width, len(sub) * (cell + 6) + 10), (18, 18, 18))
    d = ImageDraw.Draw(canvas)
    for r, (section, s, e, tokens) in enumerate(sub):
        y = 8 + r * (cell + 6)
        x = 6
        for tok in tokens:
            if x > width - cell:
                break
            if tok[0] == "char":
                canvas.paste(glyph(tok[1]).convert("RGB"), (x, y))
            else:
                d.rectangle([x, y, x + cell, y + cell], outline=(80, 130, 80))
                d.text((x + 1, y + 5), f"{tok[1]:02x}", fill=(120, 200, 120))
            x += cell + 1
    canvas.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="要 dump 的文件，如 ../extrac/TALK/SYOUJO.BIN")
    ap.add_argument("-o", "--out", help="JSON 输出到文件，不给则打到 stdout")
    ap.add_argument("--render", help="顺带渲染字模图(png)的路径")
    args = ap.parse_args()

    cfg = load_format()
    try:
        fmt, decoded = decode_file(args.path, cfg)
    except (ValueError, NotImplementedError) as e:
        raise SystemExit(f"[跳过] {os.path.basename(args.path)}: {e}")

    if args.render:
        render_png(decoded, args.render)

    result = to_json(args.path, fmt, decoded, cfg, load_codetable())
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"已写 {args.out}：{result['count']} 句（{fmt}）")
    else:
        print(text)


if __name__ == "__main__":
    main()
