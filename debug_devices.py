# debug_devices.py
# 診斷腳本：列出所有鍵盤的 device_id，並直接印出 Registry 中的原始資料
# 執行方式：python debug_devices.py > debug_output.txt
# 請將輸出結果貼給開發者分析

import ctypes
import ctypes.wintypes as wintypes
import re
import winreg
from ctypes import windll, byref, sizeof, create_unicode_buffer

RIM_TYPEKEYBOARD = 1
RIDI_DEVICENAME  = 0x20000007

class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]

def get_all_keyboard_raw_paths():
    count = wintypes.UINT(0)
    windll.user32.GetRawInputDeviceList(None, byref(count), sizeof(RAWINPUTDEVICELIST))
    device_list = (RAWINPUTDEVICELIST * count.value)()
    windll.user32.GetRawInputDeviceList(device_list, byref(count), sizeof(RAWINPUTDEVICELIST))

    results = []
    for item in device_list:
        if item.dwType != RIM_TYPEKEYBOARD:
            continue
        size = wintypes.UINT(0)
        windll.user32.GetRawInputDeviceInfoW(item.hDevice, RIDI_DEVICENAME, None, byref(size))
        if size.value == 0:
            continue
        buf = create_unicode_buffer(size.value)
        windll.user32.GetRawInputDeviceInfoW(item.hDevice, RIDI_DEVICENAME, buf, byref(size))
        raw = buf.value

        # 正規化
        path = re.sub(r'^[\\]{2}[?.]\\', '', raw)
        path = path.replace('#', '\\')
        path = re.sub(r'\\?\{[0-9A-Fa-f\-]+\}$', '', path)
        device_id = path.strip('\\')
        results.append((raw, device_id))
    return results

def dump_registry_key(reg_path, indent=0):
    """遞迴印出 Registry 機碼下的所有值（最多 2 層）"""
    prefix = "  " * indent
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_READ)
    except OSError:
        print(f"{prefix}[無法開啟] {reg_path}")
        return

    # 印出值
    i = 0
    while True:
        try:
            name, data, _ = winreg.EnumValue(key, i)
            # 只印出名稱相關的值
            if any(k in name.lower() for k in ("friendly", "desc", "name", "service", "class")):
                print(f"{prefix}  值: {name} = {data}")
            i += 1
        except OSError:
            break

    # 往下一層子機碼
    if indent < 1:
        j = 0
        while True:
            try:
                sub = winreg.EnumKey(key, j)
                print(f"{prefix}  [{sub}]")
                dump_registry_key(f"{reg_path}\\{sub}", indent + 1)
                j += 1
            except OSError:
                break

    winreg.CloseKey(key)

def search_bthenum(bt_addr):
    """在 BTHENUM 和 BTHLEDevice 下搜尋藍牙裝置，完整印出子機碼內容"""
    for root in ("BTHLEDevice", "BTHENUM"):
        base = f"SYSTEM\\CurrentControlSet\\Enum\\{root}"
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                    if bt_addr.upper() in sub.upper():
                        print(f"    → [{root}\\{sub}]")
                        sub_path = f"{base}\\{sub}"
                        # 印出此層所有值
                        try:
                            sk = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_READ)
                            vi = 0
                            while True:
                                try:
                                    n, d, _ = winreg.EnumValue(sk, vi)
                                    print(f"         {n} = {d}")
                                    vi += 1
                                except OSError:
                                    break
                            # 再往下一層（Instance 層）
                            ki = 0
                            while True:
                                try:
                                    inst = winreg.EnumKey(sk, ki)
                                    inst_path = f"{sub_path}\\{inst}"
                                    print(f"      [{inst}]")
                                    try:
                                        ik = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, inst_path, 0, winreg.KEY_READ)
                                        ii = 0
                                        while True:
                                            try:
                                                n, d, _ = winreg.EnumValue(ik, ii)
                                                print(f"           {n} = {d}")
                                                ii += 1
                                            except OSError:
                                                break
                                        winreg.CloseKey(ik)
                                    except OSError:
                                        pass
                                    ki += 1
                                except OSError:
                                    break
                            winreg.CloseKey(sk)
                        except OSError:
                            pass
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(key)
        except OSError:
            pass

print("=" * 70)
print("MacKeySwapper 裝置診斷報告")
print("=" * 70)

devices = get_all_keyboard_raw_paths()
for raw, device_id in devices:
    print(f"\n{'─'*70}")
    print(f"原始路徑  : {raw}")
    print(f"DeviceID  : {device_id}")

    # 查詢主機碼
    reg_path = f"SYSTEM\\CurrentControlSet\\Enum\\{device_id}"
    print(f"Registry  : HKLM\\{reg_path}")
    dump_registry_key(reg_path)

    # 若含藍牙位址，額外搜尋 BTHENUM
    addr = re.search(r'[_&]([0-9A-Fa-f]{12})(?:[_&]|$)', device_id)
    if addr:
        print(f"  藍牙位址: {addr.group(1)}，搜尋 BTHENUM/BTHLEDevice...")
        search_bthenum(addr.group(1))

print(f"\n{'='*70}")
print("ContainerID 診斷")
print("=" * 70)

# 收集所有出現過的 ContainerID
container_ids = set()
for raw, device_id in devices:
    addr_match = re.search(r'_([0-9A-Fa-f]{12})(?:&|$)', device_id)
    if not addr_match:
        continue
    bt_addr = addr_match.group(1).upper()
    for enum_root in ("BTHLEDevice", "BTHENUM"):
        base = f"SYSTEM\\CurrentControlSet\\Enum\\{enum_root}"
        try:
            base_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0, winreg.KEY_READ)
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(base_key, i); i += 1
                except OSError:
                    break
                if bt_addr not in sub.upper():
                    continue
                sub_path = f"{base}\\{sub}"
                try:
                    sk = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub_path, 0, winreg.KEY_READ)
                    j = 0
                    while True:
                        try:
                            inst = winreg.EnumKey(sk, j); j += 1
                        except OSError:
                            break
                        try:
                            ik = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{sub_path}\\{inst}", 0, winreg.KEY_READ)
                            try:
                                cid, _ = winreg.QueryValueEx(ik, "ContainerID")
                                container_ids.add(str(cid))
                                print(f"  找到 ContainerID: {cid}  (來自 {bt_addr})")
                            except OSError:
                                pass
                            winreg.CloseKey(ik)
                        except OSError:
                            pass
                    winreg.CloseKey(sk)
                except OSError:
                    pass
            winreg.CloseKey(base_key)
        except OSError:
            pass

print(f"\n共找到 {len(container_ids)} 個 ContainerID，逐一查詢 DeviceContainers：")
for cid in container_ids:
    cid_fmt = cid if cid.startswith('{') else '{' + cid + '}'
    print(f"\n  ContainerID: {cid_fmt}")
    container_path = f"SYSTEM\\CurrentControlSet\\Control\\DeviceContainers\\{cid_fmt}"
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, container_path, 0, winreg.KEY_READ)
        print(f"  路徑存在：HKLM\\{container_path}")
        vi = 0
        while True:
            try:
                n, d, _ = winreg.EnumValue(key, vi); vi += 1
                print(f"    {n} = {d}")
            except OSError:
                break
        # 列出子機碼
        ki = 0
        while True:
            try:
                sub = winreg.EnumKey(key, ki); ki += 1
                print(f"    [{sub}]")
                try:
                    sk = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{container_path}\\{sub}", 0, winreg.KEY_READ)
                    si = 0
                    while True:
                        try:
                            n, d, _ = winreg.EnumValue(sk, si); si += 1
                            print(f"       {n} = {d}")
                        except OSError:
                            break
                    winreg.CloseKey(sk)
                except OSError:
                    pass
            except OSError:
                break
        winreg.CloseKey(key)
    except OSError:
        print(f"  [無法開啟] HKLM\\{container_path}")

print(f"\n{'='*70}")
print("診斷完成")
