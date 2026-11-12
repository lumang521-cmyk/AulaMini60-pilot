# -*- coding: utf-8 -*-
"""Tkinter 界面。"""
from __future__ import annotations

import json
import hashlib
import os
import queue
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from . import autostart, codec, keycodes
from .tray import TrayIcon
from .watcher import foreground_process, list_process_names
from .config import (
    APP_NAME,
    APP_VERSION,
    AppConfig,
    BackupStore,
    ConfigError,
    DEFAULT_DATA_DIR,
    Profile,
    Rule,
    WIRELESS_SLEEP_OPTIONS,
    new_id,
)
from .engine import Engine
from .device.base import DeviceInfo

APP_FONT = "Microsoft YaHei UI"
SLEEP_KEEP_LABEL = "不修改（保持键盘当前设置）"


def _sleep_label(minutes: int | None) -> str:
    if minutes is None:
        return SLEEP_KEEP_LABEL
    return WIRELESS_SLEEP_OPTIONS.get(minutes, SLEEP_KEEP_LABEL)


def _sleep_value(label: str) -> int | None:
    for minutes, text in WIRELESS_SLEEP_OPTIONS.items():
        if text == label:
            return minutes
    return None


def _format_time() -> str:
    return time.strftime("%H:%M:%S")


class Mini60App(tk.Tk):
    def __init__(
        self,
        config_path: Path | None = None,
        simulator: bool = False,
        silent: bool = False,
    ):
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1180x780")
        self.minsize(1000, 680)

        try:
            self.cfg = AppConfig.load(config_path)
        except ConfigError as exc:
            self.cfg = AppConfig()
            broken = Path(config_path) if config_path else DEFAULT_DATA_DIR / "config.json"
            try:
                broken.rename(broken.with_name(f"config.broken-{int(time.time())}.json"))
            except OSError:
                pass
            messagebox.showwarning(
                APP_NAME,
                f"配置文件损坏，已另存备份并新建空配置：\n{exc}",
                parent=self,
            )
        if simulator:
            self.cfg.settings.simulator = True
        self.backup_store = BackupStore()
        autostart.refresh_if_enabled()
        self._tray: TrayIcon | None = None

        self._log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._status_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._tray_queue: queue.Queue[str] = queue.Queue()
        self._devices: list[DeviceInfo] = []
        self._closing = False
        self._quitting = False
        self._tray_ok = False

        self.engine = Engine(self.cfg, on_log=self._push_log)
        self.engine.on_status = self._push_status

        self._setup_style()
        self._build_menu()
        self._build_layout()
        self._refresh_profiles()
        self._refresh_rules()
        self._refresh_backups()
        self._load_settings_into_form()
        self._update_buttons()
        self._setup_tray()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.engine.start()
        self.after(120, self._drain_queues)
        self.after(300, self._auto_connect)
        if silent:
            if self._tray_ok:
                self.withdraw()
            else:
                self.deiconify()

    # ------------------------------------------------------------ 外观
    def _setup_style(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "clam", "default"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        try:
            default_font = tkfont.nametofont("TkDefaultFont")
            default_font.configure(family=APP_FONT, size=10)
            self.option_add("*Font", default_font)
        except Exception:
            pass
        style.configure("Treeview", rowheight=26)
        style.configure("Treeview.Heading", font=(APP_FONT, 10, "bold"))
        style.configure("Title.TLabel", font=(APP_FONT, 16, "bold"))
        style.configure("Subtitle.TLabel", foreground="#5b6472")
        style.configure("Good.TLabel", foreground="#1a7f37")
        style.configure("Bad.TLabel", foreground="#b42318")
        style.configure("Warn.TLabel", foreground="#b54708")
        style.configure("TLabelframe.Label", font=(APP_FONT, 10, "bold"))

    def _build_menu(self) -> None:
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="打开数据目录", command=self._open_data_dir)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="快速上手", command=self._show_help)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.config(menu=menubar)

    # ------------------------------------------------------------ 布局
    def _build_layout(self) -> None:
        header = ttk.Frame(self, padding=(16, 12, 16, 6))
        header.pack(fill="x")
        ttk.Label(header, text="Mini60 场景切换器", style="Title.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="指定程序切到前台时，自动把狼蛛 MINI60 HE 的按键功能切换成你定义的方案",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=14, pady=(6, 0))

        body = ttk.Frame(self, padding=(12, 0, 12, 0))
        body.pack(fill="both", expand=True)
        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill="both", expand=True)

        self.tab_device = ttk.Frame(self.notebook, padding=12)
        self.tab_profiles = ttk.Frame(self.notebook, padding=12)
        self.tab_rules = ttk.Frame(self.notebook, padding=12)
        self.tab_settings = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.tab_device, text="  设备  ")
        self.notebook.add(self.tab_profiles, text="  方案  ")
        self.notebook.add(self.tab_rules, text="  程序  ")
        self.notebook.add(self.tab_settings, text="  设置  ")

        self._build_device_tab()
        self._build_profiles_tab()
        self._build_rules_tab()
        self._build_settings_tab()
        self._build_log_panel()
        self._build_statusbar()

    def _build_device_tab(self) -> None:
        frame = self.tab_device

        box = ttk.LabelFrame(frame, text="键盘连接", padding=12)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)

        self.conn_state = ttk.Label(box, text="未连接", style="Bad.TLabel")
        self.conn_state.grid(row=0, column=0, sticky="w")
        self.conn_detail = ttk.Label(box, text="", style="Subtitle.TLabel")
        self.conn_detail.grid(row=0, column=1, columnspan=4, sticky="w", padx=10)

        ttk.Label(box, text="设备：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.device_combo = ttk.Combobox(box, state="readonly", values=[])
        self.device_combo.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        ttk.Button(box, text="刷新设备", command=self._refresh_devices).grid(
            row=1, column=2, padx=(8, 0), pady=(10, 0)
        )
        ttk.Button(box, text="连接", command=self._connect_device).grid(
            row=1, column=3, padx=(8, 0), pady=(10, 0)
        )
        ttk.Button(box, text="断开", command=self._disconnect_device).grid(
            row=1, column=4, padx=(8, 0), pady=(10, 0)
        )

        options = ttk.Frame(box)
        options.grid(row=2, column=0, columnspan=5, sticky="w", pady=(10, 0))
        self.simulator_var = tk.BooleanVar(value=self.cfg.settings.simulator)
        ttk.Checkbutton(
            options,
            text="使用模拟器（没有键盘也能演示/测试）",
            variable=self.simulator_var,
            command=self._toggle_simulator,
        ).pack(side="left")
        self.dry_run_var = tk.BooleanVar(value=self.cfg.settings.dry_run)
        ttk.Checkbutton(
            options,
            text="仅演练，不写键盘",
            variable=self.dry_run_var,
            command=self._toggle_dry_run,
        ).pack(side="left", padx=18)
        ttk.Button(options, text="读取诊断信息", command=self._diagnose).pack(side="left")

        backups = ttk.LabelFrame(frame, text="配置备份（写入前会自动备份）", padding=12)
        backups.pack(fill="both", expand=True, pady=(12, 0))
        backups.columnconfigure(0, weight=1)
        backups.rowconfigure(0, weight=1)

        self.backup_list = tk.Listbox(backups, height=7, activestyle="none")
        self.backup_list.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(backups, orient="vertical", command=self.backup_list.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.backup_list.configure(yscrollcommand=scroll.set)

        button_row = ttk.Frame(backups)
        button_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(button_row, text="立即备份", command=self._backup_now).pack(side="left")
        ttk.Button(button_row, text="恢复选中备份", command=self._restore_backup).pack(
            side="left", padx=8
        )
        ttk.Button(button_row, text="打开备份目录", command=self._open_backup_dir).pack(side="left")

        hint = ttk.Label(
            frame,
            text=(
                "提示：键盘要用手里的数据线直连；官方网页驱动 / 桌面驱动开着的时候，"
                "厂商接口可能被占用，先关掉再点“刷新设备”。"
            ),
            style="Subtitle.TLabel",
        )
        hint.pack(fill="x", pady=(10, 0))

    def _build_profiles_tab(self) -> None:
        frame = self.tab_profiles
        frame.columnconfigure(0, weight=3, minsize=600)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(frame, text="方案列表", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        columns = ("name", "pages", "source")
        self.profile_tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        self.profile_tree.heading("name", text="名称")
        self.profile_tree.heading("pages", text="页面大小（1 / 2 / 3）")
        self.profile_tree.heading("source", text="来源")
        self.profile_tree.column("name", width=190)
        self.profile_tree.column("pages", width=210)
        self.profile_tree.column("source", width=60, anchor="center")
        self.profile_tree.grid(row=0, column=0, sticky="nsew")
        self.profile_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_profile_detail())
        self.profile_tree.bind("<Double-1>", lambda _e: self._open_key_editor())

        scroll = ttk.Scrollbar(left, orient="vertical", command=self.profile_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.profile_tree.configure(yscrollcommand=scroll.set)

        buttons = ttk.Frame(left)
        buttons.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="从键盘抓取", command=self._capture_profile).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(buttons, text="复制", command=self._copy_profile).grid(
            row=0, column=1, padx=(6, 0)
        )
        ttk.Button(buttons, text="导入", command=self._import_profile).grid(
            row=0, column=2, padx=(6, 0)
        )
        ttk.Button(buttons, text="导出", command=self._export_profile).grid(
            row=0, column=3, padx=(6, 0)
        )
        ttk.Button(buttons, text="重命名", command=self._rename_profile).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        ttk.Button(buttons, text="删除", command=self._delete_profile).grid(
            row=1, column=1, padx=(6, 0), pady=(6, 0)
        )

        right = ttk.LabelFrame(frame, text="方案详情", padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        self.profile_detail = tk.Text(right, height=12, wrap="word", relief="flat", state="disabled")
        self.profile_detail.grid(row=0, column=0, sticky="nsew")

        actions = ttk.Frame(right)
        actions.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(actions, text="应用到键盘", command=self._apply_selected_profile).pack(side="left")
        ttk.Button(actions, text="设为默认", command=self._set_default_profile).pack(
            side="left", padx=6
        )
        ttk.Button(actions, text="编辑按键", command=self._open_key_editor).pack(side="left")
        ttk.Button(actions, text="与键盘比较", command=self._compare_profile).pack(
            side="left", padx=6
        )

        tip = ttk.Label(
            right,
            text=(
                "推荐流程：先在官方网页驱动里把键位/高级键调好并保存，关掉驱动页面，"
                "再回这里点“从键盘抓取”。抓下一套前先在“设备”页点“断开”，"
                "否则官方驱动可能打不开键盘。"
            ),
            style="Subtitle.TLabel",
            wraplength=360,
            justify="left",
        )
        tip.grid(row=2, column=0, sticky="w", pady=(10, 0))

    def _build_rules_tab(self) -> None:
        frame = self.tab_rules
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        top = ttk.Frame(frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.rule_status = ttk.Label(top, text="当前没有匹配的触发程序", style="Subtitle.TLabel")
        self.rule_status.pack(side="left")

        columns = ("exe", "profile", "enabled", "note")
        self.rule_tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        self.rule_tree.heading("exe", text="程序（exe）")
        self.rule_tree.heading("profile", text="切换到这个方案")
        self.rule_tree.heading("enabled", text="启用")
        self.rule_tree.heading("note", text="备注")
        self.rule_tree.column("exe", width=240)
        self.rule_tree.column("profile", width=220)
        self.rule_tree.column("enabled", width=60, anchor="center")
        self.rule_tree.column("note", width=300)
        self.rule_tree.grid(row=1, column=0, sticky="nsew")
        self.rule_tree.bind("<Double-1>", lambda _e: self._edit_rule())

        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.rule_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.rule_tree.configure(yscrollcommand=scroll.set)

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(buttons, text="添加程序…", command=self._add_rule_dialog).pack(side="left")
        ttk.Button(buttons, text="识别前台程序", command=self._add_rule_from_foreground).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="手动输入", command=self._add_rule_manual).pack(side="left")
        ttk.Button(buttons, text="编辑", command=self._edit_rule).pack(side="left", padx=6)
        ttk.Button(buttons, text="启用/停用", command=self._toggle_rule).pack(side="left")
        ttk.Button(buttons, text="删除", command=self._delete_rule).pack(side="left", padx=6)
        ttk.Button(buttons, text="上移", command=lambda: self._move_rule(-1)).pack(
            side="left", padx=(18, 0)
        )
        ttk.Button(buttons, text="下移", command=lambda: self._move_rule(1)).pack(
            side="left", padx=6
        )
        ttk.Button(buttons, text="查看激活匹配", command=self._inspect_activation).pack(
            side="left", padx=18
        )
        ttk.Button(buttons, text="立即应用一次", command=self._test_rule).pack(side="left")

        hint = ttk.Label(
            frame,
            text=(
                "规则按“当前前台（激活）的程序”匹配：程序激活时切换到对应方案，"
                "切到别的程序后恢复默认方案（可在设置里关闭）。"
                "exe 名支持 * 和 ? 通配符（例如 aow_*.exe）；不确定进程名就点“识别前台程序”。"
            ),
            style="Subtitle.TLabel",
            wraplength=1040,
            justify="left",
        )
        hint.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _build_settings_tab(self) -> None:
        frame = self.tab_settings
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)

        general = ttk.LabelFrame(frame, text="切换行为", padding=12)
        general.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        general.columnconfigure(1, weight=1)

        self.set_restore = tk.BooleanVar()
        self.set_apply_start = tk.BooleanVar()
        self.set_backup = tk.BooleanVar()
        self.set_verify = tk.BooleanVar()

        ttk.Checkbutton(
            general, text="程序切走后，恢复默认方案", variable=self.set_restore
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(
            general, text="本程序启动时先应用一次默认方案", variable=self.set_apply_start
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=2)
        ttk.Checkbutton(general, text="写入前自动备份当前配置", variable=self.set_backup).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=2
        )
        ttk.Checkbutton(general, text="写入后读回校验（失败自动回滚）", variable=self.set_verify).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=2
        )

        ttk.Label(general, text="兜底轮询间隔（秒）").grid(row=4, column=0, sticky="w", pady=(10, 2))
        self.set_poll = ttk.Spinbox(general, from_=0.3, to=10.0, increment=0.1, width=8)
        self.set_poll.grid(row=4, column=1, sticky="w", pady=(10, 2))
        ttk.Label(general, text="失败重试间隔（秒）").grid(row=5, column=0, sticky="w", pady=2)
        self.set_retry = ttk.Spinbox(general, from_=1, to=120, increment=1, width=8)
        self.set_retry.grid(row=5, column=1, sticky="w", pady=2)
        ttk.Label(
            general,
            text="前台切换由系统事件即时触发，轮询只在事件丢失时兜底。",
            style="Subtitle.TLabel",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(2, 0))

        ttk.Label(general, text="无线休眠").grid(row=7, column=0, sticky="w", pady=(10, 2))
        self.set_sleep = ttk.Combobox(
            general,
            state="readonly",
            width=30,
            values=[SLEEP_KEEP_LABEL] + list(WIRELESS_SLEEP_OPTIONS.values()),
        )
        self.set_sleep.grid(row=7, column=1, sticky="w", pady=(10, 2))

        self.set_autostart = tk.BooleanVar()
        ttk.Checkbutton(general, text="开机自动启动（静默到托盘）", variable=self.set_autostart).grid(
            row=8, column=0, columnspan=2, sticky="w", pady=(10, 2)
        )
        ttk.Label(
            general,
            text="无线休眠写入键盘页 1（偏移 3），0 = 从不；切换方案时会保留该值。",
            style="Subtitle.TLabel",
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(2, 0))

        pages = ttk.LabelFrame(frame, text="读取 / 写入的页面", padding=12)
        pages.grid(row=0, column=1, sticky="nsew")
        ttk.Label(pages, text="页 1：行程 / RT / 无线休眠等", style="Subtitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(pages, text="页 2：按键功能（高级键）", style="Subtitle.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(pages, text="页 3：灯效", style="Subtitle.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w"
        )
        self.set_pages = {1: tk.BooleanVar(value=True), 2: tk.BooleanVar(value=True), 3: tk.BooleanVar(value=True)}
        ttk.Checkbutton(pages, text="包含页 1", variable=self.set_pages[1]).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Checkbutton(pages, text="包含页 2", variable=self.set_pages[2]).grid(row=3, column=1, sticky="w", pady=(8, 0))
        ttk.Checkbutton(pages, text="包含页 3", variable=self.set_pages[3]).grid(row=3, column=2, sticky="w", pady=(8, 0))
        ttk.Label(pages, text="页 2 长度（字节）").grid(row=4, column=0, sticky="w", pady=(12, 2))
        self.set_page2_len = ttk.Spinbox(pages, from_=48, to=640, increment=4, width=8)
        self.set_page2_len.grid(row=4, column=1, sticky="w", pady=(12, 2))

        device_box = ttk.LabelFrame(frame, text="设备匹配（默认不用改）", padding=12)
        device_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for col in range(6):
            device_box.columnconfigure(col, weight=1 if col in (1, 3, 5) else 0)

        ttk.Label(device_box, text="VID").grid(row=0, column=0, sticky="w")
        self.set_vid = tk.StringVar()
        ttk.Entry(device_box, textvariable=self.set_vid, width=10).grid(row=0, column=1, sticky="w")
        ttk.Label(device_box, text="PID（可空）").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.set_pid = tk.StringVar()
        ttk.Entry(device_box, textvariable=self.set_pid, width=10).grid(row=0, column=3, sticky="w")
        ttk.Label(device_box, text="Usage Page").grid(row=0, column=4, sticky="w", padx=(12, 0))
        self.set_usage_page = tk.StringVar()
        ttk.Entry(device_box, textvariable=self.set_usage_page, width=10).grid(
            row=0, column=5, sticky="w"
        )

        ttk.Label(device_box, text="Usage").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.set_usage = tk.StringVar()
        ttk.Entry(device_box, textvariable=self.set_usage, width=10).grid(
            row=1, column=1, sticky="w", pady=(6, 0)
        )

        actions = ttk.Frame(frame)
        actions.grid(row=2, column=0, columnspan=2, sticky="w", pady=(12, 0))
        ttk.Button(actions, text="保存设置", command=self._save_settings).pack(side="left")
        ttk.Button(actions, text="打开数据目录", command=self._open_data_dir).pack(side="left", padx=8)
        self.data_dir_label = ttk.Label(actions, text=str(DEFAULT_DATA_DIR), style="Subtitle.TLabel")
        self.data_dir_label.pack(side="left", padx=8)

    def _build_log_panel(self) -> None:
        frame = ttk.LabelFrame(self, text="运行日志", padding=(8, 4))
        frame.pack(fill="both", padx=12, pady=(8, 4))
        frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(frame, height=7, wrap="none", state="disabled", relief="flat")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.tag_configure("WARN", foreground="#b54708")
        self.log_text.tag_configure("ERROR", foreground="#b42318")
        self.log_text.tag_configure("INFO", foreground="#3b4350")
        self.log_text.tag_configure("time", foreground="#8a94a6")

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, padding=(14, 4, 14, 6))
        bar.pack(fill="x")
        self.status_conn = ttk.Label(bar, text="○ 未连接", style="Bad.TLabel")
        self.status_conn.pack(side="left")
        self.status_profile = ttk.Label(bar, text="当前方案：—")
        self.status_profile.pack(side="left", padx=18)
        self.status_rule = ttk.Label(bar, text="触发：—", style="Subtitle.TLabel")
        self.status_rule.pack(side="left")
        self.status_writes = ttk.Label(bar, text="写入 0 次", style="Subtitle.TLabel")
        self.status_writes.pack(side="right")

    # ------------------------------------------------------------ 队列
    def _push_log(self, level: str, text: str) -> None:
        self._log_queue.put((level, text))
        try:
            import logging

            logging.getLogger("mini60pilot").log(
                {"ERROR": logging.ERROR, "WARN": logging.WARNING}.get(level, logging.INFO), text
            )
        except Exception:
            pass

    def _push_status(self, status: dict[str, Any]) -> None:
        self._status_queue.put(status)

    def _drain_queues(self) -> None:
        try:
            while True:
                level, text = self._log_queue.get_nowait()
                self._append_log(level, text)
        except queue.Empty:
            pass
        try:
            status = None
            while True:
                status = self._status_queue.get_nowait()
        except queue.Empty:
            pass
        if status is not None:
            self._apply_status(status)
        try:
            while True:
                action = self._tray_queue.get_nowait()
                if action == "show":
                    self._show_window()
                elif action == "exit":
                    self._quit()
        except queue.Empty:
            pass
        if not self._closing:
            self.after(120, self._drain_queues)

    def _append_log(self, level: str, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{_format_time()}] ", "time")
        self.log_text.insert("end", f"{text}\n", level if level in ("WARN", "ERROR") else "INFO")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 500:
            self.log_text.delete("1.0", f"{lines - 500}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _apply_status(self, status: dict[str, Any]) -> None:
        connected = status.get("connected")
        self.status_conn.configure(
            text="● 已连接" if connected else "○ 未连接",
            style="Good.TLabel" if connected else "Bad.TLabel",
        )
        self.conn_state.configure(
            text="已连接" if connected else "未连接",
            style="Good.TLabel" if connected else "Bad.TLabel",
        )
        mode = "模拟器" if status.get("simulator") else "实机"
        self.conn_detail.configure(
            text=(status.get("device") or "—") + (f"   [{mode}]" if connected else "")
        )
        self.status_profile.configure(text=f"当前方案：{status.get('applied_profile') or '—'}")
        triggered = status.get("active_rule") or ""
        self.status_rule.configure(text=f"触发：{triggered or '—'}")
        self.status_writes.configure(
            text=f"写入 {status.get('writes', 0)} 次（Flash {status.get('flash', 0)}）"
        )
        self.rule_status.configure(text=self._rule_status_text(status))
        self.dry_run_var.set(bool(status.get("dry_run")))
        self._update_buttons()

    # ------------------------------------------------------------ 工具
    def _watch(
        self,
        future,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def check() -> None:
            if future.done():
                try:
                    result = future.result()
                except Exception as exc:
                    if on_error:
                        on_error(exc)
                    self._update_buttons()
                    return
                if on_done:
                    on_done(result)
                self._update_buttons()
            else:
                self.after(80, check)

        check()

    def _error_dialog(self, exc: Exception) -> None:
        messagebox.showerror(APP_NAME, str(exc), parent=self)

    def _selected_profile(self) -> Profile | None:
        selection = self.profile_tree.selection()
        if not selection:
            return None
        return self.cfg.profile_by_id(selection[0])

    def _selected_rule(self) -> Rule | None:
        selection = self.rule_tree.selection()
        if not selection:
            return None
        for rule in self.cfg.rules:
            if rule.id == selection[0]:
                return rule
        return None

    def _save_config(self) -> None:
        try:
            self.cfg.save()
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"保存配置失败：{exc}")
        self.engine.apply_settings(self.cfg)

    def _update_buttons(self) -> None:
        """预留：根据选中状态调整按钮可用性。"""
        self._has_profile = self._selected_profile() is not None
        self._has_rule = self._selected_rule() is not None

    # ------------------------------------------------------------ 设备
    def _auto_connect(self) -> None:
        if self._closing:
            return
        self._refresh_devices(auto_connect=True)

    def _refresh_devices(self, auto_connect: bool = False) -> None:
        self.device_combo.configure(values=["正在查找…"])
        future = self.engine.discover()
        self._watch(future, lambda items: self._fill_devices(items, auto_connect))

    def _fill_devices(self, items: list[DeviceInfo], auto_connect: bool) -> None:
        self._devices = items
        labels = [item.label() + ("  [模拟]" if item.kind == "simulator" else "") for item in items]
        self.device_combo.configure(values=labels)
        if labels:
            self.device_combo.current(0)
        if auto_connect and items:
            self.engine.connect(items[0])
        elif auto_connect:
            self._push_log("INFO", "没有发现键盘；可以勾选“使用模拟器”先体验流程")

    def _connect_device(self) -> None:
        index = self.device_combo.current()
        info = self._devices[index] if 0 <= index < len(self._devices) else None
        self._watch(self.engine.connect(info), on_error=self._error_dialog)
        self._push_log("INFO", "正在连接…")

    def _diagnose(self) -> None:
        self._push_log("INFO", "正在读取键盘配置用于诊断…")
        future = self.engine.capture()

        def done(pages: dict[int, bytes]) -> None:
            status = self.engine.status()
            lines = [
                f"设备：{status.get('device') or '—'}",
                f"模式：{'模拟器' if status.get('simulator') else '实机'}",
                "",
            ]
            for page, data in sorted(pages.items()):
                digest = hashlib.sha1(data).hexdigest()[:12]
                lines.append(f"页 {page}：{len(data)} 字节  sha1:{digest}")
                lines.append("  " + data[:32].hex(" ").upper())
            if 2 in pages:
                try:
                    ok, message = codec.Catalog(pages[2]).validate()
                    lines.extend(["", f"页 2 结构自检：{'通过' if ok else '未通过'}（{message}）"])
                except Exception as exc:  # noqa: BLE001
                    lines.extend(["", f"页 2 结构自检：失败（{exc}）"])
            messagebox.showinfo("读取诊断信息", "\n".join(lines), parent=self)

        self._watch(future, done, on_error=self._error_dialog)

    def _disconnect_device(self) -> None:
        self._watch(self.engine.disconnect())

    def _toggle_simulator(self) -> None:
        self.cfg.settings.simulator = bool(self.simulator_var.get())
        self._save_config()
        self._refresh_devices(auto_connect=True)

    def _toggle_dry_run(self) -> None:
        self.cfg.settings.dry_run = bool(self.dry_run_var.get())
        self._save_config()

    def _refresh_backups(self) -> None:
        self.backup_list.delete(0, "end")
        self._backup_files = self.backup_store.list_files()
        for path in self._backup_files:
            self.backup_list.insert("end", path.name)

    def _backup_now(self) -> None:
        self._watch(
            self.engine.backup_now("手动备份"),
            lambda _p: self._refresh_backups(),
            on_error=self._error_dialog,
        )

    def _restore_backup(self) -> None:
        selection = self.backup_list.curselection()
        if not selection:
            messagebox.showinfo(APP_NAME, "先在列表里选中一个备份")
            return
        path = self._backup_files[selection[0]]
        if not messagebox.askyesno(APP_NAME, f"把键盘恢复成这个备份？\n\n{path.name}"):
            return
        self._watch(
            self.engine.restore_backup(str(path)),
            lambda _r: self._refresh_backups(),
            on_error=self._error_dialog,
        )

    # ------------------------------------------------------------ 方案
    def _refresh_profiles(self) -> None:
        self.profile_tree.delete(*self.profile_tree.get_children())
        for profile in self.cfg.profiles:
            source = {"capture": "抓取", "import": "导入", "edit": "编辑"}.get(profile.source, profile.source)
            self.profile_tree.insert(
                "", "end", iid=profile.id, values=(profile.name, profile.summary(), source)
            )
        if self.cfg.profiles:
            first = self.cfg.default_profile or self.cfg.profiles[0]
            if self.profile_tree.exists(first.id):
                self.profile_tree.selection_set(first.id)
        self._show_profile_detail()

    def _show_profile_detail(self) -> None:
        profile = self._selected_profile()
        self.profile_detail.configure(state="normal")
        self.profile_detail.delete("1.0", "end")
        if profile is None:
            self.profile_detail.insert("end", "还没有方案。\n\n先在官方驱动里调好一套键位，再点下面的“从键盘抓取”。")
        else:
            is_default = profile.id == self.cfg.default_profile_id
            lines = [
                f"名称：{profile.name}",
                f"默认方案：{'是' if is_default else '否'}",
                f"来源：{profile.source}",
                f"创建：{profile.created}",
                f"更新：{profile.updated}",
                "",
                "页面：",
            ]
            for page in sorted(profile.pages):
                lines.append(f"  页 {page}：{len(profile.pages[page])} 字节")
            if profile.note:
                lines.extend(["", f"备注：{profile.note}"])
            if 2 in profile.pages:
                try:
                    catalog = codec.Catalog(profile.pages[2])
                    ok, message = catalog.validate()
                    lines.extend(["", f"页 2 自检：{'通过' if ok else '未通过'}（{message}）"])
                except ValueError as exc:
                    lines.extend(["", f"页 2 自检：失败（{exc}）"])
            self.profile_detail.insert("end", "\n".join(lines))
        self.profile_detail.configure(state="disabled")

    def _capture_profile(self) -> None:
        name = simpledialog.askstring(
            APP_NAME,
            "给当前键盘配置起个名字（例如：游戏、办公、默认）：",
            initialvalue=f"方案 {time.strftime('%m-%d %H:%M')}",
            parent=self,
        )
        if not name:
            return
        self._push_log("INFO", "正在读取键盘配置…")
        future = self.engine.capture()

        def done(pages: dict[int, bytes]) -> None:
            profile = Profile(id=new_id(), name=name, pages=pages, source="capture")
            self.cfg.add_profile(profile)
            self._save_config()
            self._refresh_profiles()
            self.profile_tree.selection_set(profile.id)
            self._show_profile_detail()

        self._watch(future, done, on_error=self._error_dialog)

    def _copy_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        copy = profile.copy()
        self.cfg.add_profile(copy)
        self._save_config()
        self._refresh_profiles()
        self.profile_tree.selection_set(copy.id)

    def _rename_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        name = simpledialog.askstring(APP_NAME, "新名称：", initialvalue=profile.name, parent=self)
        if not name:
            return
        profile.name = self.cfg.unique_name(name)
        profile.updated = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._save_config()
        self._refresh_profiles()

    def _delete_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        if not messagebox.askyesno(APP_NAME, f"删除方案「{profile.name}」？\n关联的程序规则也会被删除。"):
            return
        self.cfg.remove_profile(profile.id)
        self._save_config()
        self._refresh_profiles()
        self._refresh_rules()

    def _set_default_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        self.cfg.default_profile_id = profile.id
        self._save_config()
        self._refresh_profiles()
        self._push_log("INFO", f"默认方案已设为「{profile.name}」")

    def _apply_selected_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo(APP_NAME, "先选中一个方案")
            return
        self._push_log("INFO", f"正在应用方案「{profile.name}」…")
        self._watch(
            self.engine.apply_profile(profile.id, "手动应用"), on_error=self._error_dialog
        )

    def _compare_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        future = self.engine.capture()

        def done(pages: dict[int, bytes]) -> None:
            lines = [f"方案「{profile.name}」与键盘当前配置：", ""]
            for page in sorted(set(pages) | set(profile.pages)):
                current = pages.get(page)
                saved = profile.pages.get(page)
                if current is None or saved is None:
                    lines.append(f"页 {page}：只存在于一侧，无法比较")
                    continue
                diff = sum(1 for a, b in zip(current, saved) if a != b) + abs(len(current) - len(saved))
                lines.append(f"页 {page}：{'一致' if diff == 0 else f'{diff} 处不同'}（{len(saved)}B）")
            messagebox.showinfo(APP_NAME, "\n".join(lines))

        self._watch(future, done, on_error=self._error_dialog)

    def _export_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            return
        path = filedialog.asksaveasfilename(
            title="导出方案",
            defaultextension=".json",
            initialfile=f"{profile.name}.json",
            filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        Path(path).write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._push_log("INFO", f"已导出方案：{path}")

    def _import_profile(self) -> None:
        path = filedialog.askopenfilename(title="导入方案", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
            profile = Profile.from_dict(raw)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"导入失败：{exc}")
            return
        profile.id = new_id()
        profile.source = "import"
        self.cfg.add_profile(profile)
        self._save_config()
        self._refresh_profiles()

    # ------------------------------------------------------------ 改键
    def _open_key_editor(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            messagebox.showinfo(APP_NAME, "先选中一个方案")
            return
        if 2 not in profile.pages:
            messagebox.showinfo(APP_NAME, "这个方案没有页 2 数据，无法编辑按键")
            return
        KeyEditor(self, profile)

    # ------------------------------------------------------------ 规则
    def _refresh_rules(self) -> None:
        self.rule_tree.delete(*self.rule_tree.get_children())
        for rule in self.cfg.rules:
            profile = self.cfg.profile_by_id(rule.profile_id)
            self.rule_tree.insert(
                "",
                "end",
                iid=rule.id,
                values=(rule.exe, profile.name if profile else "(方案已删除)", "是" if rule.enabled else "否", rule.note),
            )
        self._sync_rule_status()

    def _rule_status_text(self, status: dict[str, Any]) -> str:
        triggered = status.get("active_rule") or ""
        active = status.get("active_program") or ""
        front = f"前台：{active}" if active else "前台：—"
        if triggered:
            return (
                f"{front}；正在由 {triggered} 触发，"
                f"当前方案：{status.get('applied_profile') or '—'}"
            )
        return f"{front}；没有匹配的触发程序"

    def _sync_rule_status(self) -> None:
        self.rule_status.configure(text=self._rule_status_text(self.engine.status()))

    def _rule_dialog(self, rule: Rule | None = None, preset_exe: str = "") -> None:
        if not self.cfg.profiles:
            messagebox.showinfo(APP_NAME, "先创建至少一个方案")
            return
        dialog = RuleDialog(self, self.cfg, rule, preset_exe)
        self.wait_window(dialog)
        if dialog.result:
            if rule is None:
                self.cfg.rules.append(dialog.result)
            else:
                rule.exe = dialog.result.exe
                rule.profile_id = dialog.result.profile_id
                rule.note = dialog.result.note
                rule.enabled = dialog.result.enabled
            self._save_config()
            self._refresh_rules()

    def _add_rule_dialog(self) -> None:
        self._rule_dialog(None)

    def _add_rule_from_foreground(self) -> None:
        info = foreground_process()
        if not info:
            messagebox.showerror(APP_NAME, "没有读到前台窗口，请先点一下目标程序再试。")
            return
        exe, title, pid = info
        self._push_log("INFO", f"当前前台程序：{exe}（pid {pid}，窗口：{title}）")
        self._rule_dialog(None, preset_exe=exe)

    def _inspect_activation(self) -> None:
        foreground = foreground_process()
        active = foreground[0].lower() if foreground else ""
        lines = [f"当前前台（激活）程序：{foreground[0] if foreground else '（没读到）'}", ""]
        if foreground:
            title, pid = foreground[1], foreground[2]
            lines.extend([f"pid：{pid}", f"窗口标题：{title}", ""])
        lines.append("规则匹配（按当前前台程序）：")
        for rule in self.cfg.rules:
            hits = rule.match_names({active}) if active else []
            state = "命中，会切换" if hits else "不匹配"
            lines.append(f"{rule.exe}  →  {state}")
        names = list_process_names()
        lines.extend(["", f"运行中的进程（{len(names)} 个，前 40 个）："])
        lines.extend(sorted(names)[:40])
        messagebox.showinfo("激活匹配检查", "\n".join(lines), parent=self)

    def _add_rule_manual(self) -> None:
        exe = simpledialog.askstring(
            APP_NAME, "输入程序名（例如 cs2.exe）：", parent=self
        )
        if not exe:
            return
        if not exe.lower().endswith(".exe"):
            exe += ".exe"
        self._rule_dialog(None, preset_exe=exe)

    def _edit_rule(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        self._rule_dialog(rule)

    def _toggle_rule(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        rule.enabled = not rule.enabled
        self._save_config()
        self._refresh_rules()

    def _delete_rule(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        self.cfg.rules = [item for item in self.cfg.rules if item.id != rule.id]
        self._save_config()
        self._refresh_rules()

    def _move_rule(self, delta: int) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        index = next((i for i, item in enumerate(self.cfg.rules) if item.id == rule.id), -1)
        target = index + delta
        if index < 0 or not 0 <= target < len(self.cfg.rules):
            return
        self.cfg.rules[index], self.cfg.rules[target] = self.cfg.rules[target], self.cfg.rules[index]
        self._save_config()
        self._refresh_rules()
        self.rule_tree.selection_set(rule.id)

    def _test_rule(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            return
        self._watch(
            self.engine.apply_profile(rule.profile_id, f"测试规则 {rule.exe}"),
            on_error=self._error_dialog,
        )

    # ------------------------------------------------------------ 设置
    def _load_settings_into_form(self) -> None:
        settings = self.cfg.settings
        self.set_restore.set(settings.restore_on_exit)
        self.set_apply_start.set(settings.apply_default_on_start)
        self.set_backup.set(settings.backup_before_write)
        self.set_verify.set(settings.verify_after_write)
        self.set_poll.set(str(settings.poll_interval))
        self.set_retry.set(str(settings.retry_seconds))
        self.set_sleep.set(_sleep_label(settings.wireless_sleep))
        self.set_pages[1].set(1 in settings.pages)
        self.set_pages[2].set(2 in settings.pages)
        self.set_pages[3].set(3 in settings.pages)
        self.set_page2_len.set(str(settings.page2_length))
        self.set_vid.set(f"0x{settings.device_vid:04X}")
        self.set_pid.set("" if settings.device_pid is None else f"0x{settings.device_pid:04X}")
        self.set_usage_page.set(f"0x{settings.device_usage_page:04X}")
        self.set_usage.set(f"0x{settings.device_usage:04X}")
        try:
            self.set_autostart.set(autostart.is_enabled())
        except Exception:
            self.set_autostart.set(False)

    def _parse_int(self, text: str, default: int) -> int:
        text = text.strip()
        if not text:
            return default
        try:
            return int(text, 16) if text.lower().startswith("0x") else int(text)
        except ValueError:
            return default

    def _save_settings(self) -> None:
        settings = self.cfg.settings
        settings.restore_on_exit = bool(self.set_restore.get())
        settings.apply_default_on_start = bool(self.set_apply_start.get())
        settings.backup_before_write = bool(self.set_backup.get())
        settings.verify_after_write = bool(self.set_verify.get())
        try:
            settings.poll_interval = float(self.set_poll.get())
            settings.retry_seconds = float(self.set_retry.get())
        except ValueError:
            messagebox.showerror(APP_NAME, "兜底轮询间隔 / 重试间隔必须是数字")
            return
        settings.wireless_sleep = _sleep_value(self.set_sleep.get())
        settings.pages = [page for page in (1, 2, 3) if self.set_pages[page].get()] or [1, 2, 3]
        try:
            settings.page2_length = int(self.set_page2_len.get())
        except ValueError:
            messagebox.showerror(APP_NAME, "页 2 长度必须是数字")
            return
        settings.sanitized()
        settings.device_vid = self._parse_int(self.set_vid.get(), 0x0C45)
        pid_text = self.set_pid.get().strip()
        settings.device_pid = self._parse_int(pid_text, 0) if pid_text else None
        settings.device_usage_page = self._parse_int(self.set_usage_page.get(), 0xFF68)
        settings.device_usage = self._parse_int(self.set_usage.get(), 0x0061)
        settings.dry_run = bool(self.dry_run_var.get())
        try:
            autostart.set_enabled(bool(self.set_autostart.get()))
            settings.autostart = bool(self.set_autostart.get())
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning(APP_NAME, f"设置开机自启失败：{exc}")
        self._save_config()
        self._refresh_devices()
        if settings.wireless_sleep is not None:
            self._watch(
                self.engine.set_wireless_sleep(settings.wireless_sleep),
                on_error=self._sleep_write_failed,
            )
        self._push_log("INFO", "设置已保存")

    def _sleep_write_failed(self, exc: Exception) -> None:
        self._push_log("WARN", f"无线休眠暂未写入键盘（{exc}），连接键盘后会自动应用")

    # ------------------------------------------------------------ 杂项
    def _open_data_dir(self) -> None:
        DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(DEFAULT_DATA_DIR))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"打不开目录：{exc}")

    def _open_backup_dir(self) -> None:
        self.backup_store.directory.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(self.backup_store.directory))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"打不开目录：{exc}")

    def _show_help(self) -> None:
        messagebox.showinfo(
            "快速上手",
            "1. 键盘用数据线直连。先打开官方网页驱动，把一套键位/高级键调好并保存，\n"
            "   然后关闭驱动页面（驱动会占用键盘）。\n"
            "2. 打开本程序，「设备」页点「刷新设备」→「连接」。\n"
            "3. 「方案」页点「从键盘抓取」，命名（例如“默认”）。\n"
            "4. 要做第二套：先在「设备」页点「断开」，再去官方驱动调好保存并关闭，\n"
            "   回来「连接」→「从键盘抓取」，命名（例如“游戏”）。\n"
            "5. 在「程序」页添加程序（如 cs2.exe），选择要切换到的方案。\n"
            "6. 程序切到前台（激活）→ 自动切换；切到别的程序 → 自动恢复默认方案。\n"
            "7. 需要的可在「设置」页选「无线休眠」时间（写入键盘页 1，0 = 从不）。\n\n"
            "建议先勾选“仅演练，不写键盘”看日志确认规则正确，再取消勾选实际写入。",
        )

    def _show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            f"{APP_NAME} v{APP_VERSION}\n\n"
            "通过 SN34F280 HID 协议直接读写狼蛛 MINI60 HE 的配置页。\n"
            "写入前自动备份，写入后读回校验，失败自动回滚。",
        )

    def _tray_icon_file(self) -> Path:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        return base / "assets" / "app.ico"

    def _setup_tray(self) -> None:
        icon = self._tray_icon_file()
        if icon.exists():
            try:
                self.iconbitmap(str(icon))
            except Exception:
                pass
        self._tray = TrayIcon(
            self._tray_queue.put,
            "Mini60 场景切换器 - 运行中",
            icon,
        )
        self._tray_ok = self._tray.start()
        if not self._tray_ok:
            self._push_log("WARN", "系统托盘不可用，关闭窗口将直接退出")

    def _show_window(self) -> None:
        self.deiconify()
        try:
            self.state("normal")
        except Exception:
            pass
        self.lift()
        try:
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
        except Exception:
            pass
        self.focus_force()

    def _on_close(self) -> None:
        if self._quitting:
            return
        if not self._tray_ok or self._tray is None:
            self._quit()
            return
        self.withdraw()
        try:
            self._tray.notify(
                "Mini60 场景切换器",
                "已最小化到托盘；双击托盘图标打开，右键菜单可退出。",
            )
        except Exception:
            pass

    def _quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._closing = True
        try:
            if self._tray:
                self._tray.stop()
        except Exception:
            pass
        try:
            self.engine.stop()
        finally:
            self.destroy()


class RuleDialog(tk.Toplevel):
    def __init__(
        self,
        master: Mini60App,
        config: AppConfig,
        rule: Rule | None = None,
        preset_exe: str = "",
    ):
        super().__init__(master)
        self.title("编辑程序规则" if rule else "添加程序规则")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.result: Rule | None = None
        self._config = config
        self._original = rule

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="程序：").grid(row=0, column=0, sticky="w")
        self.exe_var = tk.StringVar(value=rule.exe if rule else preset_exe)
        exe_entry = ttk.Entry(frame, textvariable=self.exe_var, width=46)
        exe_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(frame, text="浏览…", command=self._browse).grid(row=0, column=2, padx=(6, 0))

        ttk.Label(frame, text="方案：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.profile_names = [profile.name for profile in config.profiles]
        self.profile_var = tk.StringVar()
        combo = ttk.Combobox(frame, textvariable=self.profile_var, values=self.profile_names, state="readonly")
        combo.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(10, 0))
        if rule:
            profile = config.profile_by_id(rule.profile_id)
            if profile:
                self.profile_var.set(profile.name)
        elif self.profile_names:
            default = config.default_profile
            self.profile_var.set(default.name if default else self.profile_names[0])

        ttk.Label(frame, text="备注：").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.note_var = tk.StringVar(value=rule.note if rule else "")
        ttk.Entry(frame, textvariable=self.note_var, width=46).grid(
            row=2, column=1, columnspan=2, sticky="ew", pady=(10, 0)
        )

        self.enabled_var = tk.BooleanVar(value=rule.enabled if rule else True)
        ttk.Checkbutton(frame, text="启用这条规则", variable=self.enabled_var).grid(
            row=3, column=1, sticky="w", pady=(10, 0)
        )

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=self._save).pack(side="right", padx=(0, 8))

        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())
        self.update_idletasks()
        self._center(master)

    def _center(self, master: tk.Misc) -> None:
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="选择程序", filetypes=[("程序", "*.exe"), ("所有文件", "*.*")]
        )
        if path:
            self.exe_var.set(Path(path).name)

    def _save(self) -> None:
        exe = self.exe_var.get().strip()
        if not exe:
            messagebox.showerror(APP_NAME, "请填写程序名", parent=self)
            return
        if not exe.lower().endswith(".exe"):
            exe += ".exe"
        profile = self._config.profile_by_name(self.profile_var.get())
        if profile is None:
            messagebox.showerror(APP_NAME, "请选择方案", parent=self)
            return
        self.result = Rule(
            id=self._original.id if self._original else new_id(),
            exe=exe,
            profile_id=profile.id,
            enabled=bool(self.enabled_var.get()),
            note=self.note_var.get().strip(),
        )
        self.destroy()


class KeyEditor(tk.Toplevel):
    def __init__(self, master: Mini60App, profile: Profile):
        super().__init__(master)
        self.title(f"编辑按键 - {profile.name}")
        self.geometry("920x640")
        self.transient(master)
        self.app = master
        self.profile = profile
        self.baseline: codec.Catalog | None = self._baseline_catalog(profile)

        try:
            self.catalog = codec.Catalog(profile.pages[2])
        except ValueError as exc:
            messagebox.showerror(APP_NAME, f"页 2 数据无法解析：{exc}", parent=self)
            self.destroy()
            return
        ok, message = self.catalog.validate()
        if not ok:
            messagebox.showerror(
                APP_NAME,
                f"页 2 自检未通过：{message}\n\n"
                "为了不写坏键盘，按键编辑只对识别出的标准按键表开放。\n"
                "你可以继续用“从键盘抓取”的方式保存和切换方案。",
                parent=self,
            )
            self.destroy()
            return

        self._build()
        self._refresh_rows()
        self.update_idletasks()
        self._center(master)

    def _baseline_catalog(self, profile: Profile) -> codec.Catalog | None:
        default = self.app.cfg.default_profile
        if default and default.id != profile.id and 2 in default.pages:
            try:
                catalog = codec.Catalog(default.pages[2])
                if catalog.validate()[0]:
                    return catalog
            except ValueError:
                return None
        return None

    def _center(self, master: tk.Misc) -> None:
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _build(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=3)
        outer.columnconfigure(1, weight=2)
        outer.rowconfigure(0, weight=1)

        left = ttk.LabelFrame(outer, text="按键 / 槽位列表", padding=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            left, columns=("key", "current", "slot"), show="headings", selectmode="browse"
        )
        self.tree.heading("key", text="键位")
        self.tree.heading("current", text="当前功能")
        self.tree.heading("slot", text="槽位")
        self.tree.column("key", width=130)
        self.tree.column("current", width=230)
        self.tree.column("slot", width=70, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._load_selected())

        scroll = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)

        right = ttk.LabelFrame(outer, text="把选中的键改成", padding=10)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)

        ttk.Label(right, text="功能类型：").grid(row=0, column=0, sticky="w")
        self.type_var = tk.StringVar(value="按键")
        self.type_combo = ttk.Combobox(
            right, textvariable=self.type_var, values=["按键", "多媒体", "无输出", "恢复出厂"], state="readonly"
        )
        self.type_combo.grid(row=0, column=1, sticky="ew")
        self.type_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_editor_state())

        ttk.Label(right, text="按键：").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.key_var = tk.StringVar()
        key_values = sorted({name for name in keycodes.HID_NAMES.values() if name != "(无)"})
        self.key_combo = ttk.Combobox(right, textvariable=self.key_var, values=key_values)
        self.key_combo.grid(row=1, column=1, sticky="ew", pady=(10, 0))

        ttk.Label(right, text="修饰键：").grid(row=2, column=0, sticky="nw", pady=(10, 0))
        mods = ttk.Frame(right)
        mods.grid(row=2, column=1, sticky="w", pady=(10, 0))
        self.mod_vars = {
            "Ctrl": tk.BooleanVar(),
            "Shift": tk.BooleanVar(),
            "Alt": tk.BooleanVar(),
            "Win": tk.BooleanVar(),
        }
        for index, (name, var) in enumerate(self.mod_vars.items()):
            ttk.Checkbutton(mods, text=name, variable=var).grid(row=0, column=index, padx=(0, 8))

        ttk.Label(right, text="多媒体：").grid(row=3, column=0, sticky="w", pady=(10, 0))
        self.media_var = tk.StringVar()
        media_values = sorted(set(keycodes.CONSUMER_NAMES.values()))
        self.media_combo = ttk.Combobox(right, textvariable=self.media_var, values=media_values, state="readonly")
        self.media_combo.grid(row=3, column=1, sticky="ew", pady=(10, 0))

        self.preview = ttk.Label(right, text="", style="Subtitle.TLabel", wraplength=300, justify="left")
        self.preview.grid(row=4, column=0, columnspan=2, sticky="w", pady=(14, 0))

        buttons = ttk.Frame(right)
        buttons.grid(row=5, column=0, columnspan=2, sticky="w", pady=(18, 0))
        ttk.Button(buttons, text="应用修改", command=self._apply).pack(side="left")
        ttk.Button(buttons, text="保存并关闭", command=self._save_close).pack(side="left", padx=8)
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="left")

        note = ttk.Label(
            right,
            text=(
                "说明：槽位编号按出厂按键表排列；编辑后只改这个方案，"
                "需要点“应用到键盘”或等程序触发才会写入。"
            ),
            style="Subtitle.TLabel",
            wraplength=300,
            justify="left",
        )
        note.grid(row=6, column=0, columnspan=2, sticky="w", pady=(16, 0))
        self.feedback = ttk.Label(right, text="", style="Good.TLabel")
        self.feedback.grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._update_editor_state()

    def _rows(self) -> list[tuple[int, str, str]]:
        seen: dict[str, int] = {}
        rows: list[tuple[int, str, str]] = []
        for index in range(len(self.catalog)):
            slot = self.catalog.slot(index)
            baseline_slot = self.baseline.slot(index) if self.baseline and index < len(self.baseline) else None
            label = ""
            if baseline_slot is not None and baseline_slot.kind == codec.KIND_EMPTY:
                # 页 2 是覆盖表，全 0 的槽位没有“出厂功能”可显示，用槽位号更诚实
                label = f"槽位 0x{index:02X}"
            elif baseline_slot is not None:
                if baseline_slot.kind == codec.KIND_KEY and baseline_slot.usage:
                    label = keycodes.hid_name(baseline_slot.usage)
                elif baseline_slot.kind == codec.KIND_MEDIA:
                    label = keycodes.consumer_name(baseline_slot.usage)
            if not label and slot.kind == codec.KIND_KEY and slot.usage:
                label = keycodes.hid_name(slot.usage) + "（按当前功能）"
            if not label and slot.kind == codec.KIND_MEDIA:
                label = keycodes.consumer_name(slot.usage) + "（按当前功能）"
            if not label:
                continue
            count = seen.get(label, 0) + 1
            seen[label] = count
            if count > 1:
                label = f"{label} #{count}"
            rows.append((index, label, codec.describe_slot(slot)))
        return rows

    def _refresh_rows(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, label, current in self._rows():
            self.tree.insert("", "end", iid=str(index), values=(label, current, f"0x{index:02X}"))
        if self.tree.get_children():
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)
        else:
            self.feedback.configure(
                text=(
                    "这个方案没有自定义改键（页 2 全 0 表示全部使用出厂映射）。\n"
                    "请先在官方网页驱动里改好键位并保存到键盘，再回到这里重新抓取。"
                )
            )

    def _selected_index(self) -> int | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return int(selection[0])

    def _load_selected(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        slot = self.catalog.slot(index)
        if slot.kind == codec.KIND_MEDIA:
            self.type_var.set("多媒体")
            self.media_var.set(keycodes.consumer_name(slot.usage))
        elif slot.kind == codec.KIND_KEY and slot.usage == 0:
            self.type_var.set("无输出")
        else:
            self.type_var.set("按键")
            self.key_var.set(keycodes.hid_name(slot.usage) if slot.usage else "")
            for name, var in self.mod_vars.items():
                var.set(False)
            for bit, name in keycodes.BIT_TO_MODIFIER.items():
                if slot.modifiers & bit:
                    short = name.replace("L", "", 1) if name.startswith("L") else name
                    if short in self.mod_vars:
                        self.mod_vars[short].set(True)
        self._update_editor_state()
        self.preview.configure(text=f"槽位 0x{index:02X} 当前：{codec.describe_slot(slot)}")

    def _update_editor_state(self) -> None:
        kind = self.type_var.get()
        state = "normal" if kind == "按键" else "disabled"
        self.key_combo.configure(state=state)
        media_state = "readonly" if kind == "多媒体" else "disabled"
        self.media_combo.configure(state=media_state)

    def _build_action(self) -> codec.Action | None:
        kind = self.type_var.get()
        if kind == "无输出":
            return codec.Action(kind="none")
        if kind == "恢复出厂":
            return codec.Action(kind="default")
        if kind == "多媒体":
            try:
                usage = keycodes.consumer_from_name(self.media_var.get())
            except ValueError as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=self)
                return None
            return codec.Action(kind="media", usage=usage)
        try:
            usage = keycodes.usage_from_name(self.key_var.get())
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return None
        mods = 0
        for name, var in self.mod_vars.items():
            if var.get():
                mods |= keycodes.MODIFIER_BITS.get("L" + name, 0)
        return codec.Action(kind="key", usage=usage, modifiers=mods)

    def _apply(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        action = self._build_action()
        if action is None:
            return
        try:
            slot = codec.apply_action(self.catalog, index, action, self.baseline)
        except (ValueError, IndexError) as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.profile.pages[2] = self.catalog.to_bytes()
        self.profile.updated = time.strftime("%Y-%m-%dT%H:%M:%S")
        if self.profile.source == "capture":
            self.profile.source = "edit"
        self.app._save_config()
        self._refresh_rows()
        self.tree.selection_set(str(index))
        self.feedback.configure(text=f"已改为：{codec.describe_slot(slot)}（记得应用到键盘）")
        self.app._show_profile_detail()

    def _save_close(self) -> None:
        self.app._save_config()
        self.app._refresh_profiles()
        self.destroy()
