# -*- coding: utf-8 -*-
"""狼蛛 MINI60 HE（SONiX SN34F280）配置协议。

报文：64 字节 HID output report，24 字节一个分片：

    偏移  含义
    0     0xAA            电脑 -> 键盘的魔数
    1     命令字          读 = 0x10 + 页号，写 = 0x20 + 页号
    2     载荷长度        固定 0x18 (24)
    3-4   地址（小端）    页内字节偏移
    5     保留            0
    6     结束标志        最后一个分片置 1，键盘收到后提交并写入 Flash
    7     保留            0
    8-31  载荷            最多 24 字节
    32-63 补 0

键盘 -> 电脑的应答以 0x55 开头，结构相同；载荷同样从第 8 字节开始。

页号约定（与官方网页驱动 / 社区实现一致）：

    0  设备信息（握手，只读 0x10）
    1  触发行程 / Rapid Trigger       读 0x11  写 0x21
    2  按键功能表（高级键 / 改键）     读 0x12  写 0x22
    3  灯效                           读 0x13  写 0x23
"""
from __future__ import annotations

from dataclasses import dataclass

MAGIC_HOST = 0xAA
MAGIC_DEVICE = 0x55

REPORT_LEN = 64
# Aula Hub / Mini60 Pro 的 0xAA 包头占 8 字节，64 字节报文里最多带 56 字节数据。
# 页 1/3 只有 24 字节，仍然是一包；页 2 的 512 字节按 56 字节切 10 包。
CHUNK_SIZE = 56
PAYLOAD_OFFSET = 8

# 各页默认长度（字节）。页 0 只做握手，不参与读写。
DEFAULT_PAGE_LENGTHS: dict[int, int] = {
    0: 24,
    1: 24,
    2: 512,  # 128 个 4 字节记录（pageType, param1, param2, param3）
    3: 24,
}
MAX_PAGE_LENGTH = 1024


def read_command(page: int) -> int:
    if not 0 <= page <= 7:
        raise ValueError(f"页号超出范围: {page}")
    return 0x10 + page


def write_command(page: int) -> int:
    if not 0 <= page <= 7:
        raise ValueError(f"页号超出范围: {page}")
    return 0x20 + page


def build_read(
    page: int,
    address: int = 0,
    length: int = CHUNK_SIZE,
    packet_size: int = REPORT_LEN,
) -> bytes:
    if not 0 <= length <= payload_capacity(packet_size):
        raise ValueError(f"读取长度超出范围: {length}")
    return build_packet(
        read_command(page), address, b"", last=False, length=length, packet_size=packet_size
    )


def build_write(
    page: int,
    address: int,
    payload: bytes,
    *,
    last: bool = False,
    packet_size: int = REPORT_LEN,
) -> bytes:
    if len(payload) > payload_capacity(packet_size):
        raise ValueError(f"分片超过 {payload_capacity(packet_size)} 字节")
    return build_packet(
        write_command(page), address, payload, last=last, packet_size=packet_size
    )


def payload_capacity(packet_size: int = REPORT_LEN) -> int:
    """一个报文扣掉 8 字节包头后能带多少数据。"""
    return max(1, packet_size - PAYLOAD_OFFSET)


def build_packet(
    command: int,
    address: int,
    payload: bytes,
    *,
    last: bool,
    length: int | None = None,
    packet_size: int = REPORT_LEN,
) -> bytes:
    if not 0 <= address <= 0xFFFF:
        raise ValueError(f"地址超出范围: {address}")
    capacity = payload_capacity(packet_size)
    if len(payload) > capacity:
        raise ValueError(f"载荷超过 {capacity} 字节")
    if length is None:
        length = len(payload)
    packet = bytearray(packet_size)
    packet[0] = MAGIC_HOST
    packet[1] = command
    packet[2] = length & 0xFF
    packet[3] = address & 0xFF
    packet[4] = (address >> 8) & 0xFF
    packet[5] = 0
    packet[6] = 1 if last else 0
    packet[7] = 0
    packet[PAYLOAD_OFFSET : PAYLOAD_OFFSET + len(payload)] = payload
    return bytes(packet)


@dataclass(frozen=True)
class Response:
    command: int
    address: int
    payload: bytes
    last: bool
    raw: bytes

    @property
    def page(self) -> int | None:
        cmd = self.command
        if 0x10 <= cmd <= 0x1F:
            return cmd - 0x10
        if 0x20 <= cmd <= 0x2F:
            return cmd - 0x20
        return None


def parse_response(data: bytes) -> Response | None:
    """解析键盘应答；不是合法应答则返回 None。

    兼容两种读取方式：整包从第 0 字节开始就是 0x55，或前面多带一个 Report ID。
    """
    if not data:
        return None
    if data[0] != MAGIC_DEVICE:
        if len(data) > 1 and data[1] == MAGIC_DEVICE:
            data = data[1:]
        else:
            return None
    if len(data) < PAYLOAD_OFFSET:
        return None
    length = min(int(data[2]), CHUNK_SIZE, len(data) - PAYLOAD_OFFSET)
    if length < 0:
        return None
    return Response(
        command=data[1],
        address=data[3] | (data[4] << 8),
        payload=bytes(data[PAYLOAD_OFFSET : PAYLOAD_OFFSET + length]),
        last=bool(data[6] & 0x01),
        raw=bytes(data),
    )


def chunk_ranges(length: int, chunk_size: int = CHUNK_SIZE) -> list[tuple[int, int]]:
    """把 0..length 切成 (地址, 本片长度) 列表。"""
    if length < 0:
        raise ValueError("长度不能为负")
    chunk_size = max(1, chunk_size)
    return [(addr, min(chunk_size, length - addr)) for addr in range(0, length, chunk_size)]


def hexdump(data: bytes, limit: int = 32) -> str:
    text = data[:limit].hex(" ").upper()
    if len(data) > limit:
        text += f" … (+{len(data) - limit}B)"
    return text
