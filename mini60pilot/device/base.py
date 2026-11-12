# -*- coding: utf-8 -*-
"""设备抽象层。"""
from __future__ import annotations

from dataclasses import dataclass


class DeviceError(Exception):
    """设备通信失败。"""


class DeviceNotFound(DeviceError):
    """没有找到键盘。"""


@dataclass
class DeviceInfo:
    path: str
    vid: int
    pid: int
    usage_page: int
    usage: int
    product: str = ""
    manufacturer: str = ""
    input_len: int = 64
    output_len: int = 64
    kind: str = "hid"  # hid / simulator

    def label(self) -> str:
        product = self.product or "HID 设备"
        return (
            f"{product}  VID:{self.vid:04X} PID:{self.pid:04X} "
            f"UP:{self.usage_page:04X} U:{self.usage:02X}"
        )


class KeyboardDevice:
    """键盘读写接口。页号与协议层的页号一致。"""

    info: DeviceInfo

    @property
    def is_open(self) -> bool:  # pragma: no cover - 接口
        raise NotImplementedError

    def read_page(self, page: int, length: int) -> bytes:  # pragma: no cover - 接口
        raise NotImplementedError

    def write_page(self, page: int, data: bytes, *, commit: bool = True) -> None:  # pragma: no cover
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - 接口
        raise NotImplementedError

    def handshake(self) -> dict[str, object]:
        return {}

    # ------------------------------------------------------------ 便捷方法
    def read_pages(self, lengths: dict[int, int]) -> dict[int, bytes]:
        result: dict[int, bytes] = {}
        for page, length in lengths.items():
            result[page] = self.read_page(page, length)
        return result

    def verify_page(self, page: int, expected: bytes) -> bool:
        actual = self.read_page(page, len(expected))
        return actual[: len(expected)] == expected[: len(expected)]
