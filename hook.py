# hook.py
# 鍵盤鉤子核心模組
# 整合兩個 Win32 機制：
#   1. Raw Input (WM_INPUT)        ── 識別「哪支鍵盤」發出了按鍵事件
#   2. Low-Level Keyboard Hook     ── 攔截按鍵，對目標裝置執行 Alt ↔ Win 交換

import ctypes
import ctypes.wintypes as wintypes
import threading
from ctypes import windll, byref, sizeof, CFUNCTYPE, POINTER
from typing import Callable, Optional

import config as cfg
import device as dev

# ── Win32 常數 ────────────────────────────────────────────────
WH_KEYBOARD_LL      = 13
HC_ACTION           = 0
WM_KEYDOWN          = 0x0100
WM_KEYUP            = 0x0101
WM_SYSKEYDOWN       = 0x0104
WM_SYSKEYUP         = 0x0105
WM_INPUT            = 0x00FF

# 虛擬鍵碼
VK_LMENU            = 0xA4   # 左 Alt
VK_RMENU            = 0xA5   # 右 Alt
VK_LWIN             = 0x5B   # 左 Windows
VK_RWIN             = 0x5C   # 右 Windows

# Raw Input 相關
RIDEV_INPUTSINK     = 0x00000100   # 即使視窗非前景也接收
RIM_TYPEKEYBOARD    = 1
RIDI_DEVICENAME     = 0x20000007

# SendInput 旗標
KEYEVENTF_KEYUP     = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
INPUT_KEYBOARD      = 1


# ── Win32 結構定義 ────────────────────────────────────────────

class KBDLLHOOKSTRUCT(ctypes.Structure):
    """Low-Level Keyboard Hook 回呼傳入的資料結構"""
    _fields_ = [
        ("vkCode",      wintypes.DWORD),   # 虛擬鍵碼
        ("scanCode",    wintypes.DWORD),   # 掃描碼
        ("flags",       wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulong),
    ]

class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType",  wintypes.DWORD),
        ("dwSize",  wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam",  wintypes.WPARAM),
    ]

class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode",         wintypes.USHORT),
        ("Flags",            wintypes.USHORT),
        ("Reserved",         wintypes.USHORT),
        ("VKey",             wintypes.USHORT),
        ("Message",          wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]

class RAWINPUT_UNION(ctypes.Union):
    _fields_ = [("keyboard", RAWKEYBOARD)]

class RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("data",   RAWINPUT_UNION),
    ]

class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage",     wintypes.USHORT),
        ("dwFlags",     wintypes.DWORD),
        ("hwndTarget",  wintypes.HWND),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         wintypes.WORD),
        ("wScan",       wintypes.WORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),   # ULONG_PTR，64 位元下為 8 bytes
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    # Windows SDK 定義 INPUT 的 cbSize 為整個結構大小（包含 union）
    # 64 位元下：type(4) + padding(4) + KEYBDINPUT(24) = 32 bytes
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u",    INPUT_UNION),
    ]

# ── Hook 引擎類別 ─────────────────────────────────────────────

# 標記：由本程式自己發送的合成按鍵，避免無限遞迴攔截
INJECTED_EXTRA_INFO = 0xDEADBEEF

LowLevelKeyboardProc = CFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, POINTER(KBDLLHOOKSTRUCT)
)


class KeyboardHookEngine:
    """
    整合 Raw Input 與 LL Keyboard Hook 的核心引擎。

    Raw Input 負責記錄最後一次鍵盤事件來自哪支裝置 Handle，
    LL Hook 負責攔截並決定是否交換 Alt/Win。
    """

    def __init__(self, config_ref: dict):
        """
        config_ref: 外部傳入的設定 dict（共用同一物件，方便即時更新）
        """
        self._config = config_ref
        self._hook_handle: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._hwnd: Optional[int] = None          # Raw Input 用的隱形視窗
        self._running = False

        # 裝置 Handle → 裝置資訊 的對應表（由 device.py 建立）
        self._handle_map: dict[int, dict] = {}

        # 最近一次 Raw Input 事件來自的裝置 Handle
        self._last_raw_handle: int = 0

        # 追蹤正在交換中的按鍵：原始 vk → 已發送的目標 vk
        # 確保 DOWN 之後的 UP 一定用相同的目標 vk 發出，防止卡鍵
        self._active_swaps: dict[int, int] = {}

        # LL Hook 回呼函式（必須保持參照，避免被 GC 回收）
        self._hook_proc = LowLevelKeyboardProc(self._keyboard_proc)

    # ── 公開介面 ─────────────────────────────────────────────

    def start(self):
        """啟動 Hook 引擎（在背景執行緒中執行訊息迴圈）"""
        if self._running:
            return
        self._running = True
        self._handle_map = dev.get_handle_to_device_map()

        # 預設 _last_raw_handle：找到第一個已啟用 Mac 模式的裝置
        # 避免程式剛啟動時第一次按鍵因 Raw Input 尚未更新而漏掉交換
        for handle, device_info in self._handle_map.items():
            device_id = device_info.get("device_id", "")
            if cfg.is_mac_mode(self._config, device_id):
                self._last_raw_handle = handle
                break

        self._thread = threading.Thread(target=self._run_message_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止 Hook 引擎"""
        self._running = False
        if self._hook_handle:
            windll.user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = None
        # 發送 WM_QUIT 結束訊息迴圈
        if self._hwnd:
            windll.user32.PostMessageW(self._hwnd, 0x0012, 0, 0)  # WM_QUIT

    def refresh_devices(self):
        """重新列舉裝置（鍵盤插拔後呼叫）"""
        self._handle_map = dev.get_handle_to_device_map()

    def update_config(self, new_config: dict):
        """即時更新設定（不需重啟）"""
        self._config = new_config

    # ── 內部實作 ─────────────────────────────────────────────

    def _run_message_loop(self):
        """背景執行緒：安裝 Hook、建立隱形視窗、執行訊息迴圈"""
        # 安裝 LL Keyboard Hook
        # LL Hook（WH_KEYBOARD_LL）為系統全域 Hook，hmod 必須傳 None
        # 傳入模組 Handle 在 Python 環境下會導致錯誤碼 126（ERROR_MOD_NOT_FOUND）
        self._hook_handle = windll.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._hook_proc, None, 0
        )
        if not self._hook_handle:
            print(f"[Hook] SetWindowsHookExW 失敗，錯誤碼：{ctypes.GetLastError()}")
            return

        # 建立隱形視窗以接收 WM_INPUT
        self._hwnd = self._create_message_window()
        if self._hwnd:
            self._register_raw_input(self._hwnd)

        # 標準 Windows 訊息迴圈
        msg = wintypes.MSG()
        while self._running:
            ret = windll.user32.GetMessageW(byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            if msg.message == WM_INPUT:
                self._process_raw_input(msg.lParam)
            windll.user32.TranslateMessage(byref(msg))
            windll.user32.DispatchMessageW(byref(msg))

        # 清理
        if self._hook_handle:
            windll.user32.UnhookWindowsHookEx(self._hook_handle)
            self._hook_handle = None

    def _create_message_window(self) -> Optional[int]:
        """建立一個僅用於接收訊息的隱形視窗（Message-Only Window）"""
        # 64 位元下 WPARAM/LPARAM 都是 64 位元，必須用 c_ssize_t / c_size_t
        # 否則 ctypes 預設用 32 位元轉換會溢位
        WNDPROC = CFUNCTYPE(
            ctypes.c_ssize_t,   # 回傳值 LRESULT
            wintypes.HANDLE,    # hwnd
            wintypes.UINT,      # msg
            ctypes.c_size_t,    # wparam (WPARAM = UINT_PTR)
            ctypes.c_ssize_t,   # lparam (LPARAM = LONG_PTR)
        )

        # 同樣宣告 DefWindowProcW 的型別，避免 lparam 溢位
        _DefWindowProcW = windll.user32.DefWindowProcW
        _DefWindowProcW.restype  = ctypes.c_ssize_t
        _DefWindowProcW.argtypes = [
            wintypes.HANDLE,    # hwnd
            wintypes.UINT,      # msg
            ctypes.c_size_t,    # wparam
            ctypes.c_ssize_t,   # lparam
        ]

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_INPUT:
                self._process_raw_input(lparam)
            return _DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = WNDPROC(wnd_proc)   # 保持參照

        # 注意：Python 3.10 的 wintypes 沒有 HICON / HCURSOR / HBRUSH，
        # 統一改用 wintypes.HANDLE（底層同為指標大小的整數）
        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ("style",         wintypes.UINT),
                ("lpfnWndProc",   WNDPROC),
                ("cbClsExtra",    ctypes.c_int),
                ("cbWndExtra",    ctypes.c_int),
                ("hInstance",     wintypes.HANDLE),
                ("hIcon",         wintypes.HANDLE),
                ("hCursor",       wintypes.HANDLE),
                ("hbrBackground", wintypes.HANDLE),
                ("lpszMenuName",  wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR),
            ]

        class_name = "MacKeySwapperMsgWnd"
        wc = WNDCLASSW()
        wc.lpfnWndProc   = self._wnd_proc_ref
        wc.hInstance     = windll.kernel32.GetModuleHandleW(None)
        wc.lpszClassName = class_name

        windll.user32.RegisterClassW(byref(wc))

        # 明確宣告 CreateWindowExW 的參數與回傳型別
        # 第 9 個參數 hWndParent 需要是 c_ssize_t 才能正確傳遞 HWND_MESSAGE(-3)
        windll.user32.CreateWindowExW.restype  = wintypes.HANDLE
        windll.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,    # dwExStyle
            wintypes.LPCWSTR,  # lpClassName
            wintypes.LPCWSTR,  # lpWindowName
            wintypes.DWORD,    # dwStyle
            ctypes.c_int,      # X
            ctypes.c_int,      # Y
            ctypes.c_int,      # nWidth
            ctypes.c_int,      # nHeight
            ctypes.c_ssize_t,  # hWndParent（用 ssize_t 才能接受 -3）
            wintypes.HANDLE,   # hMenu
            wintypes.HANDLE,   # hInstance
            wintypes.LPVOID,   # lpParam
        ]

        HWND_MESSAGE = -3
        hwnd = windll.user32.CreateWindowExW(
            0, class_name, "MacKeySwapper", 0,
            0, 0, 0, 0, HWND_MESSAGE, None, wc.hInstance, None
        )
        return hwnd if hwnd else None

    def _register_raw_input(self, hwnd: int):
        """向系統註冊接收鍵盤 Raw Input 事件"""
        rid = RAWINPUTDEVICE()
        rid.usUsagePage = 0x01    # Generic Desktop Controls
        rid.usUsage     = 0x06    # Keyboard
        rid.dwFlags     = RIDEV_INPUTSINK
        rid.hwndTarget  = hwnd
        windll.user32.RegisterRawInputDevices(byref(rid), 1, sizeof(RAWINPUTDEVICE))

    def _process_raw_input(self, lparam: int):
        """
        解析 WM_INPUT 訊息，記錄最後一次輸入的裝置 Handle。
        這個值會在 LL Hook 回呼中被讀取以判斷來源鍵盤。
        """
        size = wintypes.UINT(0)
        windll.user32.GetRawInputData(lparam, 0x10000003, None, byref(size), sizeof(RAWINPUTHEADER))
        if size.value == 0:
            return

        buf = ctypes.create_string_buffer(size.value)
        windll.user32.GetRawInputData(lparam, 0x10000003, buf, byref(size), sizeof(RAWINPUTHEADER))

        raw = ctypes.cast(buf, POINTER(RAWINPUT)).contents
        if raw.header.dwType == RIM_TYPEKEYBOARD:
            # handle=0 表示合成按鍵（keybd_event 產生），不更新來源記錄
            if raw.header.hDevice:
                self._last_raw_handle = raw.header.hDevice

    def _keyboard_proc(self, nCode: int, wParam: int,
                        lParam: POINTER(KBDLLHOOKSTRUCT)) -> int:
        if nCode == HC_ACTION:
            kb = lParam.contents

            # 跳過本程式自己注入的合成按鍵，避免無限遞迴
            try:
                extra = int(kb.dwExtraInfo) & 0xFFFFFFFF
            except (TypeError, ValueError):
                extra = 0
            if extra == INJECTED_EXTRA_INFO:
                return windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

            vk = kb.vkCode
            is_keydown = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)

            if vk in (VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN):

                if is_keydown:
                    # DOWN 事件：判斷是否需要交換
                    if self._should_swap_for_current_device():
                        target_vk = self._get_swapped_vk(vk)
                        # 記錄此次交換，供配對的 UP 使用
                        self._active_swaps[vk] = target_vk
                        self._send_key(target_vk, key_up=False)
                        return 1
                    else:
                        # 若目前不需交換，清除可能殘留的記錄
                        self._active_swaps.pop(vk, None)
                else:
                    # UP 事件：優先查 _active_swaps，確保與 DOWN 配對
                    if vk in self._active_swaps:
                        target_vk = self._active_swaps.pop(vk)
                        self._send_key(target_vk, key_up=True)
                        return 1

        return windll.user32.CallNextHookEx(None, nCode, wParam, lParam)

    def _should_swap_for_current_device(self) -> bool:
        handle = self._last_raw_handle or 0
        if handle == 0:
            return False

        device_info = self._handle_map.get(handle)
        if device_info is None:
            self.refresh_devices()
            device_info = self._handle_map.get(handle)
            if device_info is None:
                return False

        device_id = device_info.get("device_id", "")
        return cfg.is_mac_mode(self._config, device_id)

    def _get_swapped_vk(self, vk: int) -> int:
        """回傳交換後的虛擬鍵碼：Alt ↔ Win"""
        swap_map = {
            VK_LMENU: VK_LWIN,
            VK_RMENU: VK_RWIN,
            VK_LWIN:  VK_LMENU,
            VK_RWIN:  VK_RMENU,
        }
        return swap_map.get(vk, vk)

    def _send_key(self, vk: int, key_up: bool):
        """
        發送合成按鍵事件（使用 keybd_event）。
        dwExtraInfo 設為 INJECTED_EXTRA_INFO，讓 Hook 跳過此合成事件。
        """
        flags = KEYEVENTF_KEYUP if key_up else 0
        if vk in (VK_LWIN, VK_RWIN):
            flags |= KEYEVENTF_EXTENDEDKEY
        windll.user32.keybd_event(vk, 0, flags, INJECTED_EXTRA_INFO)
