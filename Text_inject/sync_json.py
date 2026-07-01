#!/usr/bin/env python3
"""把 Text_inject/translations/*.json 里的 zh 合并进 Text/translations.json。

批次文件格式 = {id: {jp, zh}}(与 translations.json 同构，zh 已填、带 ⟦n⟧ 占位符)。
校验通过才写：
  ① id 必须在 translations.json 里；
  ② 批次 jp 必须与表里 jp 一致(防错位/过期批次——比如 TALK 重切前翻的批次)；
  ③ 占位符必须是 ⟦0..N-1⟧ 各一次、按升序(与回插 restore_masked 同规则，
     过了这关，回插时控制码还原不会挂)。

换行无需单独查：它是控制码之一，占位符校验已保证它没被增删/挪位。
30 字/框不在这里查：要数真实行宽得知道哪些 ⟦n⟧ 是换行(codes 在 all_text，
不在瘦身后的 translations.json)，放到回插阶段查更准；字节超长由 encode.apply_patch 挡。

去重扩散(fan-out)：P1 约 29% 是重复句(同一 jp 出现在多个 id)。合并后会把每句
译文自动填到「别处相同但还空着」的 id，翻一句顶多处。只填空的、不覆盖已有译文；
同一 jp 出现两种不同译文 = 有歧义，不扩散那句并报告。--no-fanout 可关。

用法:
    python3 sync_json.py            # 干跑，只出报告，不写文件
    python3 sync_json.py --apply    # 写回 translations.json(先备份 .bak)
    python3 sync_json.py --apply --no-fanout   # 只填批次里的确切 id，不扩散
"""
import glob
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BATCH_DIR = HERE / "translations"
TABLE = HERE.parent / "Text" / "translations.json"
MARK = re.compile(r"⟦(\d+)⟧")


def placeholders_ok(jp: str, zh: str) -> bool:
    """zh 的占位符必须恰好是 ⟦0..N-1⟧、按升序各一次(N = jp 里占位符个数)。"""
    n = len(MARK.findall(jp))
    nums = [int(m) for m in MARK.findall(zh)]
    return nums == list(range(n))


def main() -> None:
    apply = "--apply" in sys.argv
    if not TABLE.exists():
        sys.exit(f"找不到 {TABLE}")
    table = json.loads(TABLE.read_text(encoding="utf-8"))
    files = sorted(glob.glob(str(BATCH_DIR / "*.json")))
    if not files:
        sys.exit(f"没找到批次文件: {BATCH_DIR}/*.json")

    merged = 0
    unknown, jp_mismatch, ph_error, conflicts = [], [], [], []
    seen: dict[str, tuple[str, str]] = {}          # id -> (zh, 来源文件)，跨批次冲突检测

    for path in files:
        name = Path(path).name
        batch = json.loads(Path(path).read_text(encoding="utf-8"))
        for line_id, item in batch.items():
            zh = (item or {}).get("zh", "")
            if not zh:
                continue
            if line_id not in table:
                unknown.append((line_id, name)); continue
            if item.get("jp") != table[line_id].get("jp"):
                jp_mismatch.append((line_id, name)); continue
            if not placeholders_ok(table[line_id]["jp"], zh):
                ph_error.append((line_id, name)); continue
            if line_id in seen and seen[line_id][0] != zh:
                conflicts.append((line_id, seen[line_id][1], name))
            seen[line_id] = (zh, name)
            table[line_id]["zh"] = zh
            merged += 1

    # --- 去重扩散：把同一 jp 的译文填到别处相同但还空着的 id ---
    fanned = 0
    dup_conflict = []
    if "--no-fanout" not in sys.argv:
        jp_to_zh, ambiguous = {}, set()
        for v in table.values():                       # 按 jp 收集已填译文
            if v.get("zh"):
                jp = v["jp"]
                if jp in jp_to_zh and jp_to_zh[jp] != v["zh"]:
                    ambiguous.add(jp)                  # 同句两种译文 → 不扩散
                else:
                    jp_to_zh[jp] = v["zh"]
        for jp in ambiguous:
            jp_to_zh.pop(jp, None)
        dup_conflict = sorted(ambiguous)
        for v in table.values():                       # 只填空的，不覆盖已有译文
            if not v.get("zh") and v["jp"] in jp_to_zh:
                v["zh"] = jp_to_zh[v["jp"]]
                fanned += 1

    print(f"批次文件 {len(files)} 个，直接合并 zh {merged} 条，去重扩散 +{fanned} 条")

    def report(label, items):
        if items:
            print(f"  ⚠ {label}: {len(items)}  例: {items[:5]}")
    report("id 不在表里(跳过)", unknown)
    report("jp 与表不一致/过期批次(跳过)", jp_mismatch)
    report("占位符不合规(跳过)", ph_error)
    report("跨批次同 id 不同译(后者覆盖)", conflicts)
    report("同句多种译文·未扩散(需人工统一)", dup_conflict)

    if not apply:
        print("\n[干跑] 未写文件。确认无误后加 --apply 写回。")
        return

    shutil.copy(TABLE, str(TABLE) + ".bak")
    TABLE.write_text(
        json.dumps(table, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    filled = sum(1 for v in table.values() if v.get("zh"))
    print(f"\n已写回 {TABLE}(备份 {TABLE.name}.bak)。表内已填 zh 合计 {filled}/{len(table)}")


if __name__ == "__main__":
    main()
