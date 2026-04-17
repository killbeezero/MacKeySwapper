# startup.py
# 開機自動啟動管理模組
# 透過寫入 Windows Registry 的 Run Key 實現開機啟動

import os
import sys
import winreg

# Registry 路徑：目前使用者的開機啟動項目（不需管理員權限）
REGISTRY_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "MacKeySwapper"


def _get_exe_path() -> str:
    """
    取得程式的執行路徑。
    - 若以 PyInstaller 打包成 .exe，回傳 .exe 路徑
    - 若直接以 python 執行，回傳 `pythonw.exe main.py` 的完整命令
    """
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包後，sys.executable 就是 .exe
        return f'"{sys.executable}"'
    else:
        # 開發模式：使用 pythonw（不顯示主控台視窗）
        python_dir = os.path.dirname(sys.executable)
        pythonw = os.path.join(python_dir, "pythonw.exe")
        main_script = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "main.py")
        )
        return f'"{pythonw}" "{main_script}"'


def is_enabled() -> bool:
    """
    查詢目前是否已設定開機自動啟動。
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_KEY_PATH,
            0,
            winreg.KEY_READ
        )
        value, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable() -> bool:
    """
    將程式加入開機啟動。
    成功回傳 True，失敗回傳 False。
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE
        )
        exe_path = _get_exe_path()
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, exe_path)
        winreg.CloseKey(key)
        print(f"[Startup] 已加入開機啟動：{exe_path}")
        return True
    except OSError as e:
        print(f"[Startup] 加入開機啟動失敗：{e}")
        return False


def disable() -> bool:
    """
    將程式從開機啟動移除。
    成功或項目本不存在均回傳 True，失敗回傳 False。
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_KEY_PATH,
            0,
            winreg.KEY_SET_VALUE
        )
        try:
            winreg.DeleteValue(key, APP_NAME)
            print("[Startup] 已移除開機啟動")
        except FileNotFoundError:
            pass   # 原本就不存在，視為成功
        winreg.CloseKey(key)
        return True
    except OSError as e:
        print(f"[Startup] 移除開機啟動失敗：{e}")
        return False


def sync(enabled: bool) -> bool:
    """
    依 enabled 同步開機啟動狀態。
    enabled=True 時呼叫 enable()，False 時呼叫 disable()。
    """
    return enable() if enabled else disable()
