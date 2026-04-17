# main.py
# 程式進入點
# 負責環境檢查、例外處理，以及啟動 TrayApp

import sys
import os
import traceback

def _check_platform():
    """確認執行平台為 Windows"""
    if sys.platform != "win32":
        print("錯誤：MacKeySwapper 僅支援 Windows 10 / 11")
        sys.exit(1)

def _check_dependencies():
    """確認必要套件已安裝"""
    missing = []
    try:
        import pystray
    except ImportError:
        missing.append("pystray")
    try:
        from PIL import Image
    except ImportError:
        missing.append("Pillow")
    try:
        import win32api
    except ImportError:
        missing.append("pywin32")

    if missing:
        print(f"錯誤：缺少必要套件，請執行：pip install {' '.join(missing)}")
        sys.exit(1)

def main():
    _check_platform()
    _check_dependencies()

    # 將程式目錄加入 sys.path，確保模組可以互相匯入
    app_dir = os.path.dirname(os.path.abspath(__file__))
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

    try:
        from tray import TrayApp
        app = TrayApp()
        app.run()
    except Exception as e:
        # 將未預期的例外寫入 log 檔，方便除錯
        log_path = os.path.join(app_dir, "error.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*50}\n")
            traceback.print_exc(file=f)
        print(f"程式發生未預期錯誤，詳情請查看：{log_path}")
        raise

if __name__ == "__main__":
    main()
