# -*- coding: utf-8 -*-
"""Windows HID 传输层（纯 ctypes，无第三方依赖）。

只负责“把 64 字节报文发下去、把 0x55 开头的应答收上来”，协议分片在
protocol.py，页面语义在 engine.py。

键盘的厂商集合：VID 0x0C45，Usage Page 0xFF68，Usage 0x0061。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes as w

from .base import DeviceError, DeviceInfo, DeviceNotFound, KeyboardDevice
from .. import protocol

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    _hid = ctypes.WinDLL("hid", use_last_error=True)

    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_FLAG_OVERLAPPED = 0x40000000
    DIGCF_PRESENT = 0x02
    DIGCF_DEVICEINTERFACE = 0x10
    ERROR_IO_PENDING = 997
    ERROR_NO_MORE_ITEMS = 259
    WAIT_OBJECT_0 = 0
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", w.DWORD),
            ("Data2", w.WORD),
            ("Data3", w.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", w.DWORD),
            ("InterfaceClassGuid", GUID),
            ("Flags", w.DWORD),
            ("Reserved", ctypes.c_size_t),
        ]

    class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
        _fields_ = [("cbSize", w.DWORD), ("DevicePath", w.WCHAR * 1)]

    class HIDD_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("Size", w.ULONG),
            ("VendorID", w.USHORT),
            ("ProductID", w.USHORT),
            ("VersionNumber", w.USHORT),
        ]

    class HIDP_CAPS(ctypes.Structure):
        _fields_ = [
            ("Usage", w.USHORT),
            ("UsagePage", w.USHORT),
            ("InputReportByteLength", w.USHORT),
            ("OutputReportByteLength", w.USHORT),
            ("FeatureReportByteLength", w.USHORT),
            ("Reserved", w.USHORT * 17),
            ("NumberLinkCollectionNodes", w.USHORT),
            ("NumberInputButtonCaps", w.USHORT),
            ("NumberInputValueCaps", w.USHORT),
            ("NumberInputDataIndices", w.USHORT),
            ("NumberOutputButtonCaps", w.USHORT),
            ("NumberOutputValueCaps", w.USHORT),
            ("NumberOutputDataIndices", w.USHORT),
            ("NumberFeatureButtonCaps", w.USHORT),
            ("NumberFeatureValueCaps", w.USHORT),
            ("NumberFeatureDataIndices", w.USHORT),
        ]

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", w.DWORD),
            ("OffsetHigh", w.DWORD),
            ("hEvent", w.HANDLE),
        ]

    _kernel32.CreateFileW.restype = w.HANDLE
    _kernel32.CreateFileW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        w.DWORD,
        ctypes.c_void_p,
        w.DWORD,
        w.DWORD,
        w.HANDLE,
    ]
    _kernel32.CloseHandle.argtypes = [w.HANDLE]
    _kernel32.CloseHandle.restype = w.BOOL
    _kernel32.CreateEventW.restype = w.HANDLE
    _kernel32.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
    _kernel32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    _kernel32.WaitForSingleObject.restype = w.DWORD
    _kernel32.ResetEvent.argtypes = [w.HANDLE]
    _kernel32.CancelIoEx.argtypes = [w.HANDLE, ctypes.POINTER(OVERLAPPED)]
    _kernel32.GetOverlappedResult.argtypes = [
        w.HANDLE,
        ctypes.POINTER(OVERLAPPED),
        ctypes.POINTER(w.DWORD),
        w.BOOL,
    ]
    _kernel32.ReadFile.argtypes = [
        w.HANDLE,
        ctypes.c_void_p,
        w.DWORD,
        ctypes.POINTER(w.DWORD),
        ctypes.POINTER(OVERLAPPED),
    ]
    _kernel32.WriteFile.argtypes = [
        w.HANDLE,
        ctypes.c_void_p,
        w.DWORD,
        ctypes.POINTER(w.DWORD),
        ctypes.POINTER(OVERLAPPED),
    ]

    _setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    _setupapi.SetupDiGetClassDevsW.argtypes = [
        ctypes.POINTER(GUID),
        w.LPCWSTR,
        ctypes.c_void_p,
        w.DWORD,
    ]
    _setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(GUID),
        w.DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ]
    _setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.c_void_p,
        w.DWORD,
        ctypes.POINTER(w.DWORD),
        ctypes.c_void_p,
    ]
    _setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

    _hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
    _hid.HidD_GetAttributes.argtypes = [w.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
    _hid.HidD_GetPreparsedData.argtypes = [w.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    _hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    _hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    _hid.HidD_GetProductString.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
    _hid.HidD_GetManufacturerString.argtypes = [w.HANDLE, ctypes.c_void_p, w.ULONG]
    _hid.HidD_SetNumInputBuffers.argtypes = [w.HANDLE, w.ULONG]


def _wide_string(buffer: ctypes.Array) -> str:
    try:
        return buffer.value  # type: ignore[attr-defined]
    except Exception:
        return ""


def _query_caps(path: str) -> DeviceInfo | None:
    handle = _kernel32.CreateFileW(
        path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
    )
    if handle == INVALID_HANDLE_VALUE or not handle:
        handle = _kernel32.CreateFileW(
            path,
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
    if handle == INVALID_HANDLE_VALUE or not handle:
        return None
    try:
        attributes = HIDD_ATTRIBUTES()
        attributes.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if not _hid.HidD_GetAttributes(handle, ctypes.byref(attributes)):
            return None

        product = ctypes.create_unicode_buffer(256)
        _hid.HidD_GetProductString(handle, product, ctypes.sizeof(product))
        manufacturer = ctypes.create_unicode_buffer(256)
        _hid.HidD_GetManufacturerString(handle, manufacturer, ctypes.sizeof(manufacturer))

        preparsed = ctypes.c_void_p()
        if not _hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
            return None
        try:
            caps = HIDP_CAPS()
            if _hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) != 0x00110000:  # HIDP_STATUS_SUCCESS
                # 不同 NTSTATUS 宏的实际值依赖实现，失败再兜底为 0 判断
                if caps.UsagePage == 0:
                    return None
        finally:
            _hid.HidD_FreePreparsedData(preparsed)

        return DeviceInfo(
            path=path,
            vid=attributes.VendorID,
            pid=attributes.ProductID,
            usage_page=caps.UsagePage,
            usage=caps.Usage,
            product=_wide_string(product),
            manufacturer=_wide_string(manufacturer),
            input_len=caps.InputReportByteLength,
            output_len=caps.OutputReportByteLength,
        )
    finally:
        _kernel32.CloseHandle(handle)


def list_devices(vid: int | None = None) -> list[DeviceInfo]:
    """枚举 HID 设备；vid 为空时返回所有能打开的厂商集合。"""
    if not IS_WINDOWS:
        return []
    guid = GUID()
    _hid.HidD_GetHidGuid(ctypes.byref(guid))
    info_set = _setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if not info_set:
        return []
    devices: list[DeviceInfo] = []
    try:
        index = 0
        while True:
            interface = SP_DEVICE_INTERFACE_DATA()
            interface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            ok = _setupapi.SetupDiEnumDeviceInterfaces(
                info_set, None, ctypes.byref(guid), index, ctypes.byref(interface)
            )
            if not ok:
                break
            index += 1
            required = w.DWORD()
            _setupapi.SetupDiGetDeviceInterfaceDetailW(
                info_set, ctypes.byref(interface), None, 0, ctypes.byref(required), None
            )
            if not required.value:
                continue
            buffer = ctypes.create_string_buffer(required.value)
            detail = ctypes.cast(buffer, ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W))
            detail.contents.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DETAIL_DATA_W)
            if not _setupapi.SetupDiGetDeviceInterfaceDetailW(
                info_set,
                ctypes.byref(interface),
                buffer,
                required.value,
                None,
                None,
            ):
                continue
            # DevicePath 在结构里只声明为 WCHAR[1]，不能直接取字段（会只剩一个字符），
            # 必须按结构体偏移从缓冲区读完整宽字符串。
            path_offset = SP_DEVICE_INTERFACE_DETAIL_DATA_W.DevicePath.offset
            path = ctypes.wstring_at(ctypes.addressof(buffer) + path_offset)
            info = _query_caps(path)
            if info is None:
                continue
            if vid is not None and info.vid != vid:
                continue
            devices.append(info)
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(info_set)
    return devices


class WindowsHidDevice(KeyboardDevice):
    def __init__(self, info: DeviceInfo):
        self.info = info
        self._handle = None
        self._read_event = None
        self._write_event = None
        # 有线 65B（reportId 0 + 64B 包），2.4G 接收器 33B（reportId 0 + 32B 包）
        out_len = info.output_len or 64
        if out_len < 9:
            out_len = 64
        self._out_len = out_len
        self._out_prefix = b"\x00" if out_len % 2 == 1 else b""
        self._packet_size = out_len - len(self._out_prefix)
        self._chunk = protocol.payload_capacity(self._packet_size)
        self._dirty = False
        self.open()

    # ------------------------------------------------------------ 生命周期
    def open(self) -> None:
        if not IS_WINDOWS:
            raise DeviceError("当前系统不是 Windows")
        handle = _kernel32.CreateFileW(
            self.info.path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle == INVALID_HANDLE_VALUE or not handle:
            error = ctypes.get_last_error()
            raise DeviceError(f"打开键盘失败（错误码 {error}），可能被官方驱动占用")
        self._handle = handle
        self._read_event = _kernel32.CreateEventW(None, True, False, None)
        self._write_event = _kernel32.CreateEventW(None, True, False, None)
        if not self._read_event or not self._write_event:
            self.close()
            raise DeviceError("创建 HID 事件失败")
        _hid.HidD_SetNumInputBuffers(handle, 128)

    def close(self) -> None:
        if self._handle:
            try:
                _kernel32.CancelIoEx(self._handle, None)
                time.sleep(0.05)
            except Exception:
                pass
            _kernel32.CloseHandle(self._handle)
            self._handle = None
        for attr in ("_read_event", "_write_event"):
            event = getattr(self, attr)
            if event:
                _kernel32.CloseHandle(event)
                setattr(self, attr, None)

    @property
    def is_open(self) -> bool:
        return bool(self._handle)

    # ------------------------------------------------------------ 原始读写
    def _write_raw(self, packet: bytes, timeout: float = 1.0) -> None:
        if not self._handle:
            raise DeviceError("设备未打开")
        payload = self._out_prefix + packet
        if len(payload) > self._out_len:
            raise DeviceError(
                f"报文 {len(payload)} 字节超过设备 output report {self._out_len} 字节"
            )
        if len(payload) < self._out_len:
            payload += bytes(self._out_len - len(payload))
        buffer = ctypes.create_string_buffer(payload, len(payload))
        overlapped = OVERLAPPED()
        overlapped.hEvent = self._write_event
        _kernel32.ResetEvent(self._write_event)
        written = w.DWORD()
        ok = _kernel32.WriteFile(
            self._handle, buffer, len(payload), ctypes.byref(written), ctypes.byref(overlapped)
        )
        if not ok:
            error = ctypes.get_last_error()
            if error != ERROR_IO_PENDING:
                raise DeviceError(f"HID 写入失败（错误码 {error}）")
            if _kernel32.WaitForSingleObject(self._write_event, int(timeout * 1000)) != WAIT_OBJECT_0:
                _kernel32.CancelIoEx(self._handle, ctypes.byref(overlapped))
                _kernel32.WaitForSingleObject(self._write_event, 500)
                _kernel32.GetOverlappedResult(
                    self._handle, ctypes.byref(overlapped), ctypes.byref(written), False
                )
                raise DeviceError("HID 写入超时")
            if not _kernel32.GetOverlappedResult(
                self._handle, ctypes.byref(overlapped), ctypes.byref(written), False
            ):
                raise DeviceError("HID 写入未完成")
        if written.value != len(payload):
            # 某些驱动只回报报文长度，这里不强制失败
            pass

    def _read_raw(self, timeout: float = 0.4) -> bytes | None:
        if not self._handle:
            raise DeviceError("设备未打开")
        length = self.info.input_len or 64
        buffer = ctypes.create_string_buffer(length)
        overlapped = OVERLAPPED()
        overlapped.hEvent = self._read_event
        _kernel32.ResetEvent(self._read_event)
        read = w.DWORD()
        # 不等待的内部调用不需要 GQCS；HID 读在这里总是返回 pending 或立即完成
        ok = _kernel32.ReadFile(
            self._handle, buffer, length, ctypes.byref(read), ctypes.byref(overlapped)
        )
        if not ok:
            error = ctypes.get_last_error()
            if error != ERROR_IO_PENDING:
                return None
            result = _kernel32.WaitForSingleObject(self._read_event, int(timeout * 1000))
            if result != WAIT_OBJECT_0:
                _kernel32.CancelIoEx(self._handle, ctypes.byref(overlapped))
                _kernel32.WaitForSingleObject(self._read_event, 200)
                _kernel32.GetOverlappedResult(
                    self._handle, ctypes.byref(overlapped), ctypes.byref(read), False
                )
                return None
            if not _kernel32.GetOverlappedResult(
                self._handle, ctypes.byref(overlapped), ctypes.byref(read), False
            ):
                return None
        elif read.value == 0:
            # 立即完成时也走一次 GetOverlappedResult 拿实际长度
            _kernel32.GetOverlappedResult(
                self._handle, ctypes.byref(overlapped), ctypes.byref(read), False
            )
        return bytes(buffer.raw[: read.value])

    def drain(self, timeout: float = 0.02) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._read_raw(min(0.01, max(0.001, deadline - time.monotonic()))) is None:
                return

    # ------------------------------------------------------------ 请求/应答
    def _request(self, packet: bytes, page: int, address: int, timeout: float = 0.5) -> protocol.Response | None:
        # 只有上一次请求超时（可能还有迟到的应答）才需要清一次输入队列。
        # 每次分片都等 20ms 的话，2.4G 页 2 的 22 个分片会白等约 0.4s。
        if self._dirty:
            self.drain(0.01)
            self._dirty = False
        self._write_raw(packet)
        expected = protocol.read_command(page) if (packet[1] & 0xF0) == 0x10 else protocol.write_command(page)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self._read_raw(min(0.2, max(0.05, deadline - time.monotonic())))
            if raw is None:
                continue
            response = protocol.parse_response(raw)
            if response is None:
                continue
            if response.command & 0x0F != expected & 0x0F:
                continue
            if response.address not in (address, 0):
                continue
            return response
        self._dirty = True
        return None

    # ------------------------------------------------------------ 页面
    def read_page(self, page: int, length: int) -> bytes:
        if length <= 0:
            return b""
        result = bytearray()
        for address, size in protocol.chunk_ranges(length, self._chunk):
            response = None
            for _attempt in range(2):
                response = self._request(
                    protocol.build_read(
                        page, address, size, packet_size=self._packet_size
                    ),
                    page,
                    address,
                )
                if response is not None:
                    break
                time.sleep(0.02)
            if response is None:
                if result:
                    break
                raise DeviceError(f"读取第 {page} 页地址 {address} 超时")
            chunk = response.payload[:size]
            if len(chunk) < size:
                chunk += bytes(size - len(chunk))
            result.extend(chunk)
        return bytes(result)

    def write_page(self, page: int, data: bytes, *, commit: bool = True) -> None:
        if not data:
            return
        if len(data) > protocol.MAX_PAGE_LENGTH:
            raise DeviceError("页面数据过长")
        ranges = protocol.chunk_ranges(len(data), self._chunk)
        for index, (address, size) in enumerate(ranges):
            last = commit and index == len(ranges) - 1
            payload = data[address : address + size]
            packet = protocol.build_write(
                page, address, payload, last=last, packet_size=self._packet_size
            )
            self._write_raw(packet)
            # 吃掉回显，避免下一条请求读到上一条的应答
            self._read_raw(0.03)
            time.sleep(0.003)
        time.sleep(0.05)

    def handshake(self) -> dict[str, object]:
        packet = protocol.build_read(
            0, 0, min(24, self._chunk), packet_size=self._packet_size
        )
        try:
            self._write_raw(packet)
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                raw = self._read_raw(0.2)
                if raw is None:
                    continue
                response = protocol.parse_response(raw)
                if response is None:
                    continue
                text = "".join(
                    chr(byte) for byte in response.payload if 32 <= byte < 127
                )
                return {
                    "vid": self.info.vid,
                    "pid": self.info.pid,
                    "firmware": text.strip() or "未知",
                }
        except DeviceError:
            pass
        return {}


def open_device(info: DeviceInfo) -> WindowsHidDevice:
    if not IS_WINDOWS:
        raise DeviceNotFound("当前系统不支持 HID 直连")
    return WindowsHidDevice(info)
