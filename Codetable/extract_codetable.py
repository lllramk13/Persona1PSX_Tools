import json
from PIL import Image
import math


def read_bin(path):
    with open(path, 'rb') as f:
        data = f.read()
    return data


def transcribe_code_table(data,
                          code_w: int, code_h: int,
                          bpp: int = 1):
    codetable = {}

    bytes_per_row = code_w * bpp // 8
    bytes_per_glyph = bytes_per_row * code_h
    pixels_per_byte = 8 // bpp
    mask = (1 << bpp) - 1

    code_count = len(data) // bytes_per_glyph

    for i in range(code_count):
        offset = i * bytes_per_glyph
        codetable[i] = data[offset: offset + bytes_per_glyph]

    if DEBUG:
        print("bytes_per_row:", bytes_per_row)
        print("bytes_per_glyph:", bytes_per_glyph)
        print("code_count:", code_count)

        glyph = codetable[10]
        for y in range(code_h):
            row = glyph[y * bytes_per_row : (y + 1) * bytes_per_row]
            line = ""
            for byte in row:
                for p in range(pixels_per_byte):
                    shift = 8 - bpp * (p + 1)
                    value = (byte >> shift) & mask
                    line += "█" if value else " "
            print(line)

    return codetable


def decode_glyph(glyph_bytes, code_w: int, code_h: int, bpp: int = 1):
    bytes_per_row = code_w * bpp // 8
    pixels_per_byte = 8 // bpp
    mask = (1 << bpp) - 1

    bitmap = []

    for y in range(code_h):
        row_bytes = glyph_bytes[y * bytes_per_row: (y + 1) * bytes_per_row]
        row_pixels = []

        for byte in row_bytes:
            for p in range(pixels_per_byte):
                shift = 8 - bpp * (p + 1)
                value = (byte >> shift) & mask
                row_pixels.append(value)

        bitmap.append(row_pixels[:code_w])

    return bitmap


def bit_to_png(codetable, out_path,
               code_w: int = 16, code_h: int = 16,
               bpp: int = 1, cols: int = 16, scale: int = 1):
    code_count = len(codetable)
    rows = math.ceil(code_count / cols)

    img_w = cols * code_w
    img_h = rows * code_h

    img = Image.new("L", (img_w, img_h), 0)
    max_value = (1 << bpp) - 1
    if max_value == 0:
        max_value = 1

    for i in range(code_count):
        glyph_bytes = codetable[i]
        bitmap = decode_glyph(glyph_bytes, code_w, code_h, bpp)

        col = i % cols
        row = i // cols
        x0 = col * code_w
        y0 = row * code_h

        for y in range(code_h):
            for x in range(code_w):
                value = bitmap[y][x]

                gray = int(value * 255 / max_value)

                img.putpixel((x0 + x, y0 + y), gray)

    if scale != 1:
        img = img.resize((img_w * scale, img_h * scale), Image.NEAREST)

    img.save(out_path)
    print(f"saved: {out_path}")


if __name__ == "__main__":
    DEBUG = 0
    data = read_bin("/home/mark/Code/RomHacking/P1_Tools/extrac/FONT.BIN")
    codetable = transcribe_code_table(data, 16, 16, 1)
    bit_to_png(codetable, "font_grid.png", 16, 16, 1, cols=16, scale=2)