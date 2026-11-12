# -*- coding: utf-8 -*-
"""页 2「按键功能表」的编解码。

官方固件把每个按键功能存成 4 字节记录（128 个槽位，共 512 字节）：

    字节 0  类型 pageType
    字节 1  参数 1 param1
    字节 2  参数 2 param2
    字节 3  参数 3 param3

已知类型（与 Aula Hub 的 mYe 枚举一致）：

    0x00  空（保持出厂默认，不再覆盖）
    0x01  鼠标功能
    0x02  键盘按键：参数2 = HID usage
    0x03  多媒体按键：参数1+参数2 = 16 位 Consumer usage（小端）
    0x04  系统键
    0x05  额外功能
    0x06  宏
    0x07  CB
    0x08  DKS
    0x09  MT（单击/长按）
    0x0A  TGL
    0x0B  SOCD
    0x0C  RS
    0x0D  FUNC
    0x0E  END
    0x0F  MPT

页 2 是「自定义覆盖表」：某槽位全 0 表示该键使用出厂映射；只要有一个非 0
字节，就按这 4 字节覆盖。因此全 0 的表是合法的默认状态。

本模块只对「键盘按键 / 多媒体 / 无输出 / 恢复默认」做编解码，其它类型原样保留。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import keycodes

SLOT_SIZE = 4
MAX_SLOTS = 160

KIND_EMPTY = 0x00
KIND_MOUSE = 0x01
KIND_KEY = 0x02
KIND_MEDIA = 0x03
KIND_SYSTEM = 0x04
KIND_EXTRA = 0x05
KIND_MACRO = 0x06
KIND_CB = 0x07
KIND_DKS = 0x08
KIND_MT = 0x09
KIND_TGL = 0x0A
KIND_SOCD = 0x0B
KIND_RS = 0x0C
KIND_FUNC = 0x0D
KIND_END = 0x0E
KIND_MPT = 0x0F

KNOWN_KINDS = set(range(0x10))


@dataclass
class Slot:
    kind: int = 0
    p1: int = 0
    p2: int = 0
    p3: int = 0

    @classmethod
    def from_bytes(cls, data: bytes) -> "Slot":
        if len(data) != SLOT_SIZE:
            raise ValueError("槽位必须是 4 字节")
        return cls(data[0], data[1], data[2], data[3])

    def to_bytes(self) -> bytes:
        return bytes((self.kind & 0xFF, self.p1 & 0xFF, self.p2 & 0xFF, self.p3 & 0xFF))

    @property
    def usage(self) -> int:
        if self.kind == KIND_KEY:
            return self.p2
        if self.kind == KIND_MEDIA:
            return self.p1 | (self.p2 << 8)
        return 0

    @property
    def modifiers(self) -> int:
        return self.p1 if self.kind == KIND_KEY else 0


@dataclass
class Action:
    """用户可编辑的动作。"""

    kind: str = "default"  # default / key / media / none / raw
    usage: int = 0
    modifiers: int = 0
    raw: bytes = b"\x00\x00\x00\x00"

    def describe(self) -> str:
        if self.kind == "default":
            return "恢复出厂"
        if self.kind == "none":
            return "无输出"
        if self.kind == "key":
            return f"{keycodes.format_modifiers(self.modifiers)}{keycodes.hid_name(self.usage)}"
        if self.kind == "media":
            return f"多媒体：{keycodes.consumer_name(self.usage)}"
        if self.kind == "raw":
            return f"原始：{self.raw.hex(' ').upper()}"
        return self.kind


def describe_slot(slot: Slot) -> str:
    if slot.kind == KIND_EMPTY:
        return "出厂默认"
    if slot.kind == KIND_KEY:
        if slot.modifiers == 0 and slot.p2 == 0:
            return "无输出"
        return f"{keycodes.format_modifiers(slot.modifiers)}{keycodes.hid_name(slot.p2)}"
    if slot.kind == KIND_MEDIA:
        return f"多媒体：{keycodes.consumer_name(slot.usage)}"
    if slot.kind == KIND_MOUSE:
        return "鼠标功能（保留）"
    if slot.kind == KIND_MACRO:
        return "宏（保留）"
    if slot.kind == KIND_MT:
        return "单击/长按（保留）"
    if slot.kind == KIND_TGL:
        return "TGL（保留）"
    if slot.kind == KIND_SOCD:
        return "SOCD（保留）"
    if slot.kind == KIND_FUNC:
        return "功能键（保留）"
    return f"高级键类型 0x{slot.kind:02X}（保留）"


class Catalog:
    """一整页按键功能表（512 字节 / 128 槽位）。"""

    def __init__(self, data: bytes):
        if len(data) % SLOT_SIZE:
            raise ValueError("按键表长度必须是 4 的倍数")
        self.data = bytes(data)
        self.slots = [
            Slot.from_bytes(self.data[i : i + SLOT_SIZE])
            for i in range(0, len(self.data), SLOT_SIZE)
        ]

    def __len__(self) -> int:
        return len(self.slots)

    def slot(self, index: int) -> Slot:
        return self.slots[index]

    def to_bytes(self) -> bytes:
        return b"".join(slot.to_bytes() for slot in self.slots)

    def set_slot(self, index: int, slot: Slot) -> None:
        self.slots[index] = slot

    # ------------------------------------------------------------ 校验
    def validate(self) -> tuple[bool, str]:
        """结构自检：全 0 表示全部使用出厂映射，是合法状态。"""
        if len(self.data) % SLOT_SIZE:
            return False, "长度不是 4 的倍数"
        if not 40 <= len(self.slots) <= MAX_SLOTS:
            return False, f"槽位数量异常：{len(self.slots)}"
        unknown = [s.kind for s in self.slots if s.kind not in KNOWN_KINDS]
        if unknown:
            return False, f"存在未知记录类型：{sorted(set(unknown))}"
        custom = [s for s in self.slots if s.kind != KIND_EMPTY]
        if not custom:
            return True, f"{len(self.slots)} 个槽位，全部为出厂默认（没有自定义改键）"
        return True, f"{len(self.slots)} 个槽位，{len(custom)} 个自定义覆盖"

    # ------------------------------------------------------------ 展示
    def rows(self) -> list[tuple[int, Slot, str]]:
        """返回可编辑的行：(槽位下标, 槽位, 显示名)。"""
        seen: dict[str, int] = {}
        rows: list[tuple[int, Slot, str]] = []
        for index, slot in enumerate(self.slots):
            if slot.kind not in (KIND_KEY, KIND_MEDIA):
                continue
            if slot.kind == KIND_KEY and slot.usage == 0:
                continue
            base = describe_slot(slot).replace("多媒体：", "")
            count = seen.get(base, 0) + 1
            seen[base] = count
            label = base if count == 1 else f"{base} #{count}"
            rows.append((index, slot, label))
        return rows

    def find_usage(self, usage: int) -> int | None:
        for index, slot in enumerate(self.slots):
            if slot.kind == KIND_KEY and slot.p2 == usage:
                return index
        return None


def apply_action(
    catalog: Catalog, index: int, action: Action, baseline: Catalog | None = None
) -> Slot:
    """把动作写进槽位，返回写入后的槽位。"""
    if not 0 <= index < len(catalog):
        raise IndexError(f"槽位越界：{index}")

    if action.kind == "key":
        slot = Slot(KIND_KEY, action.modifiers & 0xFF, action.usage & 0xFF, 0)
    elif action.kind == "media":
        slot = Slot(KIND_MEDIA, action.usage & 0xFF, (action.usage >> 8) & 0xFF, 0)
    elif action.kind == "none":
        slot = Slot(KIND_KEY, 0, 0, 0)
    elif action.kind == "default":
        if baseline is None:
            raise ValueError("没有出厂基线，无法“恢复出厂”；请先抓取一份默认方案")
        if index < len(baseline):
            slot = baseline.slot(index)
        else:
            slot = Slot(0, 0, 0, 0)
    elif action.kind == "raw":
        slot = Slot.from_bytes(action.raw)
    else:
        raise ValueError(f"未知动作类型：{action.kind}")

    catalog.set_slot(index, slot)
    return slot


def demo_catalog() -> Catalog:
    """给模拟器用的、看起来像真键盘的按键表。"""
    usages: dict[int, int] = {
        0x00: 0x29,
        0x10: 0x35,
        0x1B: 0x2D,
        0x1C: 0x2E,
        0x20: 0x2B,
        0x2B: 0x2F,
        0x2C: 0x30,
        0x30: 0x39,
        0x3A: 0x33,
        0x3B: 0x34,
        0x3C: 0x28,
        0x40: 0xE1,
        0x48: 0x36,
        0x49: 0x37,
        0x4A: 0x38,
        0x4B: 0xE5,
        0x4C: 0x2A,
        0x50: 0xE0,
        0x51: 0xE3,
        0x52: 0xE2,
        0x53: 0x2C,
        0x54: 0xE6,
        0x55: 0xAF,
        0x56: 0x65,
        0x57: 0xE4,
        0x5C: 0x31,
    }
    for i in range(12):
        usages[0x01 + i] = 0x3A + i
    for i in range(10):
        usages[0x11 + i] = 0x1E + i
    for i in range(10):
        usages[0x21 + i] = (0x14, 0x1A, 0x08, 0x15, 0x17, 0x1C, 0x18, 0x0C, 0x12, 0x13)[i]
    for i in range(9):
        usages[0x31 + i] = (0x04, 0x16, 0x07, 0x09, 0x0A, 0x0B, 0x0D, 0x0E, 0x0F)[i]
    for i in range(7):
        usages[0x41 + i] = (0x1D, 0x1B, 0x06, 0x19, 0x05, 0x11, 0x10)[i]

    data = bytearray(128 * SLOT_SIZE)
    for index, usage in usages.items():
        if index < 128:
            data[index * SLOT_SIZE : index * SLOT_SIZE + SLOT_SIZE] = bytes(
                (KIND_KEY, 0, usage, 0)
            )
    return Catalog(bytes(data))
