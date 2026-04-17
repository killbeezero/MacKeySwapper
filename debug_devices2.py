# debug_devices2.py
# 用 SetupAPI CM_Get_Device_Property 查詢裝置顯示名稱
# 執行：python debug_devices2.py

import ctypes
import ctypes.wintypes as wintypes
import re
import winreg
from ctypes import windll, byref, sizeof, create_unicode_buffer

# SetupAPI
setupapi = ctypes.WinDLL("setupapi")
cfgmgr  = ctypes.WinDLL("cfgmgr32")

# DEVPKEY 常數（裝置屬性 GUID + PID）
class DEVPROPKEY(ctypes.Structure):
    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]

def make_devpropkey(data1, data2, data3, data4_bytes, pid):
    key = DEVPROPKEY()
    key.fmtid.Data1 = data1
    key.fmtid.Data2 = data2
    key.fmtid.Data3 = data3
    for i, b in enumerate(data4_bytes):
        key.fmtid.Data4[i] = b
    key.pid = pid
    return key

# DEVPKEY_Device_FriendlyName  {a45c254e-df1c-4efd-8020-67d146a850e0}, 14
DEVPKEY_Device_FriendlyName = make_devpropkey(
    0xa45c254e, 0xdf1c, 0x4efd,
    [0x80, 0x20, 0x67, 0xd1, 0x46, 0xa8, 0x50, 0xe0], 14
)
# DEVPKEY_Device_BusReportedDeviceDesc {540b947e-8b40-45bc-a8a2-6a0b894cbda2}, 4
DEVPKEY_Device_BusReportedDeviceDesc = make_devpropkey(
    0x540b947e, 0x8b40, 0x45bc,
    [0xa8, 0xa2, 0x6a, 0x0b, 0x89, 0x4c, 0xbd, 0xa2], 4
)
# DEVPKEY_NAME {b725f130-47ef-101a-a5f1-02608c9eebac}, 10
DEVPKEY_NAME = make_devpropkey(
    0xb725f130, 0x47ef, 0x101a,
    [0xa5, 0xf1, 0x02, 0x60, 0x8c, 0x9e, 0xeb, 0xac], 10
)

DEVPROP_TYPE_STRING = 0x00000012
CR_SUCCESS = 0


def cm_get_device_property(device_instance_id: str, prop_key) -> str | None:
    """用 CM_Get_DevNode_PropertyW 查詢裝置屬性"""
    # 先取得 DEVINST handle
    devinst = ctypes.c_ulong(0)
    ret = cfgmgr.CM_Locate_DevNodeW(
        byref(devinst),
        ctypes.c_wchar_p(device_instance_id),
        0
    )
    if ret != CR_SUCCESS:
        return None

    prop_type = ctypes.c_ulong(0)
    buf_size  = ctypes.c_ulong(0)

    # 先查大小
    cfgmgr.CM_Get_DevNode_PropertyW(
        devinst, byref(prop_key),
        byref(prop_type), None, byref(buf_size), 0
    )
    if buf_size.value == 0:
        return None

    buf = (ctypes.c_byte * buf_size.value)()
    ret = cfgmgr.CM_Get_DevNode_PropertyW(
        devinst, byref(prop_key),
        byref(prop_type), buf, byref(buf_size), 0
    )
    if ret != CR_SUCCESS:
        return None
    if prop_type.value != DEVPROP_TYPE_STRING:
        return None

    return ctypes.cast(buf, ctypes.c_wchar_p).value


# ── 列舉鍵盤 ──────────────────────────────────────────────────
RIM_TYPEKEYBOARD = 1
RIDI_DEVICENAME  = 0x20000007

class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [("hDevice", wintypes.HANDLE), ("dwType", wintypes.DWORD)]

def get_keyboards():
    count = wintypes.UINT(0)
    windll.user32.GetRawInputDeviceList(None, byref(count), sizeof(RAWINPUTDEVICELIST))
    lst = (RAWINPUTDEVICELIST * count.value)()
    windll.user32.GetRawInputDeviceList(lst, byref(count), sizeof(RAWINPUTDEVICELIST))
    results = []
    for item in lst:
        if item.dwType != RIM_TYPEKEYBOARD:
            continue
        size = wintypes.UINT(0)
        windll.user32.GetRawInputDeviceInfoW(item.hDevice, RIDI_DEVICENAME, None, byref(size))
        if not size.value:
            continue
        buf = create_unicode_buffer(size.value)
        windll.user32.GetRawInputDeviceInfoW(item.hDevice, RIDI_DEVICENAME, buf, byref(size))
        raw = buf.value
        path = re.sub(r'^[\\]{2}[?.]\\', '', raw).replace('#', '\\')
        path = re.sub(r'\\?\{[0-9A-Fa-f\-]+\}$', '', path).strip('\\')
        results.append(path)
    return results

print("=" * 70)
print("SetupAPI 裝置名稱診斷")
print("=" * 70)

for device_id in get_keyboards():
    print(f"\nDeviceID : {device_id}")
    for label, key in [
        ("FriendlyName",           DEVPKEY_Device_FriendlyName),
        ("BusReportedDeviceDesc",  DEVPKEY_Device_BusReportedDeviceDesc),
        ("NAME",                   DEVPKEY_NAME),
    ]:
        val = cm_get_device_property(device_id, key)
        print(f"  {label:30s} = {val}")

    # 往上查父節點（最多 5 層，直到找到有意義名稱）
    devinst = ctypes.c_ulong(0)
    ret = cfgmgr.CM_Locate_DevNodeW(byref(devinst), ctypes.c_wchar_p(device_id), 0)
    if ret == CR_SUCCESS:
        current = devinst
        depth = 0
        while depth < 5:
            parent = ctypes.c_ulong(0)
            ret2 = cfgmgr.CM_Get_Parent(byref(parent), current, 0)
            if ret2 != CR_SUCCESS:
                break
            depth += 1
            buf = create_unicode_buffer(512)
            buf_size = ctypes.c_ulong(512)
            cfgmgr.CM_Get_Device_IDW(parent, buf, buf_size, 0)
            parent_id = buf.value
            fname = cm_get_device_property(parent_id, DEVPKEY_Device_FriendlyName)
            bname = cm_get_device_property(parent_id, DEVPKEY_Device_BusReportedDeviceDesc)
            nname = cm_get_device_property(parent_id, DEVPKEY_NAME)
            print(f"  父節點[{depth}]: {parent_id}")
            print(f"    FriendlyName          = {fname}")
            print(f"    BusReportedDeviceDesc = {bname}")
            print(f"    NAME                  = {nname}")
            # 找到有意義的名稱就停止（排除通用描述）
            skip_words = {"service", "gatt", "profile", "isa", "pci", "acpi", "hid event", "converted"}
            useful_name = fname or bname or ""
            if useful_name and not any(w in useful_name.lower() for w in skip_words):
                print(f"    *** 找到有意義名稱: {useful_name} ***")
                break
            current = parent

print("\n" + "=" * 70)
print("診斷完成")
