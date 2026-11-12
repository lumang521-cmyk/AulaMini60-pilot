# -*- coding: utf-8 -*-
"""规则引擎：前台程序变化 -> 切换键盘方案。

所有 HID 读写都在这一个工作线程里完成，UI 只投递任务，避免并发写键盘。
"""
from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import device as device_mod
from . import protocol
from .config import AppConfig, BackupStore, Profile, WIRELESS_SLEEP_OPTIONS
from .device.base import DeviceError, DeviceInfo, DeviceNotFound, KeyboardDevice
from .watcher import ActivationWatcher

# 页 1 = 官方驱动的 GET_GAME_MODE / SET_GAME_MODE 配置块，
# 偏移 3 是无线休眠时间（单位分钟，0 = 从不休眠）。
WIRELESS_SLEEP_PAGE = 1
WIRELESS_SLEEP_OFFSET = 3
WIRELESS_SLEEP_LENGTH = protocol.DEFAULT_PAGE_LENGTHS.get(WIRELESS_SLEEP_PAGE, 24)


@dataclass
class ApplyResult:
    applied: bool
    profile: str
    changed_pages: list[int]
    skipped_reason: str = ""
    backed_up_to: str = ""


class Engine:
    def __init__(self, config: AppConfig, on_log: Callable[[str, str], None] | None = None):
        self.config = config
        self.on_log = on_log or (lambda level, text: None)
        self.on_status: Callable[[dict[str, Any]], None] | None = None
        self.backups = BackupStore()
        self._queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="mini60-engine", daemon=True)
        self._running = False
        self._stopping = False
        self._device: KeyboardDevice | None = None
        self._device_info: DeviceInfo | None = None
        self._active_exe: str = ""
        self._state_lock = threading.RLock()
        self._active_rule_id: str = ""
        self._applied_profile_id: str = ""
        self._last_error: str = ""
        self._attempt_at: dict[str, float] = {}
        self._sync_pending = False
        self._watcher: ActivationWatcher | None = None
        self._stats = {"writes": 0, "flash": 0}

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stopping = False
        self._worker.start()
        self._start_watcher()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._stopping = True
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
        self._queue.put(None)
        self._worker.join(timeout=3)
        if self._worker.is_alive():
            # 还有 HID 任务没结束，宁可留给进程退出，也不要在它用句柄时关闭设备
            return
        self._close_device()

    def _start_watcher(self) -> None:
        if self._watcher:
            self._watcher.stop()
        self._watcher = ActivationWatcher(
            self._on_activation,
            interval=self.config.settings.poll_interval,
            on_tick=self._schedule_sync,
        )
        self._watcher.start()

    def apply_settings(self, config: AppConfig) -> None:
        """UI 修改配置后调用；内部换成快照，避免线程间读写冲突。"""
        self.config = config
        # 配置变了就重新评估规则：这样改完方案/规则、关掉演练模式后能立即生效
        self._active_rule_id = ""
        self._attempt_at.clear()
        if self._watcher:
            self._watcher.update_interval(config.settings.poll_interval)
        if self._running:
            self._schedule_sync()

    def _on_activation(self, exe: str) -> None:
        with self._state_lock:
            self._active_exe = exe
        self._schedule_sync()

    def _schedule_sync(self) -> None:
        with self._state_lock:
            if self._sync_pending:
                return
            self._sync_pending = True
        self.submit(self._sync_after_change)

    def _sync_after_change(self) -> None:
        try:
            self._sync_rules()
        finally:
            with self._state_lock:
                self._sync_pending = False

    # ------------------------------------------------------------ 任务队列
    def submit(self, fn: Callable[[], Any]) -> Future:
        future: Future = Future()
        # 标记异常已读取，避免 UI 不关心结果时 Python 打印 "exception never retrieved"
        future.add_done_callback(lambda f: None if f.cancelled() else f.exception())
        self._queue.put((fn, future))
        return future

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            fn, future = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except Exception as exc:  # noqa: BLE001 - 统一上报给 UI
                self._last_error = str(exc)
                self._log("ERROR", str(exc))
                future.set_exception(exc)
                self._emit_status()

    # ------------------------------------------------------------ 设备
    @property
    def device(self) -> KeyboardDevice | None:
        return self._device

    def connect(self, info: DeviceInfo | None = None) -> Future:
        return self.submit(lambda: self._connect_locked(info))

    def _connect_locked(self, info: DeviceInfo | None = None, *, apply_default: bool = True) -> DeviceInfo:
        self._close_device()
        settings = self.config.settings
        if info is None:
            if settings.simulator:
                info = device_mod.discover(settings)[0]
            else:
                candidates = device_mod.discover(settings)
                if not candidates:
                    raise DeviceNotFound(
                        "没有找到狼蛛 MINI60 HE。请确认键盘用数据线直连、"
                        "官方驱动/网页驱动已关闭，然后点“刷新设备”。"
                    )
                info = candidates[0]
        device = device_mod.open_device(info)
        self._device = device
        self._device_info = info
        details = {}
        try:
            details = device.handshake()
        except Exception:
            details = {}
        firmware = str(details.get("firmware") or info.product or "")
        self._log(
            "INFO",
            f"已连接：{info.label()}  固件 {firmware or '未知'}  输出报文 {info.output_len}B",
        )
        self._emit_status()
        if apply_default and settings.apply_default_on_start:
            default = self.config.default_profile
            if default is not None:
                try:
                    self._apply_profile(default, "启动时应用默认方案", force=False)
                except Exception as exc:  # noqa: BLE001
                    self._log("WARN", f"启动时应用默认方案失败：{exc}")
        if apply_default and settings.wireless_sleep is not None:
            try:
                self._set_wireless_sleep_locked(settings.wireless_sleep)
            except Exception as exc:  # noqa: BLE001
                self._log("WARN", f"写入无线休眠失败：{exc}")
        return info

    def _ensure_device(self) -> KeyboardDevice:
        if self._device and self._device.is_open:
            return self._device
        self._connect_locked(None, apply_default=False)
        assert self._device is not None
        return self._device

    def _close_device(self) -> None:
        if self._device:
            try:
                self._device.close()
            except Exception:
                pass
            self._device = None
            self._device_info = None
            self._emit_status()

    def disconnect(self) -> Future:
        return self.submit(self._close_device)

    def discover(self) -> Future:
        return self.submit(lambda: device_mod.discover(self.config.settings))

    # ------------------------------------------------------------ 规则
    def _sync_rules(self) -> None:
        if self._stopping:
            return
        with self._state_lock:
            active_exe = self._active_exe
        rule, profile = self._matching_rule(active_exe)
        if rule is None:
            if not self._active_rule_id:
                return
            previous = self._active_rule_id
            if self.config.settings.restore_on_exit:
                default = self.config.default_profile
                if default:
                    try:
                        self._apply_profile(default, "切走后恢复默认", force=False)
                    except Exception as exc:  # noqa: BLE001
                        self._log("WARN", f"恢复默认方案失败，稍后重试：{exc}")
                        self._emit_status()
                        return
            self._active_rule_id = ""
            self._log("INFO", f"程序已不在前台（规则 {self._rule_label(previous)}）")
            self._emit_status()
            return

        if rule.id == self._active_rule_id:
            return
        now = time.monotonic()
        if now < self._attempt_at.get(rule.id, 0):
            return
        self._attempt_at[rule.id] = now + self.config.settings.retry_seconds
        self._log("INFO", f"检测到程序激活：{rule.exe}，准备切换到「{profile.name}」")
        try:
            self._apply_profile(profile, f"激活程序 {rule.exe}", force=False)
        except Exception as exc:  # noqa: BLE001
            self._log("ERROR", f"切换到方案「{profile.name}」失败：{exc}")
            self._emit_status()
            return
        self._attempt_at.pop(rule.id, None)
        self._active_rule_id = rule.id
        self._emit_status()

    def _matching_rule(self, active_exe: str) -> tuple[Any | None, Profile | None]:
        if not active_exe:
            return None, None
        names = {active_exe}
        for rule in self.config.rules:
            if not rule.enabled:
                continue
            if not rule.matches(names):
                continue
            profile = self.config.profile_by_id(rule.profile_id)
            if profile is not None:
                return rule, profile
        return None, None

    def _rule_label(self, rule_id: str) -> str:
        for rule in self.config.rules:
            if rule.id == rule_id:
                return rule.exe
        return rule_id

    # ------------------------------------------------------------ 无线休眠
    def set_wireless_sleep(self, minutes: int | None) -> Future:
        """把无线休眠时间写入键盘（页 1 偏移 3，单位分钟，0 = 从不）。"""
        return self.submit(lambda: self._set_wireless_sleep_locked(minutes))

    def _set_wireless_sleep_locked(self, minutes: int | None) -> bool:
        if minutes is None:
            return False
        settings = self.config.settings
        label = WIRELESS_SLEEP_OPTIONS.get(minutes, f"{minutes} 分钟")
        device = self._ensure_device()
        data = bytearray(device.read_page(WIRELESS_SLEEP_PAGE, WIRELESS_SLEEP_LENGTH))
        if len(data) <= WIRELESS_SLEEP_OFFSET:
            raise DeviceError("键盘返回的页 1 数据太短，无法写入无线休眠")
        if data[WIRELESS_SLEEP_OFFSET] == minutes & 0xFF:
            self._log("INFO", f"无线休眠已经是「{label}」，跳过写入")
            return False
        if settings.dry_run:
            self._log("INFO", f"[演练] 无线休眠将设为「{label}」，未写入")
            return False
        if settings.backup_before_write:
            path = self.backups.save({WIRELESS_SLEEP_PAGE: bytes(data)}, "设置无线休眠")
            self._log("INFO", f"已备份当前页 1：{path}")
        data[WIRELESS_SLEEP_OFFSET] = minutes & 0xFF
        payload = bytes(data)
        device.write_page(WIRELESS_SLEEP_PAGE, payload, commit=True)
        self._stats["writes"] += 1
        self._stats["flash"] += 1
        if settings.verify_after_write and not device.verify_page(WIRELESS_SLEEP_PAGE, payload):
            raise DeviceError("无线休眠写入校验失败")
        self._log("INFO", f"无线休眠已设为「{label}」")
        self._emit_status()
        return True

    def _pages_with_sleep(self, profile: Profile) -> dict[int, bytes]:
        """应用方案时把全局无线休眠值补进页 1，避免被方案里的旧值覆盖。"""
        pages = dict(profile.pages)
        data = pages.get(WIRELESS_SLEEP_PAGE)
        minutes = self.config.settings.wireless_sleep
        if minutes is None or data is None or len(data) <= WIRELESS_SLEEP_OFFSET:
            return pages
        patched = bytearray(data)
        patched[WIRELESS_SLEEP_OFFSET] = minutes & 0xFF
        pages[WIRELESS_SLEEP_PAGE] = bytes(patched)
        return pages

    # ------------------------------------------------------------ 应用方案
    def apply_profile(self, profile_id: str, reason: str = "手动") -> Future:
        return self.submit(lambda: self._apply_profile_by_id(profile_id, reason))

    def _apply_profile_by_id(self, profile_id: str, reason: str) -> ApplyResult:
        profile = self.config.profile_by_id(profile_id)
        if profile is None:
            raise ValueError("方案不存在")
        return self._apply_profile(profile, reason, force=True)

    def apply_default(self, reason: str = "手动恢复默认") -> Future:
        profile = self.config.default_profile
        if profile is None:
            raise ValueError("还没有默认方案")
        return self.submit(lambda: self._apply_profile(profile, reason, force=True))

    def _page_lengths(self, profile: Profile) -> dict[int, int]:
        settings = self.config.settings
        lengths: dict[int, int] = {}
        for page, data in profile.pages.items():
            if page not in settings.pages:
                continue
            lengths[page] = len(data)
        return lengths

    def _apply_profile(self, profile: Profile, reason: str, *, force: bool) -> ApplyResult:
        started = time.monotonic()
        settings = self.config.settings
        lengths = self._page_lengths(profile)
        if not lengths:
            raise DeviceError("这个方案没有可写入的页面，请先重新抓取")
        target = self._pages_with_sleep(profile)

        device = self._ensure_device()
        current = {
            page: device.read_page(page, length) for page, length in lengths.items()
        }
        changed = {
            page: target[page]
            for page in lengths
            if current[page][: len(target[page])] != target[page]
        }

        if not changed:
            self._applied_profile_id = profile.id
            self._log("INFO", f"「{profile.name}」与键盘当前配置一致，跳过写入")
            self._emit_status()
            return ApplyResult(False, profile.name, [], "内容相同，未写入")

        if settings.dry_run:
            self._log("INFO", f"[演练] 将切换到「{profile.name}」，改动页 {sorted(changed)}，未写入")
            self._emit_status()
            return ApplyResult(False, profile.name, sorted(changed), "演练模式")

        backup_path = ""
        if settings.backup_before_write:
            # current 已经是写入前读到的整页内容，直接用它备份，别再读一遍键盘
            backup_path = str(self.backups.save(dict(current), reason))
            self._log("INFO", f"已备份当前配置：{backup_path}")

        written: list[int] = []
        try:
            for page in sorted(changed):
                device.write_page(page, changed[page], commit=True)
                written.append(page)
                self._stats["writes"] += 1
                self._stats["flash"] += 1
            if settings.verify_after_write:
                for page in written:
                    if not device.verify_page(page, changed[page]):
                        raise DeviceError(f"第 {page} 页写入校验失败")
            self._applied_profile_id = profile.id
            self._last_error = ""
            elapsed = time.monotonic() - started
            self._log(
                "INFO",
                f"已切换到「{profile.name}」（{reason}，耗时 {elapsed:.1f}s），改动页 {written}"
                + (f"，备份 {backup_path}" if backup_path else ""),
            )
            self._emit_status()
            return ApplyResult(True, profile.name, written, "", backup_path)
        except Exception:
            # 回滚已经写过的页面
            for page in written:
                try:
                    device.write_page(page, current[page], commit=True)
                    self._log("WARN", f"已回滚第 {page} 页")
                except Exception as rollback_error:  # noqa: BLE001
                    self._log("ERROR", f"回滚第 {page} 页失败：{rollback_error}")
            raise

    # ------------------------------------------------------------ 抓取 / 备份
    def capture(self) -> Future:
        return self.submit(self._capture_locked)

    def _read_pages_safely(self, lengths: dict[int, int]) -> dict[int, bytes]:
        """逐页读取；某一页协议不兼容时跳过，不影响其它页（例如只切换灯效/RT）。"""
        device = self._ensure_device()
        pages: dict[int, bytes] = {}
        failed: list[int] = []
        for page, length in lengths.items():
            try:
                pages[page] = device.read_page(page, length)
            except Exception as exc:  # noqa: BLE001
                failed.append(page)
                self._log("WARN", f"读取第 {page} 页失败，已跳过：{exc}")
        if not pages:
            raise DeviceError("没有读到任何配置页，请确认键盘型号/协议是否匹配")
        if failed:
            self._log("WARN", f"本次跳过的页：{failed}；方案里将不包含这些页")
        return pages

    def _capture_locked(self) -> dict[int, bytes]:
        settings = self.config.settings
        lengths = {}
        for page in settings.pages:
            lengths[page] = settings.page2_length if page == 2 else protocol.DEFAULT_PAGE_LENGTHS.get(page, 24)
        pages = self._read_pages_safely(lengths)
        self._log("INFO", f"已读取键盘配置：" + "，".join(f"页{p} {len(d)}B" for p, d in pages.items()))
        return pages

    def backup_now(self, reason: str = "手动备份") -> Future:
        return self.submit(lambda: self._backup_locked(reason))

    def _backup_locked(self, reason: str) -> str:
        settings = self.config.settings
        lengths = {
            page: (settings.page2_length if page == 2 else protocol.DEFAULT_PAGE_LENGTHS.get(page, 24))
            for page in settings.pages
        }
        pages = self._read_pages_safely(lengths)
        path = self.backups.save(pages, reason)
        self._log("INFO", f"已备份配置：{path}")
        return str(path)

    def restore_backup(self, path: str) -> Future:
        return self.submit(lambda: self._restore_backup_locked(path))

    def _restore_backup_locked(self, path: str) -> dict[int, bytes]:
        pages = self.backups.load(path)
        if not pages:
            raise ValueError("备份文件里没有页面数据")
        profile = Profile(
            id="__backup__", name=Path(path).name, pages=pages, source="import", note="备份恢复"
        )
        self._apply_profile(profile, f"恢复备份 {Path(path).name}", force=True)
        self._log("INFO", f"已恢复备份：{path}")
        return pages

    def read_page(self, page: int, length: int) -> Future:
        return self.submit(lambda: self._ensure_device().read_page(page, length))

    def write_page(self, page: int, data: bytes) -> Future:
        return self.submit(lambda: self._ensure_device().write_page(page, data, commit=True))

    # ------------------------------------------------------------ 状态
    def status(self) -> dict[str, Any]:
        settings = self.config.settings
        applied = self.config.profile_by_id(self._applied_profile_id)
        rule_label = ""
        if self._active_rule_id:
            rule_label = self._rule_label(self._active_rule_id)
        return {
            "connected": bool(self._device and self._device.is_open),
            "device": self._device_info.label() if self._device_info else "",
            "simulator": bool(self._device_info and self._device_info.kind == "simulator"),
            "applied_profile": applied.name if applied else "—",
            "active_program": self._active_exe,
            "active_rule": rule_label,
            "dry_run": settings.dry_run,
            "last_error": self._last_error,
            "writes": self._stats["writes"],
            "flash": self._stats["flash"],
        }

    def _emit_status(self) -> None:
        callback = self.on_status
        if callback:
            try:
                callback(self.status())
            except Exception:
                pass

    def _log(self, level: str, text: str) -> None:
        try:
            self.on_log(level, text)
        except Exception:
            pass
