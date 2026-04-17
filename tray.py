# tray.py
# System Tray 模組
# 負責建立系統匣圖示、右鍵選單，以及協調各子模組的生命週期
#
# 執行緒架構：
#   主執行緒    ── tkinter 事件迴圈（settings_ui 必須在主執行緒）
#   背景執行緒  ── pystray（Win32 訊息迴圈）
#   背景執行緒  ── KeyboardHookEngine（Hook + Raw Input 訊息迴圈）

import queue
import threading
from typing import Optional

import os
import sys

import pystray
from PIL import Image, ImageDraw, ImageOps

import config as cfg
import device as dev
import startup
from hook import KeyboardHookEngine
from settings_ui import SettingsWindow


def _resource_path(filename: str) -> str:
    """
    取得資源檔的絕對路徑，依序嘗試以下位置：
    1. PyInstaller _MEIPASS（_internal 目錄）
    2. exe 同目錄（手動複製的備用位置）
    3. 原始碼同目錄（開發環境）
    """
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, filename))
        candidates.append(os.path.join(os.path.dirname(sys.executable), filename))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))

    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[-1]  # fallback，讓 exists() 回傳 False 觸發備用繪製


_ICON_PATH = _resource_path("icon.png")


def _create_icon_image(active: bool = False, size: int = 64) -> Image.Image:
    """
    從 icon.png 產生 Tray 圖示。
    active=True 時套用藍色色調（有裝置啟用中），否則保留原始灰色。
    若 icon.png 不存在，則 fallback 為程式碼動態繪製版本。
    """
    if os.path.exists(_ICON_PATH):
        return _load_icon_from_file(active=active, size=size)
    else:
        return _draw_fallback_icon(active=active)


def _load_icon_from_file(active: bool, size: int) -> Image.Image:
    """
    載入 icon.png 並依狀態上色：
    - inactive：保留原始灰色（直接使用原圖）
    - active  ：用 ImageOps.colorize 將灰度映射為藍色調
    """
    img = Image.open(_ICON_PATH).convert("RGBA")
    img = img.resize((size, size), Image.LANCZOS)

    if not active:
        return img

    # ── 藍色版：將灰度重新映射為藍色調 ─────────────────────────
    # 分離 alpha 通道（保留原始透明度）
    *_, alpha = img.split()

    # 將 RGB 轉為灰度（保留明暗層次）
    gray = img.convert("L")

    # colorize：暗部 → 深藍，亮部 → 白色（保留高光）
    colorized = ImageOps.colorize(
        gray,
        black="#1A4FCC",   # 深藍（暗部）
        white="#FFFFFF",   # 白色（亮部 / 按鍵高光）
        mid="#2A7EFF",     # 主色藍（中間調）
        blackpoint=30,     # 低於此值的像素映射至 black
        whitepoint=220,    # 高於此值的像素映射至 white
        midpoint=128,
    )

    # 合回原始 alpha 通道，保持透明背景
    r, g, b = colorized.split()
    return Image.merge("RGBA", (r, g, b, alpha))


def _draw_fallback_icon(active: bool) -> Image.Image:
    """
    icon.png 不存在時的備用方案：用 PIL 動態繪製鍵盤圖示。
    """
    size = 32
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    color = "#2A7EFF" if active else "#888888"
    draw.rounded_rectangle([2, 8, 30, 24], radius=3, fill=color)
    for x in range(4, 28, 6):
        draw.rectangle([x, 11, x+4, 14], fill="white")
    for x in range(4, 28, 6):
        draw.rectangle([x, 16, x+4, 19], fill="white")
    draw.rectangle([8, 21, 24, 23], fill="white")
    return img


class TrayApp:
    """
    System Tray 應用程式主類別。
    整合 Hook 引擎、設定視窗、開機啟動，並管理整個程式生命週期。

    執行緒設計：
    - pystray 使用 run_detached() 跑在背景執行緒
    - tkinter（settings_ui）必須在主執行緒執行
    - 透過 _ui_queue 讓 pystray 回呼安全地派發任務到主執行緒
    """

    def __init__(self):
        self._config: dict = cfg.load()
        self._hook: Optional[KeyboardHookEngine] = None
        self._settings_window: Optional[SettingsWindow] = None
        self._tray: Optional[pystray.Icon] = None
        # 主執行緒任務佇列：pystray 回呼將 callable 放入，主迴圈取出執行
        self._ui_queue: queue.SimpleQueue = queue.SimpleQueue()

    def run(self):
        """
        啟動整個程式。
        pystray 在背景執行緒執行，主執行緒負責 tkinter 事件迴圈。
        """
        # 1. 初次掃描裝置
        self._scan_and_register_devices()

        # 2. 同步開機啟動狀態
        startup.sync(self._config.get("startup", False))

        # 3. 啟動鍵盤 Hook 引擎（背景執行緒）
        self._hook = KeyboardHookEngine(self._config)
        self._hook.start()

        # 4. 建立設定視窗物件（尚不開啟）
        self._settings_window = SettingsWindow(
            config_ref=self._config,
            on_config_changed=self._on_config_changed,
            icon_image=_create_icon_image(active=True, size=64)  # 傳入藍色版圖示（64px）
        )

        # 5. pystray 跑在背景執行緒
        self._tray = self._create_tray_icon()
        self._tray.run_detached()

        # 6. 主執行緒：處理 UI 任務佇列（含開啟設定視窗等 tkinter 操作）
        self._main_loop()

    def _main_loop(self):
        """
        主執行緒事件迴圈。
        - 從 _ui_queue 取出任務並執行（如開啟設定視窗）
        - 定期呼叫 settings_window.tick() 驅動 tkinter 事件處理
        """
        while True:
            try:
                task = self._ui_queue.get(timeout=0.016)  # 約 60fps
                if task is None:
                    break
                task()
            except queue.Empty:
                pass

            # 驅動 tkinter 視窗更新
            if self._settings_window:
                self._settings_window.tick()

    # ── Tray 圖示建立 ─────────────────────────────────────────


    def _create_tray_icon(self) -> pystray.Icon:
        """建立 pystray 圖示物件"""
        icon = pystray.Icon(
            name="MacKeySwapper",
            icon=self._get_icon_image(),
            title=self._get_tray_tooltip(),
            menu=self._build_menu()
        )
        return icon

    def _build_menu(self) -> pystray.Menu:
        """建立右鍵選單"""
        return pystray.Menu(
            pystray.MenuItem(
                "MacKeySwapper",
                None,
                enabled=False   # 標題列（不可點擊）
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "開啟設定",
                self._on_open_settings
            ),
            pystray.MenuItem(
                "重新掃描裝置",
                self._on_rescan
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                self._startup_menu_label,
                self._on_toggle_startup,
                checked=lambda _: self._config.get("startup", False)
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "結束",
                self._on_quit
            ),
        )

    def _startup_menu_label(self, _) -> str:
        return "開機時自動啟動"

    # ── 圖示與工具提示 ────────────────────────────────────────

    def _get_icon_image(self) -> Image.Image:
        """根據是否有啟用中的裝置回傳對應圖示"""
        active = any(
            kb.get("mac_mode", False)
            for kb in cfg.get_all_keyboards(self._config)
        )
        return _create_icon_image(active)

    def _get_tray_tooltip(self) -> str:
        """產生 Tray 圖示的工具提示文字"""
        active_kbs = [
            kb for kb in cfg.get_all_keyboards(self._config)
            if kb.get("mac_mode", False)
        ]
        if not active_kbs:
            return "MacKeySwapper（無啟用裝置）"
        names = "、".join(kb.get("friendly_name", "未知") for kb in active_kbs)
        return f"MacKeySwapper｜已啟用：{names}"

    def _update_tray(self):
        """更新 Tray 圖示與工具提示"""
        if self._tray:
            self._tray.icon  = self._get_icon_image()
            self._tray.title = self._get_tray_tooltip()

    # ── 選單事件處理 ──────────────────────────────────────────

    def _on_open_settings(self, icon, item):
        """將「開啟設定視窗」任務派發到主執行緒（tkinter 必須在主執行緒執行）"""
        self._ui_queue.put(self._settings_window.show)

    def _on_rescan(self, icon, item):
        """重新掃描裝置"""
        self._scan_and_register_devices()
        if self._hook:
            self._hook.refresh_devices()
        self._update_tray()

    def _on_toggle_startup(self, icon, item):
        """切換開機啟動狀態"""
        current = self._config.get("startup", False)
        new_state = not current
        success = startup.sync(new_state)
        if success:
            cfg.set_startup(self._config, new_state)
            self._update_tray()

    def _on_quit(self, icon, item):
        """結束程式"""
        if self._hook:
            self._hook.stop()
        icon.stop()
        self._ui_queue.put(None)   # 通知主執行緒結束迴圈

    # ── 內部輔助 ──────────────────────────────────────────────

    def _scan_and_register_devices(self):
        """掃描目前連接的鍵盤，新裝置自動登錄到設定檔"""
        keyboards = dev.enumerate_keyboards()
        for kb in keyboards:
            cfg.upsert_keyboard(
                self._config,
                device_id=kb["device_id"],
                friendly_name=kb["friendly_name"]
            )

    def _on_config_changed(self, new_config: dict):
        """設定變更時的回呼（由 settings_ui 呼叫）"""
        self._config = new_config
        if self._hook:
            self._hook.update_config(new_config)
        self._update_tray()
