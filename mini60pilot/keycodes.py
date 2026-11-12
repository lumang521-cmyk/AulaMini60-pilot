# -*- coding: utf-8 -*-
"""HID 键值表：键盘页 (0x07) 与多媒体页 (0x0C)。

键位配置里保存的是 USB HID usage，而不是 Windows 虚拟键码 —— 键盘固件直接
按 usage 发报文，所以这里只维护 usage。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 键盘 usage
HID_NAMES: dict[int, str] = {0x00: "(无)"}

for _i, _ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
    HID_NAMES[0x04 + _i] = _ch.upper()

for _i in range(10):
    HID_NAMES[0x1E + _i] = str((_i + 1) % 10)

HID_NAMES.update(
    {
        0x28: "Enter",
        0x29: "Esc",
        0x2A: "Backspace",
        0x2B: "Tab",
        0x2C: "Space",
        0x2D: "-",
        0x2E: "=",
        0x2F: "[",
        0x30: "]",
        0x31: "\\",
        0x32: "NonUS#",
        0x33: ";",
        0x34: "'",
        0x35: "`",
        0x36: ",",
        0x37: ".",
        0x38: "/",
        0x39: "CapsLock",
        0x46: "PrintScreen",
        0x47: "ScrollLock",
        0x48: "Pause",
        0x49: "Insert",
        0x4A: "Home",
        0x4B: "PageUp",
        0x4C: "Delete",
        0x4D: "End",
        0x4E: "PageDown",
        0x4F: "Right",
        0x50: "Left",
        0x51: "Down",
        0x52: "Up",
        0x53: "NumLock",
        0x54: "KP/",
        0x55: "KP*",
        0x56: "KP-",
        0x57: "KP+",
        0x58: "KPEnter",
        0x62: "KP0",
        0x63: "KP.",
        0x64: "NonUS\\",
        0x65: "Menu",
        0x66: "Power",
        0x67: "KP=",
        0x74: "Execute",
        0x75: "Help",
        0x77: "Select",
        0x78: "Stop",
        0x79: "Again",
        0x7A: "Undo",
        0x7B: "Cut",
        0x7C: "Copy",
        0x7D: "Paste",
        0x7E: "Find",
        0x7F: "Mute",
        0x80: "VolumeUp",
        0x81: "VolumeDown",
        0x85: "KP,",
        0x87: "Intl1",
        0x88: "Intl2",
        0x89: "Intl3",
        0x8A: "Intl4",
        0x8B: "Intl5",
        0x8C: "Intl6",
        0x8D: "Intl7",
        0x8E: "Intl8",
        0x8F: "Intl9",
        0x90: "Lang1",
        0x91: "Lang2",
        0xE0: "LCtrl",
        0xE1: "LShift",
        0xE2: "LAlt",
        0xE3: "LWin",
        0xE4: "RCtrl",
        0xE5: "RShift",
        0xE6: "RAlt",
        0xE7: "RWin",
    }
)

for _i in range(12):
    HID_NAMES[0x3A + _i] = f"F{_i + 1}"
for _i in range(9):
    HID_NAMES[0x59 + _i] = f"KP{_i + 1}"
for _i in range(12):
    HID_NAMES[0x68 + _i] = f"F{_i + 13}"

# 常用别名 -> usage
HID_ALIASES: dict[str, int] = {
    "escape": 0x29,
    "return": 0x28,
    "enter": 0x28,
    "bksp": 0x2A,
    "backspace": 0x2A,
    "spacebar": 0x2C,
    "caps": 0x39,
    "capslock": 0x39,
    "del": 0x4C,
    "delete": 0x4C,
    "ins": 0x49,
    "insert": 0x49,
    "pgup": 0x4B,
    "pageup": 0x4B,
    "pgdn": 0x4E,
    "pagedown": 0x4E,
    "prtsc": 0x46,
    "printscreen": 0x46,
    "scrlk": 0x47,
    "scrolllock": 0x47,
    "uparrow": 0x52,
    "downarrow": 0x51,
    "leftarrow": 0x50,
    "rightarrow": 0x4F,
    "win": 0xE3,
    "lwin": 0xE3,
    "lgui": 0xE3,
    "rwin": 0xE7,
    "rgui": 0xE7,
    "apps": 0x65,
    "menu": 0x65,
    "none": 0x00,
    "无": 0x00,
}

# 修饰键 usage -> 位掩码（USB HID 键盘修饰字节）
MODIFIER_USAGE_TO_BIT: dict[int, int] = {
    0xE0: 0x01,
    0xE1: 0x02,
    0xE2: 0x04,
    0xE3: 0x08,
    0xE4: 0x10,
    0xE5: 0x20,
    0xE6: 0x40,
    0xE7: 0x80,
}
MODIFIER_BITS: dict[str, int] = {
    "LCtrl": 0x01,
    "LShift": 0x02,
    "LAlt": 0x04,
    "LWin": 0x08,
    "RCtrl": 0x10,
    "RShift": 0x20,
    "RAlt": 0x40,
    "RWin": 0x80,
}
BIT_TO_MODIFIER: dict[int, str] = {v: k for k, v in MODIFIER_BITS.items()}

MODIFIER_ALIASES: dict[str, int] = {
    "ctrl": 0x01,
    "control": 0x01,
    "shift": 0x02,
    "alt": 0x04,
    "win": 0x08,
    "super": 0x08,
    "cmd": 0x08,
    "rctrl": 0x10,
    "rshift": 0x20,
    "ralt": 0x40,
    "rwin": 0x80,
}


def hid_name(usage: int) -> str:
    return HID_NAMES.get(usage, f"0x{usage:02X}")


def usage_from_name(name: str) -> int:
    """把 "A" / "Esc" / "0x29" / "F13" 解析成 usage。"""
    s = str(name).strip()
    if not s:
        raise ValueError("空键名")
    if s.lower().startswith("0x"):
        value = int(s, 16)
        if not 0 <= value <= 0xFF:
            raise ValueError(f"usage 超出范围: {s}")
        return value
    for usage, known in HID_NAMES.items():
        if known.lower() == s.lower():
            return usage
    if s.lower() in HID_ALIASES:
        return HID_ALIASES[s.lower()]
    # 允许 "A" 之外的单字符直接给 usage
    if len(s) == 1:
        if s.isalpha():
            return 0x04 + (ord(s.lower()) - ord("a"))
        if s.isdigit():
            return 0x1E + ((int(s) + 9) % 10)
    raise ValueError(f"不认识的键名: {name}")


def format_modifiers(mask: int) -> str:
    if not mask:
        return ""
    names = [BIT_TO_MODIFIER[1 << bit] for bit in range(8) if mask & (1 << bit)]
    return "+".join(names) + "+"


def parse_combo(text: str) -> tuple[int, int]:
    """解析 "Ctrl+Shift+Esc" -> (修饰位掩码, 主键 usage)。"""
    parts = [p for p in str(text).replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError("组合键为空")
    mods = 0
    usage = 0
    for part in parts:
        low = part.lower()
        if low in MODIFIER_ALIASES:
            mods |= MODIFIER_ALIASES[low]
            continue
        candidate = usage_from_name(part)
        if candidate in MODIFIER_USAGE_TO_BIT:
            mods |= MODIFIER_USAGE_TO_BIT[candidate]
            continue
        if usage:
            raise ValueError(f"组合键里出现了两个主键: {text}")
        usage = candidate
    return mods, usage


# ---------------------------------------------------------------- 多媒体 usage
CONSUMER_NAMES: dict[int, str] = {
    0x00B5: "下一曲",
    0x00B6: "上一曲",
    0x00B7: "停止",
    0x00CD: "播放/暂停",
    0x00E2: "静音",
    0x00E9: "音量+",
    0x00EA: "音量-",
    0x0183: "媒体播放器",
    0x018A: "邮件",
    0x0192: "计算器",
    0x0194: "我的电脑",
    0x0196: "浏览器主页",
    0x019E: "锁屏",
    0x0221: "浏览器搜索",
    0x0223: "浏览器首页",
    0x0224: "浏览器后退",
    0x0225: "浏览器前进",
    0x0226: "浏览器停止",
    0x0227: "浏览器刷新",
    0x022A: "浏览器收藏",
    0x022D: "休眠",
    0x0230: "亮度+",
    0x0231: "亮度-",
    0x029C: "关机",
}


def consumer_name(usage: int) -> str:
    return CONSUMER_NAMES.get(usage, f"0x{usage:04X}")


CONSUMER_ALIASES: dict[str, int] = {
    "playpause": 0x00CD,
    "play": 0x00CD,
    "next": 0x00B5,
    "prev": 0x00B6,
    "stop": 0x00B7,
    "mute": 0x00E2,
    "volup": 0x00E9,
    "voldown": 0x00EA,
    "calculator": 0x0192,
    "calc": 0x0192,
    "mail": 0x018A,
    "homepage": 0x0223,
    "browserhome": 0x0223,
    "search": 0x0221,
    "back": 0x0224,
    "forward": 0x0225,
    "refresh": 0x0227,
    "brightnessup": 0x0230,
    "brightnessdown": 0x0231,
    "sleep": 0x022D,
}


def consumer_from_name(name: str) -> int:
    s = str(name).strip()
    if not s:
        raise ValueError("空多媒体键名")
    if s.lower().startswith("0x"):
        return int(s, 16)
    for usage, known in CONSUMER_NAMES.items():
        if known == s:
            return usage
    if s.lower() in CONSUMER_ALIASES:
        return CONSUMER_ALIASES[s.lower()]
    raise ValueError(f"不认识的多媒体键名: {name}")
