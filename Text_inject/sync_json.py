#!/usr/bin/env python3
"""导出去重翻译文件，并把 AI/批次译文安全合并进 translations.json。

用法：
    python3 Text_inject/sync_json.py export
        生成 Text/unique_translations.json 和独立 ID 索引；跳过 ALIEN。

    python3 Text_inject/sync_json.py
        干跑：检查批次文件与 unique_translations.json，不写文件。

    python3 Text_inject/sync_json.py --apply
        校验通过的译文写回 Text/translations.json，并先生成 .bak。

    python3 Text_inject/sync_json.py align
        把 translations.json 里已有的译文补进 unique_translations.json 空项。

    python3 Text_inject/sync_json.py --apply --no-fanout
        写回，但不把已有译文扩散到其他相同 jp 的空记录。
"""

import argparse
import glob
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
TEXT_DIR = HERE.parent / "Text"
BATCH_DIR = HERE / "translations"
TABLE = TEXT_DIR / "translations.json"
UNIQUE = TEXT_DIR / "unique_translations.json"
UNIQUE_INDEX = TEXT_DIR / "unique_translation_index.json"
OPEN_REQUESTS = TEXT_DIR / "p1_open_requests_changes.json"
ALL_TEXT = TEXT_DIR / "all_text.json"
MARK = re.compile(r"⟦(\d+)⟧")
REQUEST_BLOCK = re.compile(r"\s*--\[(close|clear)\]--\s*", re.IGNORECASE)
REQUEST_CONTROL = re.compile(r"\n|<[^>]+>")

HELP_TEXT = """操作：
  sync（默认）  干跑检查所有翻译来源，不写文件
  export        从 translations.json 生成去重 AI 文件和 ID 索引
  align         用 translations.json 补齐 unique_translations.json 的空译文
  help          显示本帮助

常用命令：
  python3 Text_inject/sync_json.py export
  python3 Text_inject/sync_json.py align
  python3 Text_inject/sync_json.py
  python3 Text_inject/sync_json.py --apply
  python3 Text_inject/sync_json.py --apply --no-fanout

同步时读取：
  Text_inject/translations/*.json
  Text/unique_translations.json + unique_translation_index.json
  Text/p1_open_requests_changes.json（存在时）

注意：
  默认 sync 只是干跑。只有 --apply 会备份并写回 Text/translations.json。
  all_text.json 是 ROM 提取数据，脚本不会修改它。
"""


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path: Path, data) -> None:
    """先写临时文件，再替换目标，避免中途失败损坏 JSON。"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
    temporary.replace(path)


def placeholders_ok(jp: str, zh: str) -> bool:
    """zh 的占位符必须恰好是 ⟦0..N-1⟧，按升序各出现一次。"""
    count = len(MARK.findall(jp))
    numbers = [int(number) for number in MARK.findall(zh)]
    return numbers == list(range(count))


def build_jp_index(table):
    """建立 jp -> 原始 ID 列表；AI 导出不包含 ALIEN。"""
    index = defaultdict(list)
    for line_id, item in table.items():
        if "ALIEN" in line_id.upper():
            continue
        index[item["jp"]].append(line_id)
    return index


def existing_unique_zh():
    """重新导出时，根据 jp 保留 AI 文件中已有的翻译。"""
    if not UNIQUE.exists():
        return {}
    return {
        item["jp"]: item.get("zh", "")
        for item in read_json(UNIQUE).values()
        if item.get("jp")
    }


def source_zh_for_ids(table, line_ids):
    translations = {
        table[line_id].get("zh", "")
        for line_id in line_ids
        if table[line_id].get("zh", "")
    }
    if len(translations) > 1:
        raise ValueError(f"相同 jp 存在多个不同 zh：{line_ids}")
    return next(iter(translations), "")


def export_unique() -> None:
    if not TABLE.exists():
        sys.exit(f"找不到 {TABLE}")

    table = read_json(TABLE)
    jp_index = build_jp_index(table)
    old_zh = existing_unique_zh()
    unique_data = {}
    id_index = {}

    for number, (jp, line_ids) in enumerate(jp_index.items(), start=1):
        unique_id = f"u{number:06d}"
        unique_data[unique_id] = {
            "jp": jp,
            "zh": old_zh.get(jp, "") or source_zh_for_ids(table, line_ids),
        }
        id_index[unique_id] = line_ids

    write_json(UNIQUE, unique_data)
    write_json(UNIQUE_INDEX, id_index)

    exported = sum(len(line_ids) for line_ids in id_index.values())
    duplicates = exported - len(unique_data)
    translated = sum(1 for item in unique_data.values() if item.get("zh"))
    print(f"原始记录：{len(table)}")
    print(f"跳过 ALIEN：{len(table) - exported}")
    print(f"唯一文本：{len(unique_data)}")
    print(f"已有译文：{translated}")
    print(f"待翻译：{len(unique_data) - translated}")
    print(f"去除重复：{duplicates}")
    print(f"AI 翻译文件：{UNIQUE}")
    print(f"ID 索引文件：{UNIQUE_INDEX}")


def align_unique() -> None:
    """用总表补齐 unique 的空 zh；冲突只报告，不覆盖任何已有译文。"""
    if not TABLE.exists():
        sys.exit(f"找不到 {TABLE}")
    if not UNIQUE.exists() or not UNIQUE_INDEX.exists():
        sys.exit("找不到去重翻译文件或 ID 索引，请先执行 export")

    table = read_json(TABLE)
    unique_data = read_json(UNIQUE)
    id_index = read_json(UNIQUE_INDEX)
    filled = 0
    already_aligned = 0
    unique_only = 0
    conflicts = []
    invalid = []

    for unique_id, unique_item in unique_data.items():
        line_ids = id_index.get(unique_id)
        if not isinstance(line_ids, list) or not line_ids:
            invalid.append((unique_id, "缺少 ID 索引"))
            continue

        jp = unique_item.get("jp")
        table_translations = set()
        bad_id = False
        for line_id in line_ids:
            if line_id not in table or table[line_id].get("jp") != jp:
                invalid.append((unique_id, line_id))
                bad_id = True
                break
            if table[line_id].get("zh"):
                table_translations.add(table[line_id]["zh"])
        if bad_id:
            continue

        unique_zh = unique_item.get("zh", "")
        if len(table_translations) > 1:
            conflicts.append((unique_id, "总表中相同 jp 有多个译文"))
            continue
        if not table_translations:
            if unique_zh:
                unique_only += 1
            continue

        table_zh = next(iter(table_translations))
        if not unique_zh:
            unique_item["zh"] = table_zh
            filled += 1
        elif unique_zh == table_zh:
            already_aligned += 1
        else:
            conflicts.append((unique_id, "unique 与总表译文不同"))

    write_json(UNIQUE, unique_data)
    pending = sum(1 for item in unique_data.values() if not item.get("zh"))
    print(f"从 translations.json 补入 unique：{filled} 条")
    print(f"两边已一致：{already_aligned} 条")
    print(f"仅 unique 有译文（尚未写回总表）：{unique_only} 条")
    print(f"unique 仍待翻译：{pending} 条")
    if conflicts:
        print(f"  ⚠ 译文冲突（保留 unique 原值）：{len(conflicts)}  例: {conflicts[:5]}")
    if invalid:
        print(f"  ⚠ ID 索引无效（跳过）：{len(invalid)}  例: {invalid[:5]}")
    print(f"已更新：{UNIQUE}")


def load_unique_candidates():
    """把 unique_id 格式转换回 (原始 ID, jp, zh, 来源)。"""
    if not UNIQUE.exists() and not UNIQUE_INDEX.exists():
        return []
    if not UNIQUE.exists() or not UNIQUE_INDEX.exists():
        sys.exit("去重翻译文件与 ID 索引必须同时存在，请重新执行 export")

    unique_data = read_json(UNIQUE)
    id_index = read_json(UNIQUE_INDEX)
    candidates = []

    for unique_id, item in unique_data.items():
        zh = (item or {}).get("zh", "")
        if not zh:
            continue
        if unique_id not in id_index:
            candidates.append((None, item.get("jp"), zh, unique_id))
            continue
        for line_id in id_index[unique_id]:
            candidates.append((line_id, item.get("jp"), zh, unique_id))

    return candidates


def control_family(code: str) -> str:
    """忽略控制码参数，只比较控制码种类，例如 <pause> 与 <pause:1000>。"""
    if code == "\n" or code == r"\n":
        return "newline"
    match = re.fullmatch(r"<([^>:]+)(?::[^>]*)?>", code)
    return match.group(1).lower() if match else code.lower()


def request_text_to_masked(text: str, codes: list[str]) -> str:
    """把 open-requests 的可读控制码按 all_text 的 codes 转成 ⟦n⟧。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 请求文件为了显示清楚，在块控制码两边加入了空行；这些不是游戏换行。
    text = REQUEST_BLOCK.sub(lambda match: f"<{match.group(1).lower()}>", text)
    found = list(REQUEST_CONTROL.finditer(text))
    parts = []
    position = 0
    expected_index = 0

    for match in found:
        actual = match.group(0)
        actual_family = control_family(actual)
        parts.append(text[position:match.start()])

        # 翻译前端允许重新排版。若原文此处有换行而译文省略了，补回占位符。
        while (expected_index < len(codes)
               and control_family(codes[expected_index]) == "newline"
               and actual_family != "newline"):
            parts.append(f"⟦{expected_index}⟧")
            expected_index += 1

        if actual_family == "newline":
            if (expected_index < len(codes)
                    and control_family(codes[expected_index]) == "newline"):
                parts.append(f"⟦{expected_index}⟧")
                expected_index += 1
            # 多出来的换行只是请求文件的可读排版，不写进游戏文本。
            position = match.end()
            continue

        if expected_index >= len(codes):
            raise ValueError(f"出现多余控制码：{actual!r}")
        expected = codes[expected_index]
        if actual_family != control_family(expected):
            raise ValueError(
                f"第 {expected_index} 个控制码类型不符："
                f"期望 {expected!r}，实际 {actual!r}"
            )
        parts.append(f"⟦{expected_index}⟧")
        expected_index += 1
        position = match.end()

    parts.append(text[position:])

    # 前端经常省略最后的换行和 TALK 的 <end>，这两类可以无歧义补回。
    while expected_index < len(codes):
        family = control_family(codes[expected_index])
        if family not in {"newline", "end"}:
            raise ValueError(
                f"缺少第 {expected_index} 个控制码：{codes[expected_index]!r}"
            )
        parts.append(f"⟦{expected_index}⟧")
        expected_index += 1

    return "".join(parts)


def load_open_request_candidates():
    """读取 p1_open_requests_changes.json，严格转换为标准合并候选。"""
    if not OPEN_REQUESTS.exists():
        return [], [], []
    if not ALL_TEXT.exists():
        sys.exit(f"读取 {OPEN_REQUESTS.name} 需要 {ALL_TEXT}")

    requests = read_json(OPEN_REQUESTS)
    if not isinstance(requests, list):
        sys.exit(f"{OPEN_REQUESTS} 顶层必须是请求数组")
    extracted = read_json(ALL_TEXT)
    all_text_by_id = {
        item["id"]: item
        for item in extracted.get("entries", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }

    candidates = []
    unsupported = []
    conversion_errors = []
    for request in requests:
        request_name = request.get("title") or request.get("id") or "未命名请求"
        changes = request.get("changes", {})
        if not isinstance(changes, dict):
            conversion_errors.append((request_name, "changes 不是对象"))
            continue

        for line_id, change in changes.items():
            source = f"{OPEN_REQUESTS.name}:{request_name}"
            if line_id.startswith("dup:"):
                unsupported.append((line_id, source))
                continue
            if line_id not in all_text_by_id:
                unsupported.append((line_id, source))
                continue

            new_text = (change or {}).get("new", "")
            if not new_text:
                continue
            extracted_item = all_text_by_id[line_id]
            try:
                masked = request_text_to_masked(new_text, extracted_item.get("codes", []))
            except ValueError as error:
                conversion_errors.append((line_id, source, str(error)))
                continue
            candidates.append((line_id, extracted_item.get("masked"), masked, source))

    return candidates, unsupported, conversion_errors


def sync_translations(apply: bool, fanout: bool) -> None:
    if not TABLE.exists():
        sys.exit(f"找不到 {TABLE}")

    table = read_json(TABLE)
    batch_files = sorted(glob.glob(str(BATCH_DIR / "*.json")))
    unique_candidates = load_unique_candidates()
    open_candidates, unsupported_requests, request_conversion_errors = (
        load_open_request_candidates()
    )
    if not batch_files and not unique_candidates and not open_candidates:
        sys.exit("没有可合并的批次译文、去重译文或 open-request 译文")

    merged = 0
    unique_merged = 0
    unknown, jp_mismatch, ph_error, conflicts = [], [], [], []
    seen = {}  # 原始 ID -> (zh, 来源)

    def merge_one(line_id, jp, zh, source, is_unique=False):
        nonlocal merged, unique_merged
        if not zh:
            return
        if line_id is None or line_id not in table:
            unknown.append((line_id, source))
            return
        if jp != table[line_id].get("jp"):
            jp_mismatch.append((line_id, source))
            return
        if not placeholders_ok(table[line_id]["jp"], zh):
            ph_error.append((line_id, source))
            return
        if line_id in seen and seen[line_id][0] != zh:
            conflicts.append((line_id, seen[line_id][1], source))
        seen[line_id] = (zh, source)
        table[line_id]["zh"] = zh
        merged += 1
        if is_unique:
            unique_merged += 1

    for path_string in batch_files:
        path = Path(path_string)
        batch = read_json(path)
        for line_id, item in batch.items():
            item = item or {}
            merge_one(line_id, item.get("jp"), item.get("zh", ""), path.name)

    for line_id, jp, zh, unique_id in unique_candidates:
        merge_one(line_id, jp, zh, f"{UNIQUE.name}:{unique_id}", is_unique=True)

    for line_id, jp, zh, source in open_candidates:
        merge_one(line_id, jp, zh, source)

    fanned = 0
    duplicate_conflicts = []
    if fanout:
        jp_to_zh = {}
        ambiguous = set()
        for item in table.values():
            if item.get("zh"):
                jp = item["jp"]
                if jp in jp_to_zh and jp_to_zh[jp] != item["zh"]:
                    ambiguous.add(jp)
                else:
                    jp_to_zh[jp] = item["zh"]
        for jp in ambiguous:
            jp_to_zh.pop(jp, None)
        duplicate_conflicts = sorted(ambiguous)

        for item in table.values():
            if not item.get("zh") and item["jp"] in jp_to_zh:
                item["zh"] = jp_to_zh[item["jp"]]
                fanned += 1

    print(
        f"批次文件 {len(batch_files)} 个，直接合并 zh {merged} 条"
        f"（其中去重文件 {unique_merged} 条），去重扩散 +{fanned} 条"
    )
    if OPEN_REQUESTS.exists():
        print(f"open requests 严格转换成功：{len(open_candidates)} 条")

    def report(label, items):
        if items:
            print(f"  ⚠ {label}: {len(items)}  例: {items[:5]}")

    report("id 不在表里（跳过）", unknown)
    report("jp 与表不一致/索引过期（跳过）", jp_mismatch)
    report("占位符不合规（跳过）", ph_error)
    report("同 id 不同译（后者覆盖）", conflicts)
    report("同句多种译文·未扩散（需人工统一）", duplicate_conflicts)
    report("open requests 无法定位的 ID（跳过）", unsupported_requests)
    report("open requests 控制码无法对齐（跳过）", request_conversion_errors)

    if not apply:
        print("\n[干跑] 未写文件。确认无误后加 --apply 写回。")
        return

    shutil.copy2(TABLE, str(TABLE) + ".bak")
    write_json(TABLE, table)
    filled = sum(1 for item in table.values() if item.get("zh"))
    print(f"\n已写回 {TABLE}（备份 {TABLE.name}.bak）")
    print(f"表内已填 zh：{filled}/{len(table)}")
    print("\n同步 unique_translations.json：")
    align_unique()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="导出去重翻译，并安全校验、合并多种翻译来源。",
        epilog=HELP_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=("sync", "export", "align", "help"),
        default="sync",
        help="要执行的操作；省略时为 sync 干跑",
    )
    parser.add_argument("--apply", action="store_true", help="备份并写回 translations.json")
    parser.add_argument("--no-fanout", action="store_true", help="不向相同 jp 的空记录扩散译文")
    args = parser.parse_args()

    if args.action == "help":
        parser.print_help()
    elif args.action == "export":
        if args.apply or args.no_fanout:
            parser.error("export 不使用 --apply 或 --no-fanout")
        export_unique()
    elif args.action == "align":
        if args.apply or args.no_fanout:
            parser.error("align 不使用 --apply 或 --no-fanout")
        align_unique()
    else:
        sync_translations(apply=args.apply, fanout=not args.no_fanout)


if __name__ == "__main__":
    main()
