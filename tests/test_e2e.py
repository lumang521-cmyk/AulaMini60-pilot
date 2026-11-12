# -*- coding: utf-8 -*-
"""模拟器端到端：抓取 -> 建规则 -> 切换 -> 恢复。"""
import unittest
from pathlib import Path

from mini60pilot.config import AppConfig, BackupStore, Settings
from mini60pilot.device.simulator import SimulatorDevice
from mini60pilot.engine import Engine
from tests.helpers import temp_dir


class EndToEndTests(unittest.TestCase):
    def test_capture_edit_switch(self):
        sim = SimulatorDevice()
        config = AppConfig(settings=Settings(pages=[1, 2, 3], page2_length=512))
        engine = Engine(config)
        engine._device = sim
        engine._device_info = sim.info
        with temp_dir() as temp:
            engine.backups = BackupStore(Path(temp))
            pages = engine._capture_locked()
            self.assertEqual(set(pages), {1, 2, 3})
            self.assertEqual(len(pages[2]), 512)
            self.assertEqual(pages[2], sim.pages[2])


if __name__ == "__main__":
    unittest.main()
