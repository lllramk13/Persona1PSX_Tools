"""从 all_text.json 同步人工维护的 translations.json。

翻译表采用最小结构::

    {
      "ADV/E0.BIN#0:0": {
        "jp": "原文⟦0⟧",   # = all_text 的 masked(带 ⟦n⟧ 占位符的可读日文)
        "zh": "译文⟦0⟧"
      }
    }

源文(masked)与 codes 只存一份在 all_text.json。这里的 jp 一身两职:既是翻译源,
又当漂移检测的锚——回插所需的 codes 按 id 去 all_text.json(或重新解码原文件)取。

重复运行会补入新 ID、清理尚未翻译的失效 ID,并保留已有 zh。如果某条已经翻译、
但其源文(masked)与最新提取结果不同,脚本会拒绝覆盖,要求人工核对。
"""

import argparse
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_ALL_TEXT = HERE / "all_text.json"
DEFAULT_TRANSLATIONS = HERE / "translations.json"


class SyncError(ValueError):
    pass


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sync(all_text_path=DEFAULT_ALL_TEXT, translations_path=DEFAULT_TRANSLATIONS):
    all_text_path = Path(all_text_path)
    translations_path = Path(translations_path)
    extracted = load_json(all_text_path)
    entries = extracted.get("entries")
    if not isinstance(entries, list):
        raise SyncError(f"{all_text_path} 缺少 entries 数组")

    current = {}
    if translations_path.exists():
        current = load_json(translations_path)
        if not isinstance(current, dict):
            raise SyncError(f"{translations_path} 顶层必须是 ID→{{jp,zh}} 对象")

    result = {}
    conflicts = []
    added = updated = preserved = 0
    seen = set()
    for entry in entries:
        line_id = entry.get("id")
        source = entry.get("masked")          # 存进 jp 字段的源文(带 ⟦n⟧)
        if not isinstance(line_id, str) or not isinstance(source, str):
            raise SyncError("all_text 条目缺少合法的 id/masked")
        if line_id in seen:
            raise SyncError(f"all_text ID 重复: {line_id}")
        seen.add(line_id)

        old = current.get(line_id)
        if old is None:
            result[line_id] = {"jp": source, "zh": ""}
            added += 1
            continue
        if not isinstance(old, dict) or not isinstance(old.get("zh", ""), str):
            raise SyncError(f"翻译条目格式错误: {line_id}")

        zh = old.get("zh", "")
        source_changed = old.get("jp") != source
        if source_changed and zh:
            conflicts.append(line_id)
            # 暂存旧值只为生成明确错误;发生冲突时不会写文件。
            result[line_id] = dict(old)
        else:
            result[line_id] = {"jp": source, "zh": zh}
            if source_changed:
                updated += 1
            else:
                preserved += 1

    stale_translated = [line_id for line_id, item in current.items()
                        if line_id not in seen and isinstance(item, dict)
                        and item.get("zh")]
    if stale_translated:
        conflicts.extend(stale_translated)

    if conflicts:
        preview = "\n  ".join(conflicts[:20])
        more = f"\n  …另有 {len(conflicts) - 20} 条" if len(conflicts) > 20 else ""
        raise SyncError(
            "发现已有译文对应的源文变化或失效 ID，未写文件；请人工迁移：\n  "
            + preview + more)

    translations_path.parent.mkdir(parents=True, exist_ok=True)
    translations_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    removed_empty = len(set(current) - seen) - len(stale_translated)
    return {
        "total": len(result),
        "added": added,
        "source_updated": updated,
        "preserved": preserved,
        "removed_empty": removed_empty,
    }


def main():
    parser = argparse.ArgumentParser(
        description="同步 P1 最小翻译表(id→{jp,zh})，并保留已有 zh")
    parser.add_argument("--all-text", default=str(DEFAULT_ALL_TEXT))
    parser.add_argument("-o", "--out", default=str(DEFAULT_TRANSLATIONS))
    args = parser.parse_args()
    try:
        stats = sync(args.all_text, args.out)
    except (OSError, json.JSONDecodeError, SyncError) as exc:
        raise SystemExit(f"[失败] {exc}")
    print(
        f"已同步 {args.out}: {stats['total']} 条；新增 {stats['added']}，"
        f"源文更新 {stats['source_updated']}，保留 {stats['preserved']}，"
        f"清理空失效项 {stats['removed_empty']}")


if __name__ == "__main__":
    main()
