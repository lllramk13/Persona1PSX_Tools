"""
P1 容器解析
==========
只负责「定位每个文件里每句话在哪」(start/end)，不解码文字。

文本编码全游戏通用（见 decode.py），但「目录(容器)」各文件类型不同，
所以每种文件类型一个 reader。reader 统一返回 spans：
    [(section_idx, start, end), ...]
之后驱动把每个 (start, end) 交给 decode.decode()。
"""
import struct


def detect_type(data):
    """按头部魔数粗判类型（回退用；路径→格式的映射在 format.json 里）。"""
    if data.startswith(b"PS-X EXE"):
        return "slps"
    head = int.from_bytes(data[0:4], "little")
    if head in (0x14, 0x04):        # TALK: 普通=0x14, ETC=0x04
        return "talk"
    # D：首个 u32 同时是顶层指针表大小，也是第一项资源的偏移。
    if 4 <= head <= 0x4000 and head % 4 == 0 and head < len(data):
        count = head // 4
        ptrs = struct.unpack_from(f"<{count}I", data)
        plausible = sum(p == 0 or head <= p < len(data) for p in ptrs)
        if ptrs[0] == head and plausible == count:
            return "dfile"
    return "unknown"


def _read_ptr_table(data, table_off, base, lo, hi):
    """
    从 table_off 顺着读 u32 当指针（值是相对 base 的偏移），返回「绝对偏移」列表。
    指针必须落在 (lo, hi) 内且递增，否则认为表读完了（后面是 FF 填充）。
    """
    ptrs = []
    o = table_off
    last = -1
    while o + 4 <= len(data):
        absu = base + int.from_bytes(data[o:o + 4], "little")
        if not (lo < absu < hi):    # 越界 → 停
            break
        if absu < last:             # 不再递增 → 停
            break
        ptrs.append(absu)
        last = absu
        o += 4
    return ptrs


def read_talk(data):
    """
    TALK 容器。返回 [(section, start, end), ...] 覆盖全部段。

    结构：头部 5 个 u32 = [表偏移, 段0界, 段1界, 段2界, 段3界]
      · 段0：指针表在「表偏移」(=0x14)，指针是【绝对】偏移，文本到 段0界。
      · 段1..4：指针表在「段k界」，指针是【相对该段起点】的偏移，文本到下一个段界
        （最后一段到文件尾）。验证过：段1指针 0x800 → 绝对 段0界+0x800。
      · 空段（表偏移 == 上界，如 ZOMBIKO 后两段）直接跳过。
    """
    table_off = int.from_bytes(data[0:4], "little")
    if table_off == 0x04:
        # ETC 变体：无类别段，指针表紧接表头(0x04)，绝对指针，文本到文件尾
        blocks = [(0x04, 0, len(data))]
    else:
        # 标准：头部 5 个 u32 = [表偏移, 段0界, 段1界, 段2界, 段3界]
        _, *secs = struct.unpack("<5I", data[0:20])        # secs = [sec0, sec1, sec2, sec3]
        bounds = secs + [len(data)]
        # 每个块描述: (指针表偏移, 指针基准, 文本上界)
        blocks = [(table_off, 0, bounds[0])]               # 段0：base=0（绝对指针）
        for k in range(4):
            tbl = secs[k]
            if tbl >= bounds[k + 1]:                        # 空段
                continue
            blocks.append((tbl, tbl, bounds[k + 1]))        # 段k+1：base=段起点（相对指针）

    spans = []
    for section, (tbl, base, hi) in enumerate(blocks):
        ptrs = _read_ptr_table(data, tbl, base, lo=tbl, hi=hi)
        for j, start in enumerate(ptrs):
            end = ptrs[j + 1] if j + 1 < len(ptrs) else hi
            spans.append((section, start, end))
    return spans


def read_efile(data):
    """
    E0-E3 主线脚本容器。

    扇区 0 开头是严格递增的 u16 场景目录；值的单位是 0x800 字节扇区。
    每个场景中 FF 21 00 00 既可能是一条普通脚本命令，也可能引出文本：

      · 后面仍是 FFxx：脚本命令，跳过；
      · 后面直接是字符编码：文本块。

    文本模式只接受已经确认的文本控制码；其他 FFxx 回到脚本/二进制模式。
    块尾偶尔紧跟对齐字节/小结构，不能把它们当字，因此 span 收在最后一个
    “显示结束”控制码（close/wait/clear）之后。
    """
    sector = 0x800
    if len(data) < sector:
        return []

    # 目录没有显式计数；递增性、文件边界和首项=1 共同给出稳定终止条件。
    scene_sectors = []
    last = 0
    for o in range(0, sector, 2):
        value = int.from_bytes(data[o:o + 2], "little")
        if value <= last or value * sector >= len(data):
            break
        scene_sectors.append(value)
        last = value

    if not scene_sectors or scene_sectors[0] != 1:
        raise ValueError("不是有效的 E 文件：u16 扇区目录缺失")

    # E 文本控制码的整条长度。必须用白名单：场景尾部的二进制表里也会偶然
    # 出现 FF00/FF0C 等字节对，若把所有 FF00-FF1F 都当正文就会吞进伪文本。
    ctrl_lengths = {
        0x01: 2,
        0x02: 2,
        0x03: 2,
        0x04: 2,
        0x05: 4,
        0x06: 3,
        0x07: 3,
        0x08: 3,
        0x0E: 3,
        0x0F: 3,
        0x11: 2,
        0x12: 2,
    }
    marker = b"\xff\x21\x00\x00"
    terminal = {0x01, 0x02, 0x04}  # close / wait / clear
    spans = []

    for scene, start_sector in enumerate(scene_sectors):
        scene_start = start_sector * sector
        if scene + 1 < len(scene_sectors):
            scene_end = scene_sectors[scene + 1] * sector
        else:
            scene_end = len(data)

        pos = scene_start
        while True:
            mark = data.find(marker, pos, scene_end)
            if mark < 0:
                break
            text_start = mark + len(marker)
            pos = text_start

            # 普通 FF21 opcode 后面接另一条脚本命令；真文本目前四个 E 文件中
            # 都以字符开头。这个限制有意保守，宁可暴露漏项也不 dump 假文本。
            if text_start >= scene_end or data[text_start] == 0xFF:
                continue

            i = text_start
            text_end = None
            while i < scene_end:
                b = data[i]
                if 0x80 <= b <= 0x87:
                    i += 2
                elif b != 0xFF:
                    i += 1
                else:
                    if i + 1 >= scene_end:
                        break
                    code = data[i + 1]
                    if code not in ctrl_lengths:  # 非文本脚本命令/二进制表
                        break
                    i += ctrl_lengths[code]
                    if code in terminal:
                        text_end = i

            if text_end is not None:
                spans.append((scene, text_start, text_end))

    return spans


def read_dfile(data):
    """
    D00-D24 区域地图容器。

    文件开头是顶层资源指针表：首个 u32 = 表大小 = 项数 * 4。表项为绝对
    文件偏移，0 表示空槽；槽位按资源用途排列，所以后半段不保证物理递增。
    每个非空资源的物理上界由下一个更大的唯一指针给出。

    含消息的脚本资源沿用 E 文件的 FF 21 00 00 + 文本流结构。结构体里的
    16 位数值偶尔也会形成同样的四字节序列，因此还要求正文首字节非零、
    非 FF，并且能够走到 close/wait/clear 控制码。
    """
    if len(data) < 4:
        return []
    table_size = int.from_bytes(data[0:4], "little")
    if table_size < 4 or table_size % 4 or table_size > len(data):
        raise ValueError("不是有效的 D 文件：顶层 u32 指针表缺失")

    count = table_size // 4
    ptrs = list(struct.unpack_from(f"<{count}I", data))
    if ptrs[0] != table_size:
        raise ValueError("不是有效的 D 文件：首资源偏移与表大小不符")
    if any(p and not (table_size <= p < len(data)) for p in ptrs):
        raise ValueError("不是有效的 D 文件：资源指针越界")

    # 一个物理资源可能被多个槽共享；只扫描一次，并以第一个槽号作为 section。
    first_slot = {}
    for slot, ptr in enumerate(ptrs):
        if ptr:
            first_slot.setdefault(ptr, slot)
    starts = sorted(first_slot)

    ctrl_lengths = {
        0x01: 2, 0x02: 2, 0x03: 2, 0x04: 2,
        0x05: 4, 0x06: 3, 0x07: 3, 0x08: 3,
        0x0E: 3, 0x0F: 3, 0x10: 3, 0x11: 2, 0x12: 2,
    }
    marker = b"\xff\x21\x00\x00"
    terminal = {0x01, 0x02, 0x04}
    spans = []

    for idx, resource_start in enumerate(starts):
        resource_end = starts[idx + 1] if idx + 1 < len(starts) else len(data)
        pos = resource_start
        while True:
            mark = data.find(marker, pos, resource_end)
            if mark < 0:
                break
            text_start = mark + len(marker)
            pos = text_start
            if (text_start >= resource_end or
                    data[text_start] in (0x00, 0xFF)):
                continue

            i = text_start
            text_end = None
            while i < resource_end:
                b = data[i]
                if 0x80 <= b <= 0x87:
                    i += 2
                elif b != 0xFF:
                    i += 1
                else:
                    if i + 1 >= resource_end:
                        break
                    code = data[i + 1]
                    if code not in ctrl_lengths:
                        break
                    i += ctrl_lengths[code]
                    if code in terminal:
                        text_end = i

            if text_end is not None:
                spans.append((first_slot[resource_start], text_start, text_end))

    return spans


def _psx_pointer_runs(data):
    """找 PS-X EXE 内连续的 RAM 地址表，返回 (表起点, 表终点, 文件偏移列表)。"""
    if len(data) < 0x800 or not data.startswith(b"PS-X EXE"):
        return []
    load_addr = int.from_bytes(data[0x18:0x1c], "little")
    ram_file_delta = load_addr - 0x800

    def to_file(value):
        off = value - ram_file_delta
        return off if 0x800 <= off < len(data) else None

    runs = []
    o = 0
    while o + 4 <= len(data):
        target = to_file(int.from_bytes(data[o:o + 4], "little"))
        if o % 4 or target is None:
            o += 1
            continue
        start = o
        targets = []
        while o + 4 <= len(data):
            target = to_file(int.from_bytes(data[o:o + 4], "little"))
            if target is None:
                break
            targets.append(target)
            o += 4
        if len(targets) >= 4:
            runs.append((start, o, targets))
    return runs


def _ff01_spans(data, start, end, max_raw=None):
    """切出 [start,end) 内由 FF01 终止的字串；返回不含终止码/零填充的 span。"""
    spans = []
    pos = start
    while pos < end:
        term = data.find(b"\xff\x01", pos, end)
        if term < 0:
            break
        left = pos
        while left < term and data[left] == 0:
            left += 1
        right = term
        while right > left and data[right - 1] == 0:
            right -= 1
        if right > left:
            if max_raw is not None and right - left > max_raw:
                break
            spans.append((left, right))
        pos = term + 2
    return spans


def read_slps(data):
    """
    SLPS_005.00 中已确认的 FF01 字符串表：

      section 0：角色姓名（主说明表之前的一串短字串）
      section 1：技能/物品效果说明（405 项 RAM 指针表，输出唯一字串）
      section 2：剧情/赌场选项（紧跟“辞书0/辞书1”指针表）
      section 3：恶魔/Persona 名（0x38 字节定长记录）
      section 4：物品名（0x20 字节定长记录）
      section 5：武器名（0x20 字节定长记录）
      section 6：弹药/防具/技能等名称（0x20 字节定长记录）

    地址均从 PS-X EXE 头和指针表推导，不依赖写死的文件偏移。
    """
    runs = _psx_pointer_runs(data)
    if not runs:
        raise ValueError("不是有效的 SLPS：找不到 RAM 指针表")

    # 最大地址表是技能/物品说明索引（本版 405 项）。
    _, _, desc_targets = max(runs, key=lambda r: len(r[2]))
    unique_desc = sorted(set(desc_targets))
    desc_start = unique_desc[0]
    desc_limit = max(unique_desc) + 0x100

    spans = []

    # 从说明表首字串向前追溯连续短 FF01 字串；遇见前一块长数据即停止。
    delimiters = []
    q = 0
    while True:
        q = data.find(b"\xff\x01", q, desc_start)
        if q < 0:
            break
        delimiters.append(q)
        q += 2
    name_spans = []
    right = desc_start
    for term in reversed(delimiters):
        prev = data.rfind(b"\xff\x01", 0, term)
        left = prev + 2 if prev >= 0 else 0
        candidate = _ff01_spans(data, left, term + 2, max_raw=32)
        if not candidate:
            break
        name_spans.append(candidate[-1])
        right = left
    for start, end in reversed(name_spans):
        spans.append((0, start, end))

    # 指针表含大量重复项；翻译时只需每个物理字串一次。
    for target in unique_desc:
        term = data.find(b"\xff\x01", target, min(desc_limit, len(data)))
        if term < 0:
            raise ValueError(f"SLPS 说明字串缺少 FF01 终止码: 0x{target:X}")
        spans.append((1, target, term))

    # 8 项交替指向“辞书0/辞书1”的小表，其后紧接选项字串。
    dict_runs = [r for r in runs
                 if 4 <= len(r[2]) <= 16 and r[0] > 0x30000
                 and max(r[2]) < r[0] and r[0] - min(r[2]) < 0x100]
    if dict_runs:
        _, option_start, _ = max(dict_runs, key=lambda r: r[0])
        pos = option_start

        load_addr = int.from_bytes(data[0x18:0x1c], "little")
        ram_file_delta = load_addr - 0x800

        def is_ram_pointer(at):
            if at + 4 > len(data):
                return False
            value = int.from_bytes(data[at:at + 4], "little")
            off = value - ram_file_delta
            return value % 4 == 0 and 0x800 <= off < len(data)

        def starts_record_table(at):
            # 选项区后面是 (RAM字串指针, 4字节元数据) 的 8 字节记录表。
            return all(is_ram_pointer(at + step * 8) for step in range(3))

        while pos < len(data):
            while pos < len(data) and data[pos] == 0:
                pos += 1
            if starts_record_table(pos):
                break
            term = data.find(b"\xff\x01", pos, min(pos + 0x100, len(data)))
            if term < 0:
                break
            end = term
            while end > pos and data[end - 1] == 0:
                end -= 1
            if end > pos:
                spans.append((2, pos, end))
            pos = term + 2

    # 其余名称在定长 gameplay 数据记录里，不带统一指针表。以下布局属于
    # SLPS_005.00 日版这一固定版本；用说明表首地址校验，避免误套到别的版本。
    if desc_start == 0x398D0:
        fixed_tables = [
            (3, 0x33000, 119, 0x38, 0x1C),
            (4, 0x34C00, 162, 0x20, 0x08),
            (5, 0x36040, 144, 0x20, 0x08),
            # 76 项后切换为图表/变长技能结构，不再套 0x20 记录。
            (6, 0x37240, 76, 0x20, 0x08),
        ]
        for section, table_start, record_count, record_size, field_off in fixed_tables:
            for index in range(record_count):
                start = table_start + index * record_size + field_off
                limit = start + 10
                i = start
                while i < limit:
                    b = data[i]
                    if 0x80 <= b <= 0x87:
                        i += 2
                    elif b in (0x00, 0xFF):
                        break
                    else:
                        i += 1
                if i > start:
                    spans.append((section, start, i))

    return spans


# 格式名 → reader。驱动按 detect_type 的结果在这里查。
READERS = {
    "talk": read_talk,
    "efile": read_efile,
    "dfile": read_dfile,
    "slps": read_slps,
}
