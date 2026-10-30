"""Thin, read-only ctypes wrappers for the Windows APIs the scanner needs.

Every function here only *queries* state. Importing this module on non-Windows
platforms is safe; the functions simply return neutral values.
"""

import os
import sys

IS_WINDOWS = sys.platform == "win32"

# File attribute flags (winnt.h)
FILE_ATTRIBUTE_SPARSE_FILE = 0x200
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_ATTRIBUTE_COMPRESSED = 0x800
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000

PLACEHOLDER_ATTRS = (FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN
                     | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)
NEEDS_REAL_ALLOC = PLACEHOLDER_ATTRS | FILE_ATTRIBUTE_SPARSE_FILE | FILE_ATTRIBUTE_COMPRESSED

# Reparse tags
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003   # junctions and volume mount points
IO_REPARSE_TAG_SYMLINK = 0xA000000C
IO_REPARSE_TAG_DEDUP = 0x80000013
IO_REPARSE_TAG_WOF = 0x80000017           # compact OS / WOF-compressed files
IO_REPARSE_TAG_APPEXECLINK = 0x8000001B


def is_cloud_tag(tag):
    """OneDrive / cloud-files placeholders use IO_REPARSE_TAG_CLOUD_* (0x9000x01A)."""
    return (tag & 0xFFFF0FFF) == 0x9000001A


def is_data_tag(tag):
    """Reparse tags whose files hold real (possibly compressed) data on this volume."""
    return tag in (IO_REPARSE_TAG_DEDUP, IO_REPARSE_TAG_WOF) or is_cloud_tag(tag)


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    _k32.GetCompressedFileSizeW.restype = wintypes.DWORD
    _k32.GetDiskFreeSpaceW.argtypes = [wintypes.LPCWSTR] + [ctypes.POINTER(wintypes.DWORD)] * 4
    _k32.GetDiskFreeSpaceW.restype = wintypes.BOOL
    _k32.GetLogicalDrives.restype = wintypes.DWORD
    _k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    _k32.GetDriveTypeW.restype = wintypes.UINT
    _k32.GetVolumeInformationW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
        wintypes.DWORD]
    _k32.GetVolumeInformationW.restype = wintypes.BOOL


def long_path(path):
    """Prefix long absolute paths with \\\\?\\ so scandir/stat work past MAX_PATH."""
    if IS_WINDOWS and len(path) >= 240 and not path.startswith("\\\\?\\"):
        if path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + path[2:]
        return "\\\\?\\" + path
    return path


def compressed_size(path):
    """Actual on-disk bytes of a file (0 for dehydrated cloud placeholders). None on failure."""
    if not IS_WINDOWS:
        return None
    high = wintypes.DWORD(0)
    low = _k32.GetCompressedFileSizeW(long_path(path), ctypes.byref(high))
    if low == 0xFFFFFFFF and ctypes.get_last_error() != 0:
        return None
    return (high.value << 32) + low


def cluster_size(root):
    if not IS_WINDOWS:
        return 4096
    spc, bps, free, total = (wintypes.DWORD() for _ in range(4))
    ok = _k32.GetDiskFreeSpaceW(root, ctypes.byref(spc), ctypes.byref(bps),
                                ctypes.byref(free), ctypes.byref(total))
    if not ok or not spc.value or not bps.value:
        return 4096
    return spc.value * bps.value


DRIVE_TYPES = {0: "unknown", 1: "invalid", 2: "removable", 3: "fixed", 4: "network",
               5: "cdrom", 6: "ramdisk"}


def list_drives():
    """[(root, drive_type_name, label, fs_name)] for every logical drive."""
    if not IS_WINDOWS:
        return []
    out = []
    mask = _k32.GetLogicalDrives()
    for i in range(26):
        if not mask & (1 << i):
            continue
        root = chr(ord("A") + i) + ":\\"
        dtype = DRIVE_TYPES.get(_k32.GetDriveTypeW(root), "unknown")
        label_buf = ctypes.create_unicode_buffer(261)
        fs_buf = ctypes.create_unicode_buffer(261)
        label, fs = "", ""
        if dtype in ("fixed", "removable", "ramdisk"):
            if _k32.GetVolumeInformationW(root, label_buf, 261, None, None, None, fs_buf, 261):
                label, fs = label_buf.value, fs_buf.value
        out.append((root, dtype, label, fs))
    return out


def is_admin():
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def read_registry_values(key_path, hive="HKCU"):
    """Read all string values of a registry key (read-only). Returns {} on failure."""
    if not IS_WINDOWS:
        return {}
    import winreg
    root = winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    values = {}
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ) as key:
            i = 0
            while True:
                try:
                    name, data, _ = winreg.EnumValue(key, i)
                except OSError:
                    break
                if isinstance(data, str):
                    values[name] = os.path.expandvars(data)
                i += 1
    except OSError:
        pass
    return values


def read_registry_subkeys(key_path, hive="HKCU"):
    if not IS_WINDOWS:
        return []
    import winreg
    root = winreg.HKEY_CURRENT_USER if hive == "HKCU" else winreg.HKEY_LOCAL_MACHINE
    names = []
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ) as key:
            i = 0
            while True:
                try:
                    names.append(winreg.EnumKey(key, i))
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return names
