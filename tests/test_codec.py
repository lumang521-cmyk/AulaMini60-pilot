# -*- coding: utf-8 -*-
import unittest

from mini60pilot import codec, keycodes


class CodecTests(unittest.TestCase):
    def setUp(self):
        self.catalog = codec.demo_catalog()

    def test_validate_demo(self):
        ok, message = self.catalog.validate()
        self.assertTrue(ok, message)
        self.assertEqual(len(self.catalog), 128)

    def test_empty_table_is_valid(self):
        catalog = codec.Catalog(bytes(512))
        ok, message = catalog.validate()
        self.assertTrue(ok, message)
        self.assertIn("全部为出厂默认", message)

    def test_usage_lookup(self):
        index = self.catalog.find_usage(0x04)  # A
        self.assertEqual(index, 0x31)

    def test_encode_key_combo(self):
        slot = codec.apply_action(
            self.catalog, 0x31, codec.Action(kind="key", usage=0x06, modifiers=0x01)
        )
        self.assertEqual(slot.to_bytes(), bytes((0x02, 0x01, 0x06, 0x00)))
        self.assertEqual(codec.describe_slot(slot), "LCtrl+C")

    def test_encode_media(self):
        slot = codec.apply_action(self.catalog, 0x31, codec.Action(kind="media", usage=0x00E9))
        self.assertEqual(slot.to_bytes(), bytes((0x03, 0xE9, 0x00, 0x00)))
        self.assertIn("音量+", codec.describe_slot(slot))

    def test_encode_none(self):
        slot = codec.apply_action(self.catalog, 0x31, codec.Action(kind="none"))
        self.assertEqual(slot.to_bytes(), bytes((0x02, 0x00, 0x00, 0x00)))
        self.assertEqual(codec.describe_slot(slot), "无输出")

    def test_restore_default_requires_baseline(self):
        with self.assertRaises(ValueError):
            codec.apply_action(self.catalog, 0x31, codec.Action(kind="default"), None)

    def test_restore_default_from_baseline(self):
        baseline = codec.demo_catalog()
        codec.apply_action(self.catalog, 0x31, codec.Action(kind="key", usage=0x06))
        slot = codec.apply_action(self.catalog, 0x31, codec.Action(kind="default"), baseline)
        self.assertEqual(slot.to_bytes(), baseline.slot(0x31).to_bytes())

    def test_invalid_catalog(self):
        data = bytearray(512)
        data[0] = 0x7F  # 未知记录类型
        catalog = codec.Catalog(bytes(data))
        ok, _ = catalog.validate()
        self.assertFalse(ok)

    def test_rows(self):
        rows = self.catalog.rows()
        self.assertGreater(len(rows), 50)
        labels = [label for _, _, label in rows]
        self.assertIn("A", labels)
        self.assertIn("Esc", labels)

    def test_roundtrip_bytes(self):
        self.assertEqual(codec.Catalog(self.catalog.to_bytes()).to_bytes(), self.catalog.to_bytes())

    def test_keycodes(self):
        self.assertEqual(keycodes.usage_from_name("A"), 0x04)
        self.assertEqual(keycodes.usage_from_name("F13"), 0x68)
        self.assertEqual(keycodes.parse_combo("Ctrl+Shift+Esc"), (0x03, 0x29))
        self.assertEqual(keycodes.consumer_from_name("音量+"), 0x00E9)


if __name__ == "__main__":
    unittest.main()
