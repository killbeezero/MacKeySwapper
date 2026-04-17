# settings_ui.py
# 設定視窗模組（tkinter）
# 提供圖形介面讓使用者管理各鍵盤的 Mac 模式，以及開機啟動設定

import io
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Callable, Optional

import config as cfg
import device as dev
import startup


class SettingsWindow:
    """
    設定視窗。
    - 顯示所有已記錄及目前已連接的鍵盤
    - 提供 Mac 模式開關（Checkbutton）
    - 提供開機啟動開關
    - 提供「重新掃描裝置」按鈕
    """

    def __init__(self, config_ref: dict,
                 on_config_changed: Optional[Callable[[dict], None]] = None,
                 icon_image=None):
        """
        config_ref        : 共用的設定 dict
        on_config_changed : 設定變更後的回呼（通知 hook.py 即時套用）
        icon_image        : PIL Image 物件，用作視窗圖示
        """
        self._config = config_ref
        self._on_config_changed = on_config_changed
        self._icon_image = icon_image   # PIL Image，在 _build_window 時轉為 tk 格式
        self._window: Optional[tk.Tk] = None
        self._tk_icon = None            # 保持 PhotoImage 參照避免被 GC

    def show(self):
        """開啟設定視窗（若已開啟則將其帶到最前）"""
        if self._window and self._window.winfo_exists():
            self._window.lift()
            self._window.focus_force()
            return

        self._build_window()

    def _build_window(self):
        """建立視窗元件"""
        win = tk.Tk()
        self._window = win
        win.title("MacKeySwapper 設定")
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── 套用視窗圖示 ──────────────────────────────────────
        if self._icon_image:
            try:
                # 將 PIL Image 轉為 PNG bytes → tkinter PhotoImage
                buf = io.BytesIO()
                # 放大到 64x64 讓視窗圖示更清晰
                icon64 = self._icon_image.resize((64, 64))
                icon64.save(buf, format="PNG")
                buf.seek(0)
                self._tk_icon = tk.PhotoImage(data=buf.read())
                win.iconphoto(True, self._tk_icon)
            except Exception:
                pass   # 圖示設定失敗不影響功能

        # ── 視窗置中 ──────────────────────────────────────────
        win.update_idletasks()
        w, h = 520, 400
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        # ── 主框架 ────────────────────────────────────────────
        main_frame = ttk.Frame(win, padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ── 標題 ─────────────────────────────────────────────
        ttk.Label(
            main_frame,
            text="鍵盤裝置管理",
            font=("Microsoft JhengHei UI", 12, "bold")
        ).pack(anchor=tk.W, pady=(0, 8))

        ttk.Label(
            main_frame,
            text="為每支鍵盤單獨啟用 Mac 模式（交換 Alt ↔ Win 鍵）",
            foreground="#555555"
        ).pack(anchor=tk.W, pady=(0, 8))

        # ── 鍵盤清單（Treeview + Scrollbar）────────────────────
        list_frame = ttk.Frame(main_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        columns = ("name", "device_id", "mac_mode", "last_seen")
        self._tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            height=8,
            selectmode="browse"
        )

        self._tree.heading("name",      text="裝置名稱")
        self._tree.heading("device_id", text="識別碼（VID/PID）")
        self._tree.heading("mac_mode",  text="Mac 模式")
        self._tree.heading("last_seen", text="最後連線")

        self._tree.column("name",      width=160, anchor=tk.W)
        self._tree.column("device_id", width=180, anchor=tk.W)
        self._tree.column("mac_mode",  width=70,  anchor=tk.CENTER)
        self._tree.column("last_seen", width=90,  anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL,
                                  command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # ── 操作按鈕列 ───────────────────────────────────────
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Button(
            btn_frame, text="🔄 重新掃描裝置",
            command=self._scan_devices
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            btn_frame, text="✅ 啟用 Mac 模式",
            command=lambda: self._toggle_mac_mode(True)
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            btn_frame, text="❌ 停用 Mac 模式",
            command=lambda: self._toggle_mac_mode(False)
        ).pack(side=tk.LEFT)

        # ── 分隔線 ───────────────────────────────────────────
        ttk.Separator(main_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(0, 8))

        # ── 開機啟動設定 ─────────────────────────────────────
        startup_frame = ttk.Frame(main_frame)
        startup_frame.pack(fill=tk.X, pady=(0, 8))

        self._startup_var = tk.BooleanVar(
            value=self._config.get("startup", False)
        )
        ttk.Checkbutton(
            startup_frame,
            text="Windows 開機時自動啟動 MacKeySwapper",
            variable=self._startup_var,
            command=self._on_startup_toggle
        ).pack(side=tk.LEFT)

        # ── 狀態列 ───────────────────────────────────────────
        self._status_var = tk.StringVar(value="")
        ttk.Label(
            main_frame,
            textvariable=self._status_var,
            foreground="#0066CC"
        ).pack(anchor=tk.W)

        # ── 初始載入資料 ─────────────────────────────────────
        self._refresh_list()

        # 注意：不呼叫 win.mainloop()
        # 視窗的事件迴圈由 tray.py 的 _main_loop() 透過 _tick() 驅動

    def _refresh_list(self):
        """重新整理鍵盤清單顯示"""
        # 清空現有列
        for row in self._tree.get_children():
            self._tree.delete(row)

        # 取得目前已連接的裝置 Handle 對應
        connected_ids = {
            kb["device_id"]
            for kb in dev.enumerate_keyboards()
        }

        keyboards = cfg.get_all_keyboards(self._config)

        if not keyboards:
            self._tree.insert("", tk.END, values=(
                "（尚未偵測到任何鍵盤，請按「重新掃描裝置」）",
                "", "", ""
            ))
            return

        for kb in keyboards:
            device_id   = kb.get("device_id", "")
            name        = kb.get("friendly_name", "未知裝置")
            mac_mode    = "✅ 啟用" if kb.get("mac_mode") else "—"
            last_seen   = kb.get("last_seen", "")[:10]   # 只顯示日期

            # 目前已連接的裝置以粗體標示
            tag = "connected" if device_id in connected_ids else ""

            # 識別碼只顯示 VID/PID 部分（避免太長）
            short_id = self._shorten_device_id(device_id)

            self._tree.insert("", tk.END,
                              iid=device_id,
                              values=(name, short_id, mac_mode, last_seen),
                              tags=(tag,))

        self._tree.tag_configure("connected", font=("Microsoft JhengHei UI", 9, "bold"))

    def _shorten_device_id(self, device_id: str) -> str:
        """從 device_id 中截取 VID&PID 部分"""
        import re
        match = re.search(r'(VID_[0-9A-Fa-f]{4}&PID_[0-9A-Fa-f]{4})', device_id)
        return match.group(1) if match else device_id[:30]

    def _get_selected_device_id(self) -> Optional[str]:
        """取得目前在 Treeview 中選取的裝置 device_id"""
        selected = self._tree.selection()
        if not selected:
            messagebox.showinfo("提示", "請先選取一支鍵盤裝置", parent=self._window)
            return None
        return selected[0]   # iid 即為 device_id

    def _toggle_mac_mode(self, enabled: bool):
        """切換選取裝置的 Mac 模式"""
        device_id = self._get_selected_device_id()
        if not device_id:
            return

        success = cfg.set_mac_mode(self._config, device_id, enabled)
        if success:
            state_text = "啟用" if enabled else "停用"
            self._set_status(f"已{state_text}該裝置的 Mac 模式")
            if self._on_config_changed:
                self._on_config_changed(self._config)
            self._refresh_list()
        else:
            messagebox.showerror("錯誤", "設定失敗，找不到該裝置", parent=self._window)

    def _scan_devices(self):
        """重新掃描並登錄目前已連接的鍵盤裝置"""
        keyboards = dev.enumerate_keyboards()
        if not keyboards:
            self._set_status("未偵測到任何鍵盤裝置")
            return

        new_count = 0
        for kb in keyboards:
            existing = cfg.get_keyboard(self._config, kb["device_id"])
            if existing is None:
                new_count += 1
            cfg.upsert_keyboard(
                self._config,
                device_id=kb["device_id"],
                friendly_name=kb["friendly_name"]
            )

        if self._on_config_changed:
            self._on_config_changed(self._config)

        self._refresh_list()
        self._set_status(
            f"掃描完成，共偵測到 {len(keyboards)} 支鍵盤"
            + (f"（其中 {new_count} 支為新裝置）" if new_count else "")
        )

    def _on_startup_toggle(self):
        """開機啟動 Checkbutton 狀態變更"""
        enabled = self._startup_var.get()
        success = startup.sync(enabled)
        if success:
            cfg.set_startup(self._config, enabled)
            state_text = "已啟用" if enabled else "已停用"
            self._set_status(f"開機自動啟動：{state_text}")
        else:
            # 若 Registry 操作失敗，還原 Checkbutton 狀態
            self._startup_var.set(not enabled)
            messagebox.showerror(
                "錯誤", "修改開機啟動設定失敗，請確認程式有足夠的權限",
                parent=self._window
            )

    def _set_status(self, message: str):
        """更新狀態列文字"""
        self._status_var.set(message)

    def tick(self):
        """
        由主執行緒的事件迴圈定期呼叫，驅動 tkinter 處理視窗事件。
        視窗未開啟時直接返回。
        """
        if self._window and self._window.winfo_exists():
            try:
                self._window.update()
            except tk.TclError:
                self._window = None

    @property
    def is_open(self) -> bool:
        return bool(self._window and self._window.winfo_exists())

    def _on_close(self):
        """關閉視窗"""
        if self._window:
            self._window.destroy()
            self._window = None
