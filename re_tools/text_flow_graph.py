#!/usr/bin/env python3
"""E 的 FF55 脚本调用图：展示每次显示调用实际泵到关框为止的字节。

这不是翻译器，而是“原版事实层”。脚本记录地址是稳定 ID；记录操作数给出文本
renderer 本身在 FF01 只置 flag；但 FF55 handler 看到该 flag 后停止逐帧泵送，脚本从
FF55 后继续。因此这里为了描述“单次 FF55 调用”在首个 FF01 停。注意：这只是运行时
调用边界，绝不是底层存储裁切边界；共享/重叠流仍必须完整保留。
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "Text"), str(ROOT / "Text_inject")]

import dump  # noqa: E402
from ebin_rebuild import EFile, RAM  # noqa: E402


def token_at(raw: bytes, off: int, end: int, ctrl):
    byte = raw[off]
    if byte == 0xFF and off + 1 < end:
        code = raw[off + 1]
        length = ctrl.get(code, (None, 2))[1]
        return ("ctrl", code, bytes(raw[off + 2:off + length])), min(end, off + length)
    if 0x80 <= byte <= 0x87 and off + 1 < end:
        return ("char", (byte - 0x80) * 256 + raw[off + 1]), off + 2
    return ("char", byte), off + 1


def trace(raw: bytes, start: int, end: int, ctrl, codetable):
    """把一次 FF55 调用压成节点；首个 FF01 后控制权回脚本。"""
    nodes = []
    off = start
    text_start = None
    text_parts = []

    def flush_text(stop):
        nonlocal text_start, text_parts
        if text_start is not None:
            nodes.append({
                "kind": "text", "start": text_start, "end": stop,
                "text": "".join(text_parts),
                "raw": raw[text_start:stop].hex(" "),
            })
        text_start, text_parts = None, []

    while off < end:
        token, next_off = token_at(raw, off, end, ctrl)
        if token[0] == "char" and token[1] != 0:
            if text_start is None:
                text_start = off
            text_parts.append(codetable.get(token[1], f"{{{token[1]}}}"))
            off = next_off
            continue
        flush_text(off)
        if token[0] == "char":
            pad_start = off
            while off < end and raw[off] == 0:
                off += 1
            nodes.append({
                "kind": "padding", "start": pad_start, "end": off,
                "count": off - pad_start, "raw": raw[pad_start:off].hex(" "),
            })
            continue
        code = token[1]
        name = ctrl.get(code, (f"FF{code:02X}?", 2))[0]
        nodes.append({
            "kind": "control", "start": off, "end": next_off,
            "code": f"FF{code:02X}", "name": name,
            "params": token[2].hex(" "), "raw": raw[off:next_off].hex(" "),
        })
        off = next_off
        if code == 0x01:
            break
    flush_text(off)
    for index, node in enumerate(nodes):
        node["id"] = f"n{index}"
        node["next"] = f"n{index + 1}" if index + 1 < len(nodes) else None
    return nodes, off


def section_entries(ef: EFile, section_index: int):
    sec = ef.section(section_index)
    entries = []
    for site, target in sec.ptr_sites:
        head = site - 4
        if head < 0 or sec.raw[head] != 0xFF or sec.raw[head + 1] not in (0x55, 0x58):
            continue
        entries.append({
            "record": head,
            "opcode": f"FF{sec.raw[head + 1]:02X}",
            "operand_site": site,
            "target": target,
        })
    unique = {(e["record"], e["target"]): e for e in entries}
    return sec, [unique[key] for key in sorted(unique)]


def containing_span(spans, target):
    for start, end in spans:
        if start <= target < end:
            return start, end
    return None


def build_file_report(path: Path, section_index: int, record_filter=None, codetable_path=None):
    ef = EFile(path)
    sec, entries = section_entries(ef, section_index)
    ctrl = dump.load_format()["ctrl"]["efile"]
    if codetable_path:
        raw_table = json.loads(Path(codetable_path).read_text(encoding="utf-8"))
        codetable = {int(key): value for key, value in raw_table.items()}
    else:
        codetable = dump.load_codetable()
    out = []
    for entry in entries:
        if record_filter is not None and entry["record"] not in record_filter:
            continue
        span = containing_span(sec.spans, entry["target"])
        item = dict(entry)
        item["record_hex"] = f"0x{entry['record']:X}"
        item["target_hex"] = f"0x{entry['target']:X}"
        item["record_raw"] = sec.raw[entry["record"]:entry["record"] + 8].hex(" ")
        if span is None:
            item["error"] = "target_not_in_reader_span"
            item["nodes"] = []
        else:
            item["span"] = list(span)
            item["nodes"], item["return_offset"] = trace(
                sec.raw, entry["target"], span[1], ctrl, codetable)
            item["return_offset_hex"] = f"0x{item['return_offset']:X}"
            item["invocation_raw"] = sec.raw[
                entry["target"]:item["return_offset"]].hex(" ")
        out.append(item)
    return {
        "file": str(path), "section": section_index,
        "section_size": sec.size, "entries": out,
    }


def node_signature(node):
    if node["kind"] == "control":
        return f"{node['code']}:{node['params']}"
    if node["kind"] == "padding":
        return f"pad:{node['count']}"
    return f"text:{node['text']}"


def compare_reports(original, patched):
    patched_by_record = {entry["record"]: entry for entry in patched["entries"]}
    rows = []
    for old in original["entries"]:
        new = patched_by_record.get(old["record"])
        row = {"record": old["record_hex"], "old_target": old["target_hex"]}
        if new is None:
            row["status"] = "missing_record"
        else:
            row["new_target"] = new["target_hex"]
            old_controls = [node_signature(n) for n in old["nodes"] if n["kind"] == "control"]
            new_controls = [node_signature(n) for n in new["nodes"] if n["kind"] == "control"]
            old_layout = [node_signature(n) for n in old["nodes"] if n["kind"] != "text"]
            new_layout = [node_signature(n) for n in new["nodes"] if n["kind"] != "text"]
            if old_controls != new_controls:
                row["status"] = "control_path_diff"
                row["old_control_path"] = old_controls
                row["new_control_path"] = new_controls
            elif old_layout != new_layout:
                row["status"] = "padding_path_diff"
                row["old_layout_path"] = old_layout
                row["new_layout_path"] = new_layout
            else:
                row["status"] = "same_path"
        rows.append(row)
    return rows


def write_dot(report, path: Path):
    lines = ["digraph text_flow {", "  rankdir=LR;"]
    for entry in report["entries"]:
        prefix = f"r{entry['record']:x}"
        lines.append(f'  {prefix} [shape=box,label="{entry["opcode"]} @{entry["record_hex"]}"];')
        previous = prefix
        for node in entry["nodes"]:
            node_id = f"{prefix}_{node['id']}"
            if node["kind"] == "text":
                label = node["text"][:24].replace('"', '\\"')
            elif node["kind"] == "control":
                label = f"{node['code']} {node['name']} {node['params']}"
            else:
                label = f"padding x{node['count']}"
            lines.append(f'  {node_id} [label="{label}"];')
            lines.append(f"  {previous} -> {node_id};")
            previous = node_id
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name", help="E0/E1/E2/E3")
    parser.add_argument("section", type=int)
    parser.add_argument("--patched", type=Path)
    parser.add_argument("--record", action="append", type=lambda value: int(value, 0))
    parser.add_argument("--out", type=Path, default=Path("text_flow_graph.json"))
    parser.add_argument("--dot", type=Path)
    args = parser.parse_args()
    source = ROOT / "extrac" / "ADV" / f"{args.name}.BIN"
    record_filter = set(args.record) if args.record else None
    original = build_file_report(source, args.section, record_filter)
    payload = {"original": original}
    if args.patched:
        patched = build_file_report(
            args.patched, args.section, record_filter,
            ROOT / "Codetable" / "codetable_zh.json")
        payload["patched"] = patched
        payload["comparison"] = compare_reports(original, patched)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    if args.dot:
        write_dot(original, args.dot)
    print(f"entries={len(original['entries'])} -> {args.out}")
    if "comparison" in payload:
        counts = {}
        for row in payload["comparison"]:
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        print("comparison", counts)


if __name__ == "__main__":
    main()
