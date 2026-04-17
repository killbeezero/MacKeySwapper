# device.py
# 鍵盤裝置列舉與識別模組
# 透過 Win32 Raw Input API 列舉所有已連接的鍵盤裝置，
# 並從 Windows Registry 查詢對應的友善顯示名稱（如 "Cube Pocket Keyboard"）

import ctypes
import ctypes.wintypes as wintypes
import re
import winreg
from ctypes import windll, byref, sizeof, create_unicode_buffer
from typing import Optional

# ── Win32 常數 ────────────────────────────────────────────────
RIM_TYPEKEYBOARD = 1
RIDI_DEVICENAME  = 0x20000007

# ── Win32 結構定義 ────────────────────────────────────────────

class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [
        ("hDevice", wintypes.HANDLE),
        ("dwType",  wintypes.DWORD),
    ]


# ── SetupAPI / CfgMgr32 查詢裝置名稱 ─────────────────────────

_cfgmgr = ctypes.WinDLL("cfgmgr32")
CR_SUCCESS = 0

class _DEVPROPKEY(ctypes.Structure):
    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]
    _fields_ = [("fmtid", _GUID), ("pid", ctypes.c_ulong)]

def _make_devpropkey(d1, d2, d3, d4, pid):
    k = _DEVPROPKEY()
    k.fmtid.Data1 = d1; k.fmtid.Data2 = d2; k.fmtid.Data3 = d3
    for i, b in enumerate(d4):
        k.fmtid.Data4[i] = b
    k.pid = pid
    return k

# DEVPKEY_Device_FriendlyName {a45c254e-df1c-4efd-8020-67d146a850e0}, 14
_DEVPKEY_FriendlyName = _make_devpropkey(
    0xa45c254e, 0xdf1c, 0x4efd,
    [0x80, 0x20, 0x67, 0xd1, 0x46, 0xa8, 0x50, 0xe0], 14
)
# DEVPKEY_Device_BusReportedDeviceDesc {540b947e-8b40-45bc-a8a2-6a0b894cbda2}, 4
_DEVPKEY_BusDesc = _make_devpropkey(
    0x540b947e, 0x8b40, 0x45bc,
    [0xa8, 0xa2, 0x6a, 0x0b, 0x89, 0x4c, 0xbd, 0xa2], 4
)
DEVPROP_TYPE_STRING = 0x00000012

# 通用描述字串，出現時繼續往上找
_SKIP_WORDS = {"service", "gatt", "profile", "isa bridge", "acpi", "hid event",
               "converted", "bluetooth le service"}


def _cm_get_string_property(devinst: int, prop_key) -> Optional[str]:
    """用 CM_Get_DevNode_PropertyW 讀取字串屬性"""
    prop_type = ctypes.c_ulong(0)
    buf_size  = ctypes.c_ulong(0)
    _cfgmgr.CM_Get_DevNode_PropertyW(
        devinst, byref(prop_key), byref(prop_type), None, byref(buf_size), 0
    )
    if buf_size.value == 0:
        return None
    buf = (ctypes.c_byte * buf_size.value)()
    ret = _cfgmgr.CM_Get_DevNode_PropertyW(
        devinst, byref(prop_key), byref(prop_type), buf, byref(buf_size), 0
    )
    if ret != CR_SUCCESS or prop_type.value != DEVPROP_TYPE_STRING:
        return None
    return ctypes.cast(buf, ctypes.c_wchar_p).value


def _query_name_via_cfgmgr(device_id: str) -> Optional[str]:
    """
    用 CfgMgr32 API 從裝置節點往上追溯父節點，
    找到有意義的 FriendlyName 或 BusReportedDeviceDesc。

    診斷結果顯示藍牙鍵盤的名稱在第 2 層父節點
    BTHLE\DEV_{MAC}\ 的 FriendlyName 中。
    USB 鍵盤的名稱在第 1 層父節點的 BusReportedDeviceDesc。
    """
    devinst = ctypes.c_ulong(0)
    ret = _cfgmgr.CM_Locate_DevNodeW(
        byref(devinst), ctypes.c_wchar_p(device_id), 0
    )
    if ret != CR_SUCCESS:
        return None

    current = devinst
    for _ in range(5):
        parent = ctypes.c_ulong(0)
        if _cfgmgr.CM_Get_Parent(byref(parent), current, 0) != CR_SUCCESS:
            break

        fname = _cm_get_string_property(parent, _DEVPKEY_FriendlyName)
        bname = _cm_get_string_property(parent, _DEVPKEY_BusDesc)

        # FriendlyName 優先，再看 BusReportedDeviceDesc
        for candidate in (fname, bname):
            if not candidate:
                continue
            lower = candidate.lower()
            # 排除通用無意義描述
            if not any(w in lower for w in _SKIP_WORDS):
                return candidate

        current = parent

    return None


def _query_registry_friendly_name(device_id: str) -> Optional[str]:
    """
    查詢裝置顯示名稱，策略優先順序：
    1. CfgMgr32 父節點追溯（最準確）
    2. 主機碼 FriendlyName
    3. 主機碼 DeviceDesc 解析
    """
    name = _query_name_via_cfgmgr(device_id)
    if name:
        return name

    name = _query_enum_value(device_id, "FriendlyName")
    if name:
        return name

    desc = _query_enum_value_raw(device_id, "DeviceDesc")
    if desc:
        return _resolve_device_desc(desc)

    return None


def _resolve_device_desc(value: str) -> Optional[str]:
    """解析 @inf,...;實際名稱 格式，取分號後的文字"""
    if not value:
        return None
    if ';' in value:
        name = value.split(';')[-1].strip()
        return name if name else None
    if not value.startswith('@'):
        return value.strip()
    return None


def _query_enum_value(device_id: str, value_name: str) -> Optional[str]:
    value = _query_enum_value_raw(device_id, value_name)
    if value and not value.startswith('@'):
        return value.strip()
    return None


def _query_enum_value_raw(device_id: str, value_name: str) -> Optional[str]:
    reg_path = f"SYSTEM\\CurrentControlSet\\Enum\\{device_id}"
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_READ)
        value, _ = winreg.QueryValueEx(key, value_name)
        winreg.CloseKey(key)
        return str(value) if value else None
    except OSError:
        return None


# ── Raw Input 裝置路徑處理 ────────────────────────────────────

def _get_raw_device_name(handle: int) -> Optional[str]:
    """透過 Raw Input Handle 取得裝置路徑字串"""
    size = wintypes.UINT(0)
    windll.user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, byref(size))
    if size.value == 0:
        return None
    buf = create_unicode_buffer(size.value)
    windll.user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buf, byref(size))
    return buf.value


def _normalize_device_id(raw_path: str) -> str:
    """
    將 Raw Input 路徑（\\?\HID#VID_xxxx&PID_xxxx#...）
    正規化為 Registry 風格的 DeviceInstanceId（HID\VID_xxxx&PID_xxxx\...）
    """
    path = re.sub(r'^[\\]{2}[?.]\\', '', raw_path)
    path = path.replace('#', '\\')
    path = re.sub(r'\\?\{[0-9A-Fa-f\-]+\}$', '', path)
    return path.strip('\\')


def _extract_vid_pid(device_id: str):
    """從 DeviceInstanceId 解析 VID 與 PID，找不到時回傳 (None, None)"""
    match = re.search(r'VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})', device_id)
    if match:
        return match.group(1).upper(), match.group(2).upper()
    return None, None


def _is_virtual_device(device_id: str) -> bool:
    """
    判斷是否為虛擬/系統裝置，這類裝置不對應實體鍵盤，應過濾掉。
    例如：RDP 虛擬鍵盤、Terminal Server 鍵盤等。
    """
    virtual_patterns = [
        r'^TERMINPUT_BUS',
        r'RDP_KBD',
        r'VIRTUAL',
        r'^ROOT\\RDP',
    ]
    upper_id = device_id.upper()
    return any(re.search(p, upper_id) for p in virtual_patterns)


def _make_friendly_name(device_id: str, vid: Optional[str],
                         pid: Optional[str]) -> str:
    """
    組成裝置顯示名稱，優先順序：
    1. Registry 查詢到的真實名稱（如 "Cube Pocket Keyboard"）
    2. VID/PID 組合（如 "鍵盤 (VID_258A PID_8072)"）
    3. device_id 片段
    """
    # 優先從 Registry 查詢真實名稱
    registry_name = _query_registry_friendly_name(device_id)
    if registry_name:
        return registry_name

    # 次選：VID/PID 組合
    APPLE_VID = "05AC"
    if vid and pid:
        prefix = "Apple 鍵盤" if vid.upper() == APPLE_VID else "鍵盤"
        return f"{prefix} (VID_{vid} PID_{pid})"

    # Fallback：device_id 第二段
    parts = device_id.split("\\")
    return parts[1] if len(parts) > 1 else device_id


# ── 公開介面 ──────────────────────────────────────────────────

def enumerate_keyboards() -> list[dict]:
    """
    列舉目前系統中所有已連接的鍵盤裝置。
    虛擬裝置會自動過濾，每個實體裝置回傳：
        handle        (int) : Raw Input 裝置 Handle
        device_id     (str) : 正規化後的 DeviceInstanceId
        friendly_name (str) : 顯示名稱（優先使用 Registry 中的真實名稱）
        vid           (str) : Vendor ID
        pid           (str) : Product ID
    """
    count = wintypes.UINT(0)
    windll.user32.GetRawInputDeviceList(None, byref(count), sizeof(RAWINPUTDEVICELIST))
    if count.value == 0:
        return []

    device_list = (RAWINPUTDEVICELIST * count.value)()
    windll.user32.GetRawInputDeviceList(device_list, byref(count), sizeof(RAWINPUTDEVICELIST))

    keyboards = []
    for item in device_list:
        if item.dwType != RIM_TYPEKEYBOARD:
            continue

        raw_name = _get_raw_device_name(item.hDevice)
        if not raw_name:
            continue

        device_id = _normalize_device_id(raw_name)

        # 過濾虛擬裝置
        if _is_virtual_device(device_id):
            continue

        vid, pid = _extract_vid_pid(device_id)
        friendly_name = _make_friendly_name(device_id, vid, pid)

        keyboards.append({
            "handle":        item.hDevice,
            "device_id":     device_id,
            "friendly_name": friendly_name,
            "vid":           vid or "",
            "pid":           pid or "",
        })

    return keyboards


def get_handle_to_device_map() -> dict[int, dict]:
    """回傳以 Raw Input Handle 為 key 的鍵盤裝置字典"""
    return {kb["handle"]: kb for kb in enumerate_keyboards()}


def is_apple_keyboard(device_info: dict) -> bool:
    """判斷是否為 Apple 鍵盤（VID = 05AC）"""
    return device_info.get("vid", "").upper() == "05AC"
