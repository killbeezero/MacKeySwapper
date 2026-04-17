# config.py
# 設定檔讀寫模組
# 負責載入、儲存、查詢 config.json 中的鍵盤設定資料

import json
import os
from datetime import datetime
from typing import Optional

# 設定檔預設存放於程式所在目錄
CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# 預設設定結構
DEFAULT_CONFIG = {
    "startup": False,       # 是否開機自動啟動
    "keyboards": []         # 已記錄的鍵盤清單
}

# 單一鍵盤設定的預設結構
DEFAULT_KEYBOARD = {
    "device_id": "",        # HID DeviceInstanceId（唯一識別碼）
    "friendly_name": "",    # 顯示名稱（供使用者辨識）
    "mac_mode": False,      # 是否啟用 Alt ↔ Win 交換
    "last_seen": ""         # 最後一次偵測到的時間（ISO 8601）
}


def load() -> dict:
    """
    從磁碟載入設定檔。
    若檔案不存在，自動建立預設設定並儲存。
    """
    if not os.path.exists(CONFIG_PATH):
        save(DEFAULT_CONFIG.copy())
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 補齊可能缺少的頂層欄位（向後相容）
        for key, val in DEFAULT_CONFIG.items():
            if key not in data:
                data[key] = val
        return data
    except (json.JSONDecodeError, IOError) as e:
        print(f"[Config] 載入設定檔失敗：{e}，使用預設值")
        return DEFAULT_CONFIG.copy()


def save(config: dict) -> None:
    """
    將設定寫入磁碟。
    """
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"[Config] 儲存設定檔失敗：{e}")


def get_keyboard(config: dict, device_id: str) -> Optional[dict]:
    """
    依 device_id 查詢鍵盤設定。
    找不到時回傳 None。
    """
    for kb in config.get("keyboards", []):
        if kb.get("device_id") == device_id:
            return kb
    return None


def get_keyboard_by_vid_pid(config: dict, vid: str, pid: str) -> Optional[dict]:
    """
    以 VID+PID 模糊比對查詢鍵盤設定。
    用於藍牙裝置重新配對後 DeviceInstanceId 尾段改變的情況。
    """
    pattern = f"VID_{vid.upper()}&PID_{pid.upper()}"
    for kb in config.get("keyboards", []):
        if pattern in kb.get("device_id", "").upper():
            return kb
    return None


def upsert_keyboard(config: dict, device_id: str, friendly_name: str,
                    mac_mode: Optional[bool] = None) -> dict:
    """
    新增或更新鍵盤設定。
    - 若 device_id 已存在：更新 friendly_name、last_seen，
      若 mac_mode 有傳入則一併更新。
    - 若不存在：新增一筆預設設定。
    回傳更新後的鍵盤設定 dict。
    """
    now = datetime.now().isoformat(timespec="seconds")
    kb = get_keyboard(config, device_id)

    if kb is None:
        # 新增
        kb = {
            **DEFAULT_KEYBOARD,
            "device_id": device_id,
            "friendly_name": friendly_name,
            "mac_mode": mac_mode if mac_mode is not None else False,
            "last_seen": now
        }
        config["keyboards"].append(kb)
    else:
        # 更新
        kb["friendly_name"] = friendly_name
        kb["last_seen"] = now
        if mac_mode is not None:
            kb["mac_mode"] = mac_mode

    save(config)
    return kb


def set_mac_mode(config: dict, device_id: str, enabled: bool) -> bool:
    """
    設定指定鍵盤的 Mac 模式開關。
    回傳 True 表示成功，False 表示找不到該裝置。
    """
    kb = get_keyboard(config, device_id)
    if kb is None:
        print(f"[Config] 找不到裝置：{device_id}")
        return False
    kb["mac_mode"] = enabled
    save(config)
    return True


def is_mac_mode(config: dict, device_id: str) -> bool:
    """
    查詢指定鍵盤是否啟用 Mac 模式。
    找不到裝置時預設回傳 False。
    """
    kb = get_keyboard(config, device_id)
    if kb is None:
        return False
    return kb.get("mac_mode", False)


def set_startup(config: dict, enabled: bool) -> None:
    """
    設定是否開機自動啟動，並儲存。
    """
    config["startup"] = enabled
    save(config)


def get_all_keyboards(config: dict) -> list:
    """
    回傳所有已記錄鍵盤的清單（副本）。
    """
    return list(config.get("keyboards", []))
