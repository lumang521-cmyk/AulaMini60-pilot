# -*- coding: utf-8 -*-
"""内存模拟键盘：没有硬件时也能完整演示 / 测试切换流程。"""
from __future__ import annotations

from .base import DeviceError, DeviceInfo, KeyboardDevice
from .. import codec


def _default_page1() -> bytes:
    data = bytearray(24)
    data[8] = 20  # RT 按下 0.20mm
    data[9] = 20  # RT 抬起 0.20mm
    data[16] = 0xA0  # 触发深度 160 = 1.60mm
    data[17] = 0x00
    data[19] = 0x00
    data[22] = 0x01
    data[23] = 0x01
    return bytes(data)


def _default_page3() -> bytes:
    data = bytearray(24)
    data[8] = 1
    data[9], data[10], data[11] = 0, 255, 230
    data[16] = 4
    data[17] = 3
    data[18] = 1
    return bytes(data)


class SimulatorDevice(KeyboardDevice):
    """把三页配置放在内存里的假键盘。"""

    def __init__(self, name: str = "MINI 60 HE（模拟器）") -> None:
        self.info = DeviceInfo(
            path="simulator://mini60",
            vid=0x0C45,
            pid=0x80A2,
            usage_page=0xFF68,
            usage=0x0061,
            product=name,
            manufacturer="AULA (simulated)",
            input_len=64,
            output_len=64,
            kind="simulator",
        )
        self.pages: dict[int, bytes] = {
            1: _default_page1(),
            2: codec.demo_catalog().to_bytes(),
            3: _default_page3(),
        }
        self.connected = True
        self.fail_next_write = False
        self.fail_next_read = False
        self.fail_writes_for_pages: set[int] = set()
        self.writes: list[tuple[int, bytes, bool]] = []
        self.reads: list[tuple[int, int]] = []
        self.flash_writes = 0

    @property
    def is_open(self) -> bool:
        return self.connected

    def _check(self) -> None:
        if not self.connected:
            raise DeviceError("模拟器未连接")

    def read_page(self, page: int, length: int) -> bytes:
        self._check()
        if self.fail_next_read:
            self.fail_next_read = False
            raise DeviceError("模拟读取失败")
        if page not in self.pages:
            raise DeviceError(f"模拟器没有页 {page}")
        self.reads.append((page, length))
        data = self.pages[page]
        if len(data) >= length:
            return data[:length]
        return data + bytes(length - len(data))

    def write_page(self, page: int, data: bytes, *, commit: bool = True) -> None:
        self._check()
        if self.fail_next_write or page in self.fail_writes_for_pages:
            self.fail_next_write = False
            raise DeviceError(f"模拟写入页 {page} 失败")
        if page == 0:
            raise DeviceError("页 0 只读")
        if len(data) > 640:
            raise DeviceError("模拟器拒绝超长页")
        self.pages[page] = bytes(data)
        self.writes.append((page, bytes(data), commit))
        if commit:
            self.flash_writes += 1

    def close(self) -> None:
        self.connected = False

    def handshake(self) -> dict[str, object]:
        return {"vid": self.info.vid, "pid": self.info.pid, "firmware": "V1.55 (sim)"}

    # ------------------------------------------------------------ 测试辅助
    def set_page(self, page: int, data: bytes) -> None:
        self.pages[page] = bytes(data)

    def snapshot(self) -> dict[int, bytes]:
        return {page: bytes(data) for page, data in self.pages.items()}

    def restore(self, pages: dict[int, bytes]) -> None:
        for page, data in pages.items():
            self.pages[page] = bytes(data)
