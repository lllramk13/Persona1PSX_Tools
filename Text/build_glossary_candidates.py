"""生成 P1 全量术语候选及上下文，供人工定译和 AI 提示词使用。

没有日语形态分析器时，不能可靠地把任意句子切成“全部名词”。本工具选择高召回、
低噪声的可复现来源：SLPS 名称表、说话人、片假名词、连续汉字词、引号术语、
拉丁字母专名，以及重复完整短句。相同候选会合并来源并统计全语料命中。
"""

import argparse
import importlib.metadata
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_ALL_TEXT = HERE / "all_text.json"
DEFAULT_TRANSLATIONS = HERE / "translations.json"
DEFAULT_OUTPUT = HERE / "glossary_candidates.json"
DEFAULT_SIMPLE_OUTPUT = HERE / "glossary_frequency.json"

MARK_RE = re.compile(r"⟦(\d+)⟧")
KATA_RE = re.compile(r"[ァ-ヺ][ァ-ヺー・]{1,}")
KANJI_RE = re.compile(r"[一-鿿々ヶヵ]{2,}")
LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-']{1,}")
QUOTE_RE = re.compile(r"[「『]([^」』\n]{2,24})[」』]")
SPEAKER_RE = re.compile(r"(?:^|\n)\s*([^\n:：]{1,16})[:：]")

# 这些控制码会造成可见文本边界；提取术语时把它们当换行。
BREAK_CODES = {"<close>", "<wait>", "<clear>", "<end>"}
SLPS_NAME_SECTIONS = {
    0: "character_name",
    3: "demon_persona_name",
    4: "item_name",
    5: "weapon_name",
    6: "armor_ammo_name",
}


def visible_text(entry):
    """把 masked 还原为只含可见文字/边界的字符串，不暴露控制语法。"""
    codes = entry["codes"]

    def replace(match):
        index = int(match.group(1))
        if index >= len(codes):
            return " "
        code = codes[index]
        if code == r"\n" or code in BREAK_CODES:
            return "\n"
        return " "

    text = MARK_RE.sub(replace, entry["masked"])
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def clean_term(term):
    term = re.sub(r"\s+", " ", term).strip(" 　・ー,.、。!?！？:：")
    if len(term) < 2 or len(term) > 40:
        return None
    if term.isdigit() or not re.search(r"[ぁ-ゟァ-ヺ一-鿿A-Za-z]", term):
        return None
    return term


def add_term(kinds, term, kind):
    term = clean_term(term)
    if term:
        kinds[term].add(kind)


def load_sudachi():
    try:
        from sudachipy import Dictionary, SplitMode
    except ImportError as exc:
        raise RuntimeError(
            "未安装 SudachiPy；请在仓库根目录运行 "
            ".venv/bin/python -m pip install -r P1_Tools/requirements.txt，"
            "或显式加 --regex-only 使用旧正则候选。") from exc
    tokenizer = Dictionary(dict="core").create()
    versions = {
        "engine": "SudachiPy",
        "sudachipy": importlib.metadata.version("SudachiPy"),
        "dictionary": "SudachiDict-core",
        "dictionary_version": importlib.metadata.version("SudachiDict-core"),
        "modes": ["C", "A"],
        "pos_filter": "part_of_speech()[0] == '名詞'",
    }
    return tokenizer, SplitMode, versions


def build(all_text_path=DEFAULT_ALL_TEXT, translations_path=DEFAULT_TRANSLATIONS,
          min_count=2, max_examples=5, regex_only=False):
    all_text = json.loads(Path(all_text_path).read_text(encoding="utf-8"))
    translations = json.loads(Path(translations_path).read_text(encoding="utf-8"))
    metadata = {entry["id"]: entry for entry in all_text["entries"]}
    kinds = defaultdict(set)
    sudachi_info = defaultdict(lambda: {
        "normalized": Counter(), "readings": Counter(), "pos": set(),
        "oov": False, "token_count": Counter(),
    })
    texts = []
    exact_ids = defaultdict(list)
    if regex_only:
        tokenizer = split_mode = None
        analyzer = {
            "engine": "regex-only", "modes": [],
            "warning": "未做日语形态分析，只使用片假名/汉字等规则。",
        }
    else:
        tokenizer, split_mode, analyzer = load_sudachi()

    for line_id, entry in translations.items():
        text = visible_text(entry)
        texts.append((line_id, text))
        compact = re.sub(r"\s+", " ", text).strip()
        if 2 <= len(compact) <= 40:
            exact_ids[compact].append(line_id)

        meta = metadata.get(line_id, {})
        if meta.get("source") == "SLPS_005.00":
            kind = SLPS_NAME_SECTIONS.get(meta.get("section"))
            if kind:
                add_term(kinds, compact, kind)

        for match in SPEAKER_RE.finditer(text):
            add_term(kinds, match.group(1), "speaker")
        if regex_only:
            for match in KATA_RE.finditer(text):
                add_term(kinds, match.group(0), "katakana_regex")
            for match in KANJI_RE.finditer(text):
                add_term(kinds, match.group(0), "kanji_compound_regex")
        else:
            # C 保留长复合词/专名，A 拆出可独立统一的短词。
            # 只接受 Sudachi 判定为“名词”的词元。
            for mode_name, mode in (("C", split_mode.C), ("A", split_mode.A)):
                for morpheme in tokenizer.tokenize(text, mode):
                    pos = morpheme.part_of_speech()
                    if not pos or pos[0] != "名詞":
                        continue
                    surface = clean_term(morpheme.surface())
                    if not surface:
                        continue
                    kind = f"sudachi_noun_{mode_name.lower()}"
                    kinds[surface].add(kind)
                    if len(pos) > 1 and pos[1] == "固有名詞":
                        kinds[surface].add("sudachi_proper_noun")
                    info = sudachi_info[surface]
                    info["normalized"][morpheme.normalized_form()] += 1
                    reading = morpheme.reading_form()
                    if reading:
                        info["readings"][reading] += 1
                    info["pos"].add(",".join(pos[:3]))
                    info["oov"] = info["oov"] or morpheme.is_oov()
                    info["token_count"][mode_name] += 1
        for match in LATIN_RE.finditer(text):
            add_term(kinds, match.group(0), "latin")
        for match in QUOTE_RE.finditer(text):
            add_term(kinds, match.group(1), "quoted")

    for text, ids in exact_ids.items():
        if len(ids) >= min_count:
            add_term(kinds, text, "repeated_text")

    output = []
    for term, term_kinds in kinds.items():
        occurrences = 0
        entry_count = 0
        examples = []
        sources = set()
        for line_id, text in texts:
            count = text.count(term)
            if not count:
                continue
            occurrences += count
            entry_count += 1
            source = line_id.split("#", 1)[0]
            sources.add(source)
            if len(examples) < max_examples:
                one_line = re.sub(r"\s+", " ", text).strip()
                at = one_line.find(term)
                left = max(0, at - 35)
                right = min(len(one_line), at + len(term) + 35)
                context = ("…" if left else "") + one_line[left:right] + (
                    "…" if right < len(one_line) else "")
                examples.append({"id": line_id, "text": context})

        authoritative = bool(term_kinds & set(SLPS_NAME_SECTIONS.values()))
        if occurrences < min_count and not authoritative:
            continue
        row = {
            "jp": term,
            "count": occurrences,
            "entry_count": entry_count,
            "source_count": len(sources),
            "kinds": sorted(term_kinds),
            "examples": examples,
            "zh": "",
        }
        if term in sudachi_info:
            info = sudachi_info[term]
            row["sudachi"] = {
                "token_count": dict(sorted(info["token_count"].items())),
                "normalized": [value for value, _ in info["normalized"].most_common()],
                "readings": [value for value, _ in info["readings"].most_common()],
                "pos": sorted(info["pos"]),
                "oov": info["oov"],
            }
        output.append(row)

    priority = {
        "character_name": 0, "demon_persona_name": 1, "item_name": 2,
        "weapon_name": 3, "armor_ammo_name": 4, "speaker": 5,
        "quoted": 6, "sudachi_proper_noun": 7, "sudachi_noun_c": 8,
        "sudachi_noun_a": 9, "katakana_regex": 10,
        "kanji_compound_regex": 11, "latin": 12, "repeated_text": 13,
    }
    output.sort(key=lambda item: (
        min(priority[kind] for kind in item["kinds"]),
        -item["count"], -len(item["jp"]), item["jp"],
    ))
    analyzer["candidate_count"] = len(output)
    analyzer["entry_count"] = len(translations)
    analyzer["min_count"] = min_count
    return output, analyzer


def main():
    parser = argparse.ArgumentParser(description="生成 P1 全量术语候选、频次和上下文")
    parser.add_argument("--all-text", default=str(DEFAULT_ALL_TEXT))
    parser.add_argument("--translations", default=str(DEFAULT_TRANSLATIONS))
    parser.add_argument("-o", "--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--simple-out", default=str(DEFAULT_SIMPLE_OUTPUT),
                        help="只含 jp/count 的精简表")
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--max-examples", type=int, default=5)
    parser.add_argument("--regex-only", action="store_true",
                        help="不用 Sudachi，显式回退到片假名/汉字正则")
    args = parser.parse_args()
    try:
        rows, analyzer = build(
            args.all_text, args.translations, args.min_count,
            args.max_examples, args.regex_only)
    except RuntimeError as exc:
        raise SystemExit(f"[失败] {exc}")
    # 候选表同时是人工术语工作表；重跑只刷新频次/上下文，
    # 不能把已经定好的中文译名清空。
    out_path = Path(args.out)
    if out_path.exists():
        try:
            old_rows = json.loads(out_path.read_text(encoding="utf-8"))
            old_zh = {row["jp"]: row.get("zh", "") for row in old_rows
                      if isinstance(row, dict) and isinstance(row.get("jp"), str)}
            for row in rows:
                row["zh"] = old_zh.get(row["jp"], "")
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    out_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    simple_rows = [{"jp": row["jp"], "count": row["count"]} for row in rows]
    Path(args.simple_out).write_text(
        json.dumps(simple_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    metadata_path = out_path.with_name("glossary_metadata.json")
    metadata_path.write_text(
        json.dumps(analyzer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    by_kind = defaultdict(int)
    for row in rows:
        for kind in row["kinds"]:
            by_kind[kind] += 1
    print(f"已写 {args.out}: {len(rows)} 个术语候选")
    print(f"已写 {args.simple_out}: 仅 jp/count")
    print(f"已写 {metadata_path}: {analyzer['engine']} 分析元数据")
    print("分类: " + ", ".join(f"{key}={value}" for key, value in sorted(by_kind.items())))


if __name__ == "__main__":
    main()
