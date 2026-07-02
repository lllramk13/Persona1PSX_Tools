#!/usr/bin/env python3
"""E 段脚本静态全量验证器。

用 ADV.BIN 反汇编得到的引擎事实，在全部 E 段上做脚本图遍历：
  · 长度表 @ ADV.BIN file+0x568F0（RAM 0x800BAC90），s5 += len[op]
  · dispatch: op-0x21 < 0x6A 查表；范围外走默认 handler（同样按长度表推进）
  · 跳转: FF22(+4 无条件) FF23(+4 条件) FF38/FF3E/FF49(+8 条件)
  · 文本: FF55(+4 → 文本指针，脚本继续在记录之后)
  · 终止: FF21（解释器退出）
验证目标：
  1. 从种子出发的脚本走行是否全部落在 FF 记录上（长度表正确性）
  2. FF55 目标 vs dump reader 文本 span 的吻合度
  3. 文本 span 内是否存在"像指针"的对齐字（通用重定位的误报风险）
  4. FF58 等字操作数是否指针
  5. 脚本区覆盖率
"""
import bisect
import json
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/home/mark/Code/RomHacking/P1_Tools")
sys.path.insert(0, str(ROOT / "Text"))
import containers  # noqa: E402

ADV = (ROOT / "extrac" / "ADV.BIN").read_bytes()
LEN_TABLE = ADV[0x568F0:0x568F0 + 0x90]
RAMBASE = 0x80100000
SECTOR = 0x800

JUMP_AT4 = {0x22, 0x23}
JUMP_AT8 = {0x38, 0x3E, 0x49}
TEXT_OP = 0x55
END_OP = 0x21
WORD_VAL_OPS = {0x3F, 0x58}  # 字操作数（32 位值，疑似非指针，待实证）


def sections_of(data):
    ptrs = []
    last = 0
    for o in range(0, SECTOR, 2):
        v = struct.unpack_from("<H", data, o)[0]
        if v <= last or v * SECTOR >= len(data):
            break
        ptrs.append(v)
        last = v
    out = []
    for i, p in enumerate(ptrs):
        start = p * SECTOR
        end = ptrs[i + 1] * SECTOR if i + 1 < len(ptrs) else len(data)
        out.append((start, end))
    return out


class SecStats:
    pass


def walk_script(sec, entry, visited):
    """从段内偏移 entry 走脚本。返回 (clean, records, jump_targets, text_targets, fail_reason)."""
    size = len(sec)
    records = []
    jumps, texts = [], []
    stack = [entry]
    local_seen = set()
    while stack:
        pos = stack.pop()
        while True:
            if pos in local_seen or pos in visited:
                break
            if pos < 0 or pos + 4 > size or pos % 4:
                return False, records, jumps, texts, f"pos out/unaligned {pos:#x}"
            b0 = sec[pos]
            op = sec[pos + 1]
            if b0 != 0xFF:
                return False, records, jumps, texts, f"non-FF byte {b0:02x} @ {pos:#x}"
            if op >= 0x90:
                return False, records, jumps, texts, f"op {op:02x} >= 0x90 @ {pos:#x}"
            ln = LEN_TABLE[op]
            if pos + ln > size:
                return False, records, jumps, texts, f"record overruns section @ {pos:#x}"
            local_seen.add(pos)
            records.append(pos)
            if op == END_OP:
                break
            if op in JUMP_AT4 or op in JUMP_AT8:
                toff = 4 if op in JUMP_AT4 else 8
                tgt = struct.unpack_from("<I", sec, pos + toff)[0]
                rel = tgt - RAMBASE
                if not (0 <= rel < size):
                    return False, records, jumps, texts, \
                        f"jump target {tgt:#x} out of section @ {pos:#x}"
                jumps.append((pos, op, rel))
                if op == 0x22:  # 无条件：只走目标
                    pos = rel
                    continue
                stack.append(rel)  # 条件：两边都走
            elif op == TEXT_OP:
                tgt = struct.unpack_from("<I", sec, pos + 4)[0]
                rel = tgt - RAMBASE
                if not (0 <= rel < size):
                    return False, records, jumps, texts, \
                        f"text target {tgt:#x} out of section @ {pos:#x}"
                texts.append((pos, rel))
            pos += ln
    return True, records, jumps, texts, None


def main():
    grand = Counter()
    fail_examples = []
    ff58_words = []
    op_census = Counter()
    ptr_in_text_examples = []
    text_match = Counter()

    for name in ["E0", "E1", "E2", "E3"]:
        data = (ROOT / "extrac" / "ADV" / f"{name}.BIN").read_bytes()
        spans = containers.read_efile(data)
        secs = sections_of(data)
        # 每段的文本 span（文件偏移 → 段内）
        sec_spans = defaultdict(list)
        for scene, ts, te in spans:
            sec_spans[scene].append((ts - secs[scene][0], te - secs[scene][0]))

        for si, (s0, s1) in enumerate(secs):
            sec = data[s0:s1]
            size = len(sec)
            tspans = sorted(sec_spans[si])
            tstarts = [a for a, _ in tspans]

            def in_text(pos):
                i = bisect.bisect_right(tstarts, pos) - 1
                return i >= 0 and tspans[i][0] <= pos < tspans[i][1]

            # 1) 所有对齐指针字
            ptr_words = []
            for o in range(0, size - 3, 4):
                v = struct.unpack_from("<I", sec, o)[0]
                if RAMBASE <= v < RAMBASE + size:
                    ptr_words.append((o, v - RAMBASE))
            grand["ptr_words"] += len(ptr_words)
            for o, rel in ptr_words:
                if in_text(o):
                    grand["ptr_in_text"] += 1
                    if len(ptr_in_text_examples) < 10:
                        ptr_in_text_examples.append(f"{name} sec{si} +{o:#x}")

            # 2) 种子：目标字节是 FF 且 4 对齐的指针 + 段头 +0x60
            seeds = set()
            for o, rel in ptr_words:
                if in_text(o) or rel % 4 or rel + 4 > size:
                    continue
                if sec[rel] == 0xFF and sec[rel + 1] != 0xFF:
                    seeds.add(rel)
            if size >= 0x64:
                v = struct.unpack_from("<I", sec, 0x60)[0]
                rel = v - RAMBASE
                if 0 <= rel < size and rel % 4 == 0:
                    seeds.add(rel)

            visited = set()
            clean = dirty = 0
            all_text_targets = set()
            for seed in sorted(seeds):
                ok, recs, jumps, texts, why = walk_script(sec, seed, visited)
                if ok:
                    clean += 1
                    visited.update(recs)
                    for pos in recs:
                        op_census[sec[pos + 1]] += 1
                        if sec[pos + 1] == 0x58:
                            ff58_words.append(struct.unpack_from("<I", sec, pos + 4)[0])
                    for _, rel in texts:
                        all_text_targets.add(rel)
                else:
                    dirty += 1
                    if len(fail_examples) < 12:
                        fail_examples.append(f"{name} sec{si} seed +{seed:#x}: {why}")
            grand["seeds_clean"] += clean
            grand["seeds_dirty"] += dirty
            grand["script_records"] += len(visited)

            # 3) FF55 目标 vs reader 文本 span
            for rel in all_text_targets:
                if any(a == rel for a, _ in tspans):
                    text_match["exact"] += 1
                elif in_text(rel):
                    text_match["inside_span"] += 1
                else:
                    text_match["not_in_reader"] += 1
            for a, _ in tspans:
                if a not in all_text_targets:
                    text_match["reader_only"] += 1

    print("=== 总计 ===")
    for k, v in sorted(grand.items()):
        print(f"  {k}: {v}")
    print("\n=== FF55 目标 vs dump reader ===")
    for k, v in text_match.items():
        print(f"  {k}: {v}")
    print("\n=== 文本内指针字（误报风险） ===")
    for e in ptr_in_text_examples:
        print(f"  {e}")
    print("\n=== 走行失败样例 ===")
    for e in fail_examples:
        print(f"  {e}")
    print("\n=== FF58 字操作数是否像指针 ===")
    ptrlike = sum(1 for w in ff58_words if RAMBASE <= w < RAMBASE + 0x9000)
    print(f"  FF58 出现 {len(ff58_words)} 次, 指针样 {ptrlike}")
    for w in ff58_words[:8]:
        print(f"    {w:#010x}")
    print("\n=== opcode 使用频次 ===")
    for op, n in sorted(op_census.items()):
        print(f"  FF{op:02X}: {n}")


if __name__ == "__main__":
    main()
