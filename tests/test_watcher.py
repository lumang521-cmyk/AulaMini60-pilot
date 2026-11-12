# -*- coding: utf-8 -*-
import time
import unittest

from mini60pilot.watcher import ActivationWatcher, ProcessWatcher


class WatcherTests(unittest.TestCase):
    def test_poll_once_calls_on_change(self):
        changes = []
        current = {"explorer.exe"}
        watcher = ProcessWatcher(lambda names: changes.append(set(names)), provider=lambda: set(current))
        watcher.poll_once()
        self.assertEqual(changes, [{"explorer.exe"}])
        watcher.poll_once()
        self.assertEqual(len(changes), 1)
        current.add("cs2.exe")
        watcher.poll_once()
        self.assertEqual(len(changes), 2)
        self.assertIn("cs2.exe", changes[-1])

    def test_start_stop(self):
        events = []
        watcher = ProcessWatcher(lambda names: events.append(names), interval=0.3, provider=lambda: {"a.exe"})
        watcher.start()
        watcher.stop()
        self.assertGreaterEqual(len(events), 1)

    def test_activation_watcher_reports_foreground_changes(self):
        changes = []
        current = {"exe": "explorer.exe"}
        watcher = ActivationWatcher(
            lambda exe: changes.append(exe), provider=lambda: current["exe"]
        )
        self.assertEqual(watcher.poll_once(), "explorer.exe")
        watcher.poll_once()
        self.assertEqual(changes, ["explorer.exe"])
        current["exe"] = "CS2.EXE"
        self.assertEqual(watcher.poll_once(), "cs2.exe")
        self.assertEqual(changes, ["explorer.exe", "cs2.exe"])
        current["exe"] = ""
        self.assertEqual(watcher.poll_once(), "")
        self.assertEqual(changes[-1], "")

    def test_wake_triggers_immediate_poll(self):
        current = {"exe": "a.exe"}
        events = []
        watcher = ActivationWatcher(
            lambda exe: events.append(exe),
            interval=10.0,
            provider=lambda: current["exe"],
        )
        watcher.start()
        try:
            time.sleep(0.1)
            current["exe"] = "b.exe"
            watcher._wake.set()  # 模拟 WinEvent 钩子收到前台变化
            deadline = time.monotonic() + 1.0
            while "b.exe" not in events and time.monotonic() < deadline:
                time.sleep(0.01)
        finally:
            watcher.stop()
        self.assertIn("b.exe", events)
        self.assertFalse(watcher._thread.is_alive())
        if watcher._hook_thread:
            self.assertFalse(watcher._hook_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
