"""P1 encoder 的 stdlib unittest；不修改仓库文件。"""

import json
import tempfile
import unittest
from pathlib import Path

import decode as D
import dump
import encode as E


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


class EncodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = dump.load_format()
        cls.codetable = dump.load_codetable()

    def samples(self):
        yield ROOT / "extrac/TALK/SYOUJO.BIN"
        yield ROOT / "extrac/ADV/E0.BIN"
        yield ROOT / "extrac/D00/D00.BIN"
        yield ROOT / "extrac/SLPS_005.00"

    def test_semantic_roundtrip_representative_files(self):
        for path in self.samples():
            with self.subTest(path=path.name):
                fmt, decoded = dump.decode_file(str(path), self.cfg)
                ctrl = self.cfg["ctrl"].get(fmt, {})
                for _, _, _, tokens in decoded:
                    encoded = E.encode_tokens(tokens)
                    # 原盘偶尔把可用单字节的 slot 也写成 80xx；encoder
                    # 会选更短的等价形式，因此验证 token 而非字节外观。
                    self.assertEqual(
                        D.decode(encoded, 0, len(encoded), ctrl), tokens)

    def test_modified_text_low_page_slots_are_escaped(self):
        """修改文本使用游戏安全写法：0x80-0xFE 都写成 80xx。"""
        for slot in (0x80, 0x88, 0x9B, 0xE2, 0xFE):
            with self.subTest(slot=hex(slot)):
                self.assertEqual(E.encode_slot(slot), bytes([0x80, slot]))

        self.assertEqual(E.encode_slot(0xFF), b"\x80\xFF")
        self.assertEqual(E.encode_slot(0x100), b"\x81\x00")

    def test_translation_space_roundtrips_as_slot_zero(self):
        tokens = E.markup_to_tokens(" ", self.codetable, self.cfg["ctrl"]["efile"])
        self.assertEqual(tokens, [("char", 0)])

    def test_literal_angle_bracket_text_is_not_forced_to_control(self):
        tokens = E.markup_to_tokens(
            "<SPRIT>", self.codetable, self.cfg["ctrl"]["efile"])
        shown = D.tokens_to_text(tokens, self.codetable, self.cfg["ctrl"]["efile"])
        self.assertEqual(shown, "<SPRIT>")

    def test_markup_roundtrip_representative_files(self):
        for path in self.samples():
            with self.subTest(path=path.name):
                fmt, decoded = dump.decode_file(str(path), self.cfg)
                ctrl = self.cfg["ctrl"].get(fmt, {})
                for _, _, _, tokens in decoded:
                    markup = E.tokens_to_markup(tokens, self.codetable, ctrl)
                    rebuilt = E.markup_to_tokens(markup, self.codetable, ctrl)
                    self.assertEqual(
                        E._strip_filler(tokens, ctrl),
                        E._strip_filler(rebuilt, ctrl))

    def test_p2_style_mask_roundtrip(self):
        for path in self.samples():
            with self.subTest(path=path.name):
                fmt, decoded = dump.decode_file(str(path), self.cfg)
                ctrl = self.cfg["ctrl"].get(fmt, {})
                for _, _, _, tokens in decoded:
                    jp, masked, codes = E.tokens_to_masked(
                        tokens, self.codetable, ctrl)
                    self.assertEqual(E.restore_masked(masked, codes), jp)

    def test_patch_copy_and_container_rescan(self):
        source = ROOT / "extrac/D00/D00.BIN"
        fmt, decoded = dump.decode_file(str(source), self.cfg)
        ctrl = self.cfg["ctrl"][fmt]
        record = E._line_records(decoded)[0]
        markup = E.tokens_to_markup(record["tokens"], self.codetable, ctrl)
        with tempfile.TemporaryDirectory() as td:
            patch = Path(td) / "patch.json"
            output = Path(td) / "D00.patched.BIN"
            patch.write_text(
                '{"lines":[{"id":"%s","zh":%s}]}' %
                (record["id"], json.dumps(markup, ensure_ascii=False)),
                encoding="utf-8")
            count, out_fmt = E.apply_patch(source, patch, output)
            self.assertEqual((count, out_fmt), (1, "dfile"))
            self.assertEqual(output.read_bytes(), source.read_bytes())
            out_fmt, out_decoded = dump.decode_file(str(output), self.cfg)
            out_record = E._line_records(out_decoded)[0]
            self.assertEqual(out_fmt, "dfile")
            self.assertEqual(
                E._strip_filler(out_record["tokens"], ctrl),
                E._strip_filler(record["tokens"], ctrl))

    def test_patch_copy_e0_is_byte_exact(self):
        """E 文件原文回插不能规范化 80xx/xx 等价写法。"""
        source = ROOT / "extrac/ADV/E0.BIN"
        fmt, decoded = dump.decode_file(str(source), self.cfg)
        ctrl = self.cfg["ctrl"][fmt]
        record = E._line_records(decoded)[0]
        markup = E.tokens_to_markup(record["tokens"], self.codetable, ctrl)
        with tempfile.TemporaryDirectory() as td:
            patch = Path(td) / "patch.json"
            output = Path(td) / "E0.copy.BIN"
            patch.write_text(json.dumps({"lines": [{
                "id": record["id"], "zh": markup,
            }]}, ensure_ascii=False), encoding="utf-8")
            count, out_fmt = E.apply_patch(source, patch, output)
            self.assertEqual((count, out_fmt), (1, "efile"))
            self.assertEqual(output.read_bytes(), source.read_bytes())

    def test_real_chinese_replacement_and_rescan(self):
        """日版字库现有“中文”两字；用它们做一次真正的改字回插。"""
        source = ROOT / "extrac/D00/D00.BIN"
        fmt, decoded = dump.decode_file(str(source), self.cfg)
        ctrl = self.cfg["ctrl"][fmt]
        record = E._line_records(decoded)[0]
        reverse = {text: slot for slot, text in self.codetable.items()}
        # 只改可见字，原控制码及参数的顺序全部保留。
        new_tokens = [("char", reverse["中"]), ("char", reverse["文"])]
        new_tokens += [token for token in record["tokens"]
                       if token[0] == "ctrl" and
                       ctrl.get(token[1], (None, 2))[0] != "pad"]
        markup = E.tokens_to_markup(new_tokens, self.codetable, ctrl)

        with tempfile.TemporaryDirectory() as td:
            patch = Path(td) / "patch.json"
            output = Path(td) / "D00.chinese.BIN"
            patch.write_text(json.dumps({"lines": [{
                "id": record["id"], "zh": markup,
            }]}, ensure_ascii=False), encoding="utf-8")
            E.apply_patch(source, patch, output)
            _, out_decoded = dump.decode_file(str(output), self.cfg)
            visible = [token for token in E._line_records(out_decoded)[0]["tokens"]
                       if token[0] == "char"]
            self.assertEqual(visible[:2], new_tokens[:2])


if __name__ == "__main__":
    unittest.main()
