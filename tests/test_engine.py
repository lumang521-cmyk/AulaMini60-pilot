# -*- coding: utf-8 -*-
import unittest
from pathlib import Path

from mini60pilot import codec
from mini60pilot.config import AppConfig, BackupStore, Profile, Rule, Settings, new_id
from mini60pilot.device.simulator import SimulatorDevice
from mini60pilot.engine import Engine
from tests.helpers import make_temp_dir, remove_temp_dir


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = make_temp_dir()
        self.sim = SimulatorDevice()
        self.original = self.sim.snapshot()

        self.default_profile = Profile(
            id=new_id(), name="默认", pages=self.sim.snapshot(), source="capture"
        )

        game_pages = self.sim.snapshot()
        catalog = codec.Catalog(game_pages[2])
        codec.apply_action(catalog, 0x31, codec.Action(kind="key", usage=0x05))  # A -> B
        game_pages[2] = catalog.to_bytes()
        self.game_profile = Profile(id=new_id(), name="游戏", pages=game_pages, source="edit")

        self.config = AppConfig(
            profiles=[self.default_profile, self.game_profile],
            rules=[Rule(id="r1", exe="cs2.exe", profile_id=self.game_profile.id)],
            default_profile_id=self.default_profile.id,
            settings=Settings(pages=[1, 2, 3], page2_length=512),
        )
        self.engine = Engine(self.config)
        self.engine.backups = BackupStore(Path(self.temp))
        self.engine._device = self.sim
        self.engine._device_info = self.sim.info

    def tearDown(self):
        remove_temp_dir(self.temp)

    def test_apply_and_restore(self):
        result = self.engine._apply_profile(self.game_profile, "test", force=True)
        self.assertTrue(result.applied)
        self.assertEqual(result.changed_pages, [2])
        self.assertEqual(self.sim.pages[2], self.game_profile.pages[2])
        self.assertEqual(self.sim.pages[1], self.original[1])

        self.engine._apply_profile(self.default_profile, "restore", force=True)
        self.assertEqual(self.sim.pages[2], self.original[2])

    def test_skip_when_identical(self):
        result = self.engine._apply_profile(self.default_profile, "same", force=True)
        self.assertFalse(result.applied)
        self.assertIn("未写入", result.skipped_reason)

    def test_rules_trigger_and_restore(self):
        self.engine._active_exe = "cs2.exe"
        self.engine._sync_rules()
        self.assertEqual(self.sim.pages[2], self.game_profile.pages[2])
        self.assertEqual(self.engine.status()["active_rule"], "cs2.exe")

        self.engine._active_exe = ""
        self.engine._sync_rules()
        self.assertEqual(self.sim.pages[2], self.original[2])
        self.assertEqual(self.engine.status()["active_rule"], "")

    def test_dry_run_does_not_write(self):
        self.config.settings.dry_run = True
        result = self.engine._apply_profile(self.game_profile, "dry", force=True)
        self.assertFalse(result.applied)
        self.assertEqual(self.sim.pages[2], self.original[2])

    def test_rollback_on_verification_failure(self):
        pages = self.sim.snapshot()
        catalog = codec.Catalog(pages[2])
        codec.apply_action(catalog, 0x31, codec.Action(kind="key", usage=0x05))
        pages[2] = catalog.to_bytes()
        pages[3] = bytes([1]) + pages[3][1:]
        broken = Profile(id=new_id(), name="坏方案", pages=pages)

        self.sim.fail_writes_for_pages = {3}
        with self.assertRaises(Exception):
            self.engine._apply_profile(broken, "fail", force=True)
        self.assertEqual(self.sim.pages[2], self.original[2])
        self.assertEqual(self.sim.pages[3], self.original[3])

    def test_backup_created_before_write(self):
        self.engine._apply_profile(self.game_profile, "test", force=True)
        backups = self.engine.backups.list_files()
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.engine.backups.load(backups[0])[2], self.original[2])

    def test_disabled_rule_ignored(self):
        self.config.rules[0].enabled = False
        self.engine._active_exe = "cs2.exe"
        self.engine._sync_rules()
        self.assertEqual(self.sim.pages[2], self.original[2])

    def test_wireless_sleep_written_to_page1(self):
        changed = self.engine._set_wireless_sleep_locked(15)
        self.assertTrue(changed)
        self.assertEqual(self.sim.pages[1][3], 15)
        # 只改偏移 3，其它字节保持不变
        self.assertEqual(self.sim.pages[1][:3], self.original[1][:3])
        self.assertEqual(self.sim.pages[1][4:], self.original[1][4:])

    def test_wireless_sleep_skips_when_identical(self):
        self.engine._set_wireless_sleep_locked(15)
        before = self.sim.flash_writes
        changed = self.engine._set_wireless_sleep_locked(15)
        self.assertFalse(changed)
        self.assertEqual(self.sim.flash_writes, before)

    def test_wireless_sleep_dry_run_does_not_write(self):
        self.config.settings.dry_run = True
        changed = self.engine._set_wireless_sleep_locked(15)
        self.assertFalse(changed)
        self.assertEqual(self.sim.pages[1][3], self.original[1][3])

    def test_wireless_sleep_none_is_noop(self):
        self.assertFalse(self.engine._set_wireless_sleep_locked(None))
        self.assertEqual(self.sim.pages[1], self.original[1])

    def test_apply_profile_keeps_wireless_sleep(self):
        self.config.settings.wireless_sleep = 30
        result = self.engine._apply_profile(self.game_profile, "test", force=True)
        self.assertIn(1, result.changed_pages)
        self.assertEqual(self.sim.pages[1][3], 30)
        self.assertEqual(self.game_profile.pages[1][3], self.original[1][3])

    def test_apply_profile_without_sleep_keeps_profile_page1(self):
        self.config.settings.wireless_sleep = None
        self.engine._apply_profile(self.game_profile, "test", force=True)
        self.assertEqual(self.sim.pages[1], self.game_profile.pages[1])

    def test_restore_backup(self):
        path = self.engine.backups.save(self.original, "test")
        changed = dict(self.original)
        catalog = codec.Catalog(changed[2])
        codec.apply_action(catalog, 0x31, codec.Action(kind="key", usage=0x05))
        changed[2] = catalog.to_bytes()
        self.sim.restore(changed)

        self.engine._restore_backup_locked(str(path))
        self.assertEqual(self.sim.pages[2], self.original[2])

    def test_capture_skips_unreadable_page(self):
        self.sim.fail_next_read = True  # 第 1 页读失败，其余页继续
        pages = self.engine._capture_locked()
        self.assertEqual(set(pages), {2, 3})
        self.assertEqual(len(pages[2]), 512)

    def test_connect_applies_wireless_sleep(self):
        self.config.settings.simulator = True
        self.config.settings.apply_default_on_start = False
        self.config.settings.wireless_sleep = 10
        self.engine._device = None
        self.engine._device_info = None

        self.engine._connect_locked(None)
        assert self.engine.device is not None
        self.assertEqual(self.engine.device.read_page(1, 24)[3], 10)

    def test_connect_applies_default_on_start(self):
        self.config.settings.simulator = True
        self.config.settings.apply_default_on_start = True
        catalog = codec.Catalog(self.default_profile.pages[2])
        codec.apply_action(catalog, 0x31, codec.Action(kind="key", usage=0x05))
        self.default_profile.pages[2] = catalog.to_bytes()
        self.engine._device = None
        self.engine._device_info = None

        self.engine._connect_locked(None)
        self.assertIsNotNone(self.engine.device)
        assert self.engine.device is not None
        self.assertEqual(
            self.engine.device.read_page(2, len(self.default_profile.pages[2])),
            self.default_profile.pages[2],
        )


if __name__ == "__main__":
    unittest.main()
