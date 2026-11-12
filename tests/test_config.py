# -*- coding: utf-8 -*-
import json
import unittest
from pathlib import Path

from mini60pilot.config import AppConfig, BackupStore, Profile, Rule, Settings, new_id
from tests.helpers import temp_dir


class ConfigTests(unittest.TestCase):
    def test_roundtrip(self):
        with temp_dir() as temp:
            path = Path(temp) / "config.json"
            profile = Profile(id=new_id(), name="游戏", pages={1: bytes(range(24)), 2: b"\x02\x00\x29\x00"})
            config = AppConfig(profiles=[profile], default_profile_id=profile.id)
            config.rules.append(Rule(id=new_id(), exe="cs2.exe", profile_id=profile.id))
            config.settings.wireless_sleep = 30
            config.save(path)

            loaded = AppConfig.load(path)
            self.assertEqual(len(loaded.profiles), 1)
            self.assertEqual(loaded.profiles[0].pages[1], bytes(range(24)))
            self.assertEqual(loaded.rules[0].exe, "cs2.exe")
            self.assertEqual(loaded.default_profile_id, profile.id)
            self.assertEqual(loaded.settings.wireless_sleep, 30)

    def test_unique_name(self):
        config = AppConfig(profiles=[Profile(id="1", name="游戏")])
        self.assertEqual(config.unique_name("游戏"), "游戏 (2)")
        self.assertEqual(config.unique_name("办公"), "办公")

    def test_remove_profile_cleans_rules(self):
        profile = Profile(id="p1", name="游戏")
        config = AppConfig(
            profiles=[profile],
            rules=[Rule(id="r1", exe="a.exe", profile_id="p1")],
            default_profile_id="p1",
        )
        self.assertTrue(config.remove_profile("p1"))
        self.assertEqual(config.rules, [])
        self.assertEqual(config.default_profile_id, "")

    def test_settings_sanitize(self):
        settings = Settings.from_dict(
            {"pages": [1, 9, 2], "page2_length": 447, "poll_interval": 0.01}
        )
        self.assertEqual(settings.pages, [1, 2])
        self.assertEqual(settings.page2_length, 444)
        self.assertGreaterEqual(settings.poll_interval, 0.3)

    def test_wireless_sleep_setting(self):
        self.assertEqual(Settings.from_dict({"wireless_sleep": 15}).wireless_sleep, 15)
        # 0 = 从不休眠，必须保留
        self.assertEqual(Settings.from_dict({"wireless_sleep": 0}).wireless_sleep, 0)
        # 官方驱动里不存在的值直接丢弃，避免写坏键盘
        self.assertIsNone(Settings.from_dict({"wireless_sleep": 7}).wireless_sleep)
        self.assertIsNone(Settings.from_dict({"wireless_sleep": "abc"}).wireless_sleep)
        self.assertIsNone(Settings.from_dict({}).wireless_sleep)

    def test_backup_store(self):
        with temp_dir() as temp:
            store = BackupStore(Path(temp))
            path = store.save({1: b"\x01\x02", 2: b"\x03"}, "测试")
            self.assertTrue(path.exists())
            pages = store.load(path)
            self.assertEqual(pages[1], b"\x01\x02")
            self.assertEqual(store.latest(), path)

    def test_invalid_page_hex(self):
        with self.assertRaises(Exception):
            Profile.from_dict({"name": "x", "pages": {"2": "zz"}})

    def test_unknown_fields_ignored(self):
        config = AppConfig.from_dict({"profiles": [], "rules": [], "whatever": 1})
        self.assertEqual(config.profiles, [])

    def test_corrupt_config_raises(self):
        with temp_dir() as temp:
            path = Path(temp) / "config.json"
            path.write_text("{ not json", encoding="utf-8")
            with self.assertRaises(Exception):
                AppConfig.load(path)

    def test_rule_matching_exact_and_wildcard(self):
        exact = Rule(id="r1", exe="C:\\Games\\cs2.exe", profile_id="p")
        self.assertTrue(exact.matches({"cs2.exe"}))
        self.assertFalse(exact.matches({"cs2.exe.bak"}))

        wildcard = Rule(id="r2", exe="aow_*.exe", profile_id="p")
        self.assertTrue(wildcard.matches({"aow_exe.exe", "explorer.exe"}))
        self.assertEqual(wildcard.match_names({"aow_exe.exe", "x.exe"}), ["aow_exe.exe"])
        self.assertFalse(wildcard.matches({"other.exe"}))

    def test_autostart_command_is_silent(self):
        from mini60pilot import autostart

        self.assertIn("--silent", autostart._command_line())


if __name__ == "__main__":
    unittest.main()
