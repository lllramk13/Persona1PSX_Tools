"""动态字库核心分配器的 stdlib unittest；不渲染字形、不修改仓库文件。"""
from __future__ import annotations

import unittest
from collections import Counter

from Text_inject.dynamic_font import PINNED, select_core_constrained


def capacity(glyphs: int) -> dict[str, int]:
    return {
        "physical_bytes": 0x800,
        "logical_end": 0,
        "padding_bytes": 4 + glyphs * 32,
        "glyph_capacity": glyphs,
    }


class ConstrainedCoreTests(unittest.TestCase):
    def test_tight_section_chars_are_forced_into_core(self):
        frequency = Counter({"甲": 8, "乙": 7, "丙": 2, "丁": 1, "戊": 1})
        sections = {0: set("甲乙"), 1: set("乙丙")}
        capacities = {0: capacity(0), 1: capacity(1)}
        universe = set(frequency) | set(PINNED)
        core_size = len(universe) - 2

        core, report = select_core_constrained(
            frequency, sections, capacities, core_size)

        self.assertIn("甲", core)
        self.assertIn("乙", core)
        self.assertLessEqual(len(sections[1] - set(core)), 1)
        self.assertEqual(report["strategy"], "section_padding_constrained")

    def test_layout_is_deterministic(self):
        frequency = Counter({"甲": 8, "乙": 7, "丙": 2, "丁": 1, "戊": 1})
        sections = {0: set("甲乙丙"), 1: set("乙丁戊")}
        capacities = {0: capacity(1), 1: capacity(1)}
        core_size = len(set(frequency) | set(PINNED)) - 2

        first, _ = select_core_constrained(
            frequency, sections, capacities, core_size)
        second, _ = select_core_constrained(
            frequency, sections, capacities, core_size)
        self.assertEqual(first, second)

    def test_impossible_constraints_fail_closed(self):
        frequency = Counter({"甲": 1})
        sections = {0: {"甲"}}
        capacities = {0: capacity(0)}
        # 所有固定字符已占满核心，但零容量 section 又要求“甲”必须进核心。
        with self.assertRaisesRegex(ValueError, "无法满足全部 padding"):
            select_core_constrained(
                frequency, sections, capacities, len(set(PINNED)))


if __name__ == "__main__":
    unittest.main()
