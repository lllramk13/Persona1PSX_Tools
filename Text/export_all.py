"""一次性导出 P1 已破解的全部文本，并保留每条文本的来源信息。

默认输出 ``all_text.json``，覆盖：
  * TALK/*.BIN
  * ADV/E0.BIN ... E3.BIN
  * D??/D??.BIN（主地图文件，不含 M/S/VB 资源）
  * SLPS_005.00

用法：
    python3 export_all.py
    python3 export_all.py -o p1_all_text.json
"""
import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import dump


HERE = Path(__file__).resolve().parent
P1_ROOT = HERE.parent
DEFAULT_EXTRACTED = P1_ROOT / "extrac"
DEFAULT_OUTPUT = HERE / "all_text.json"


def source_files(extracted):
    """返回纳入汉化范围的文件，顺序固定以保证全局 ID/JSON 可复现。"""
    files = []
    files.extend(sorted((extracted / "TALK").glob("*.BIN")))
    files.extend(extracted / "ADV" / f"E{i}.BIN" for i in range(4))
    files.extend(sorted(extracted.glob("D??/D??.BIN")))
    files.append(extracted / "SLPS_005.00")
    missing = [p for p in files if not p.is_file()]
    if missing:
        raise FileNotFoundError("缺少输入文件: " + ", ".join(map(str, missing)))
    return files


def export_all(extracted=DEFAULT_EXTRACTED):
    extracted = Path(extracted).resolve()
    cfg = dump.load_format()
    codetable = dump.load_codetable()
    entries = []
    sources = []
    format_counts = Counter()

    for path in source_files(extracted):
        fmt, decoded = dump.decode_file(str(path), cfg)
        result = dump.to_json(str(path), fmt, decoded, cfg, codetable)
        source = path.relative_to(extracted).as_posix()
        sources.append({
            "source": source,
            "format": fmt,
            "count": result["count"],
        })
        format_counts[fmt] += result["count"]

        for line in result["lines"]:
            entries.append({
                "id": f"{source}#{line['id']}",
                "source": source,
                "format": fmt,
                "local_id": line["id"],
                "section": line["section"],
                "span": line["span"],
                "jp": line["jp"],
            })

    codetable_path = Path(dump.CODETABLE)
    return {
        "schema": 1,
        "extracted_root": "extrac",
        "codetable": "Codetable/codetable_og.json",
        "codetable_sha256": hashlib.sha256(codetable_path.read_bytes()).hexdigest(),
        "file_count": len(sources),
        "entry_count": len(entries),
        "format_counts": dict(sorted(format_counts.items())),
        "sources": sources,
        "entries": entries,
    }


def main():
    ap = argparse.ArgumentParser(description="导出 P1 全部已破解文本到一个 JSON")
    ap.add_argument("-o", "--out", default=str(DEFAULT_OUTPUT), help="输出 JSON 路径")
    ap.add_argument("--extracted", default=str(DEFAULT_EXTRACTED), help="解包文件根目录")
    args = ap.parse_args()

    result = export_all(args.extracted)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"已写 {out}：{result['file_count']} 个源文件，"
        f"{result['entry_count']} 条文本；{result['format_counts']}"
    )


if __name__ == "__main__":
    main()
