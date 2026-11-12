# -*- coding: utf-8 -*-
"""设备发现与创建。"""
from __future__ import annotations

from ..config import Settings
from .base import DeviceError, DeviceInfo, DeviceNotFound, KeyboardDevice
from .simulator import SimulatorDevice
from . import hidwin


def discover(settings: Settings) -> list[DeviceInfo]:
    if settings.simulator:
        return [SimulatorDevice().info]
    try:
        candidates = hidwin.list_devices(settings.device_vid)
    except Exception:
        return []

    # MINI 60 HE 系列：有线 0x80A2 / 无线接收器 0xFEFE，厂商集合 0xFF68（部分固件 0xFF60）
    known_pids = {0x80A2, 0xFEFE, 0x80A1}
    usage_pages = {settings.device_usage_page, 0xFF68, 0xFF60}

    def score(info: DeviceInfo) -> int:
        value = 0
        name = (info.product or "").upper().replace(" ", "")
        if info.usage_page == settings.device_usage_page:
            value += 6
        if info.usage_page == 0xFF68:
            value += 3
        elif info.usage_page == 0xFF60:
            value += 1
        if info.pid in known_pids:
            value += 4
        if "MINI60" in name:
            value += 8
        if "AULA" in name or "狼蛛" in (info.product or ""):
            value += 1
        return value

    primary = [
        info
        for info in candidates
        if info.usage_page in usage_pages
        and (settings.device_pid is None or info.pid == settings.device_pid)
    ]
    if primary:
        preferred = [info for info in primary if info.usage == settings.device_usage]
        pool = preferred or primary
        return sorted(pool, key=score, reverse=True)
    if settings.device_pid is not None:
        return [info for info in candidates if info.pid == settings.device_pid]
    return sorted(candidates, key=score, reverse=True)


def open_device(info: DeviceInfo) -> KeyboardDevice:
    if info.kind == "simulator":
        return SimulatorDevice(info.product)
    if not hidwin.IS_WINDOWS:
        raise DeviceNotFound("当前系统不支持 HID 直连")
    return hidwin.WindowsHidDevice(info)
