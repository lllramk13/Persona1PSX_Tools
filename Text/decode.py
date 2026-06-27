"""
P1 通用文本解码器
=================
全游戏通用：把一段「变长字节流」解析成 token 列表。

编码规则（TALK / E / D 都一样）：
    单字节 0x00-0x7F, 0x88-0xFE  → 一个字，slot = 该字节本身
    0x80-0x87 + 下一字节 XX       → 一个字，slot = (b-0x80)*256 + XX   (高位字逃逸)
    0xFF      + 下一字节           → 控制码

控制码的「含义 + 长度」各文件类型不同，由调用方传入的 ctrl 表决定，
所以这个解码器本身是通用的——换张 ctrl 表就能解别的文件。

token 形式：
    ("char", slot)            一个字（单字节 或 80-87 逃逸）
    ("ctrl", code, params)    一个控制码；params = 它后面跟的参数字节（颜色/帧数等），可能为空
"""


def decode(data, start, end, ctrl):
    """
    data       : 整个文件 bytes
    start, end : 这一句的字节区间 [start, end)
    ctrl       : {控制码byte(int): (名字str, 整条长度int)}；未在表里的 FF?? 默认按 2 字节吃
    返回        : token 列表
    """
    out = []
    i = start
    while i < end:
        b = data[i]

        if b == 0xFF:                                       # —— 控制码 ——
            code = data[i + 1] if i + 1 < len(data) else 0
            length = ctrl.get(code, (None, 2))[1]           # 只取长度；含义在渲染时查
            params = bytes(data[i + 2:i + length])          # 控制码后面跟的参数
            out.append(("ctrl", code, params))
            i += length

        elif 0x80 <= b <= 0x87:                             # —— 逃逸：高位字（两字节）——
            lo = data[i + 1] if i + 1 < len(data) else 0
            slot = (b - 0x80) * 256 + lo
            out.append(("char", slot))
            i += 2

        else:                                               # —— 单字节：直接就是 slot ——
            out.append(("char", b))
            i += 1

    return out


# 控制码名字属于这几类时，渲染文本输出真换行，便于阅读
_NEWLINE_NAMES = {"nl", "newline"}
_BREAK_NAMES = {"clear", "close", "end"}


def tokens_to_text(tokens, codetable, ctrl):
    """
    把 token 列表渲染成可读字符串（给人看/给翻译用）。
    codetable : {slot(int): '字'}；没有的 slot 显示成 {slot}（等码表补齐就自动变真字）
    ctrl      : 同 decode 用的那张表（把控制码翻成名字）
    """
    parts = []
    for tok in tokens:
        if tok[0] == "char":
            slot = tok[1]
            parts.append(codetable.get(slot, "{%d}" % slot))
        else:                                               # ("ctrl", code, params)
            code = tok[1]
            name = ctrl.get(code, (f"FF{code:02X}?", 2))[0]
            if name in _NEWLINE_NAMES:
                parts.append("\n")
            elif name in _BREAK_NAMES:
                parts.append(f"\n--[{name}]--\n")
            elif name == "pad":
                pass                                        # 填充/空，忽略
            else:
                parts.append(f"<{name}>")
    return "".join(parts)
