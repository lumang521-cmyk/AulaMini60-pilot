# -*- coding: utf-8 -*-
"""配置模型与本地存储。

所有数据都放在程序目录下的 data/（可用环境变量 MINI60_DATA_DIR 覆盖），
方便整个文件夹拷走即用。
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

APP_NAME = "Mini60 场景切换器"
APP_VERSION = "1.0.0"

if getattr(sys, "frozen", False):
    # PyInstaller 单文件运行时 __file__ 在临时解压目录；数据要跟着 exe 走
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path(os.environ.get("MINI60_DATA_DIR", PROJECT_ROOT / "data"))


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class ConfigError(Exception):
    pass


# 页 1（官方驱动 GET_GAME_MODE / SET_GAME_MODE）偏移 3 是键盘的无线休眠时间，
# 单位分钟，0 = 从不休眠。取值与官方 Aula Hub 的“休眠设置”选项一致。
WIRELESS_SLEEP_OPTIONS: dict[int, str] = {
    0: "从不",
    1: "1 分钟",
    2: "2 分钟",
    3: "3 分钟",
    5: "5 分钟",
    10: "10 分钟",
    15: "15 分钟",
    20: "20 分钟",
    25: "25 分钟",
    30: "30 分钟",
    45: "45 分钟",
    60: "1 小时",
    120: "2 小时",
}


# ---------------------------------------------------------------- 数据模型
@dataclass
class Profile:
    id: str
    name: str
    pages: dict[int, bytes] = field(default_factory=dict)
    note: str = ""
    source: str = "capture"
    created: str = field(default_factory=now_text)
    updated: str = field(default_factory=now_text)

    def page_hex(self, page: int) -> str:
        return self.pages[page].hex(" ").upper()

    def summary(self) -> str:
        return "  ".join(f"{page}:{len(data)}B" for page, data in sorted(self.pages.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "note": self.note,
            "source": self.source,
            "created": self.created,
            "updated": self.updated,
            "pages": {str(page): data.hex() for page, data in sorted(self.pages.items())},
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Profile":
        pages: dict[int, bytes] = {}
        for key, value in (raw.get("pages") or {}).items():
            try:
                pages[int(key)] = bytes.fromhex(str(value))
            except ValueError as exc:
                raise ConfigError(f"方案 {raw.get('name')!r} 的页 {key} 数据不合法") from exc
        return cls(
            id=str(raw.get("id") or new_id()),
            name=str(raw.get("name") or "未命名"),
            pages=pages,
            note=str(raw.get("note") or ""),
            source=str(raw.get("source") or "capture"),
            created=str(raw.get("created") or now_text()),
            updated=str(raw.get("updated") or now_text()),
        )

    def copy(self, new_name: str | None = None) -> "Profile":
        return Profile(
            id=new_id(),
            name=new_name or self.name + " 副本",
            pages=dict(self.pages),
            note=self.note,
            source=self.source,
            created=now_text(),
            updated=now_text(),
        )


@dataclass
class Rule:
    id: str
    exe: str
    profile_id: str
    enabled: bool = True
    note: str = ""

    @property
    def exe_key(self) -> str:
        return Path(self.exe).name.lower()

    def match_names(self, names: set[str]) -> list[str]:
        """返回命中的进程名；支持精确 exe 名和 * / ? 通配符。"""
        pattern = self.exe_key
        if not pattern:
            return []
        if "*" in pattern or "?" in pattern:
            return [name for name in names if fnmatch.fnmatchcase(name, pattern)]
        return [pattern] if pattern in names else []

    def matches(self, names: set[str]) -> bool:
        """支持精确 exe 名，也支持 * / ? 通配符（如 aow_*.exe）。"""
        return bool(self.match_names(names))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "exe": self.exe,
            "profile_id": self.profile_id,
            "enabled": self.enabled,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Rule":
        return cls(
            id=str(raw.get("id") or new_id()),
            exe=str(raw.get("exe") or ""),
            profile_id=str(raw.get("profile_id") or ""),
            enabled=bool(raw.get("enabled", True)),
            note=str(raw.get("note") or ""),
        )


@dataclass
class Settings:
    pages: list[int] = field(default_factory=lambda: [1, 2, 3])
    page2_length: int = 512
    poll_interval: float = 0.8
    retry_seconds: float = 5.0
    wireless_sleep: int | None = None
    restore_on_exit: bool = True
    apply_default_on_start: bool = True
    backup_before_write: bool = True
    verify_after_write: bool = True
    dry_run: bool = False
    simulator: bool = False
    autostart: bool = False
    device_vid: int = 0x0C45
    device_usage_page: int = 0xFF68
    device_usage: int = 0x0061
    device_pid: int | None = None
    device_product: str = ""

    def sanitized(self) -> "Settings":
        pages = sorted({int(p) for p in self.pages if 1 <= int(p) <= 6}) or [1, 2, 3]
        self.pages = pages
        if int(self.page2_length) in (244, 448):  # 旧版本的 61/112 记录长度
            self.page2_length = 512
        self.page2_length = max(24, min(MAX_PAGE2_LENGTH, int(self.page2_length) // 4 * 4))
        self.poll_interval = max(0.3, min(10.0, float(self.poll_interval)))
        self.retry_seconds = max(1.0, min(120.0, float(self.retry_seconds)))
        if self.wireless_sleep is not None:
            try:
                sleep_value: int | None = int(self.wireless_sleep)
            except (TypeError, ValueError):
                sleep_value = None
            self.wireless_sleep = (
                sleep_value if sleep_value in WIRELESS_SLEEP_OPTIONS else None
            )
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "pages": self.pages,
            "page2_length": self.page2_length,
            "poll_interval": self.poll_interval,
            "retry_seconds": self.retry_seconds,
            "wireless_sleep": self.wireless_sleep,
            "restore_on_exit": self.restore_on_exit,
            "apply_default_on_start": self.apply_default_on_start,
            "backup_before_write": self.backup_before_write,
            "verify_after_write": self.verify_after_write,
            "dry_run": self.dry_run,
            "simulator": self.simulator,
            "autostart": self.autostart,
            "device_vid": self.device_vid,
            "device_pid": self.device_pid,
            "device_usage_page": self.device_usage_page,
            "device_usage": self.device_usage,
            "device_product": self.device_product,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Settings":
        settings = cls()
        for key, value in (raw or {}).items():
            if hasattr(settings, key):
                setattr(settings, key, value)
        return settings.sanitized()


MAX_PAGE2_LENGTH = 640


@dataclass
class AppConfig:
    profiles: list[Profile] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    default_profile_id: str = ""
    settings: Settings = field(default_factory=Settings)
    path: Path | None = None

    # ------------------------------------------------------------ 查询
    def profile_by_id(self, profile_id: str) -> Profile | None:
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        return None

    def profile_by_name(self, name: str) -> Profile | None:
        for profile in self.profiles:
            if profile.name == name:
                return profile
        return None

    @property
    def default_profile(self) -> Profile | None:
        profile = self.profile_by_id(self.default_profile_id)
        if profile is not None:
            return profile
        return self.profiles[0] if self.profiles else None

    def unique_name(self, base: str) -> str:
        base = re.sub(r"[\r\n\t]+", " ", base).strip() or "未命名"
        existing = {profile.name for profile in self.profiles}
        if base not in existing:
            return base
        index = 2
        while f"{base} ({index})" in existing:
            index += 1
        return f"{base} ({index})"

    # ------------------------------------------------------------ 修改
    def add_profile(self, profile: Profile) -> Profile:
        profile.name = self.unique_name(profile.name)
        self.profiles.append(profile)
        if not self.profile_by_id(self.default_profile_id):
            self.default_profile_id = profile.id
        return profile

    def remove_profile(self, profile_id: str) -> bool:
        before = len(self.profiles)
        self.profiles = [p for p in self.profiles if p.id != profile_id]
        self.rules = [r for r in self.rules if r.profile_id != profile_id]
        if self.default_profile_id == profile_id:
            self.default_profile_id = self.profiles[0].id if self.profiles else ""
        return len(self.profiles) != before

    # ------------------------------------------------------------ 存储
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "app": APP_NAME,
            "saved_at": now_text(),
            "default_profile_id": self.default_profile_id,
            "settings": self.settings.to_dict(),
            "profiles": [profile.to_dict() for profile in self.profiles],
            "rules": [rule.to_dict() for rule in self.rules],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any], path: Path | None = None) -> "AppConfig":
        config = cls(
            profiles=[Profile.from_dict(item) for item in raw.get("profiles") or []],
            rules=[Rule.from_dict(item) for item in raw.get("rules") or []],
            default_profile_id=str(raw.get("default_profile_id") or ""),
            settings=Settings.from_dict(raw.get("settings") or {}),
            path=path,
        )
        if not config.profile_by_id(config.default_profile_id):
            config.default_profile_id = config.profiles[0].id if config.profiles else ""
        return config

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        path = Path(path) if path else DEFAULT_DATA_DIR / "config.json"
        if not path.exists():
            return cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"配置文件读取失败：{path}\n{exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"配置文件格式不对（根节点应为对象）：{path}")
        return cls.from_dict(raw, path)

    def save(self, path: Path | None = None) -> Path:
        target = Path(path) if path else (self.path or DEFAULT_DATA_DIR / "config.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, target)
        self.path = target
        return target


# ---------------------------------------------------------------- 备份
class BackupStore:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory) if directory else DEFAULT_DATA_DIR / "backups"

    def save(self, pages: dict[int, bytes], reason: str = "manual") -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_reason = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "-", reason)[:24] or "manual"
        path = self.directory / f"{stamp}-{safe_reason}.json"
        index = 2
        while path.exists():
            path = self.directory / f"{stamp}-{safe_reason}-{index}.json"
            index += 1
        payload = {
            "saved_at": now_text(),
            "reason": reason,
            "pages": {str(page): data.hex() for page, data in sorted(pages.items())},
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.prune()
        return path

    def prune(self, keep: int = 40) -> None:
        """只保留最近若干份备份，避免无限增长。"""
        files = self.list_files()
        for old in files[keep:]:
            try:
                old.unlink()
            except OSError:
                pass

    def latest(self) -> Path | None:
        if not self.directory.exists():
            return None
        files = sorted(self.directory.glob("*.json"))
        return files[-1] if files else None

    def load(self, path: Path) -> dict[int, bytes]:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        pages: dict[int, bytes] = {}
        for key, value in (raw.get("pages") or {}).items():
            pages[int(key)] = bytes.fromhex(str(value))
        return pages

    def list_files(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob("*.json"), reverse=True)
