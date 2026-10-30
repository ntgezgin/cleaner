"""Detect the operating system, disks, well-known folders and cloud-sync roots.

Everything here is read-only: environment variables, the registry (Windows),
~/.config/user-dirs.dirs (Linux), /proc/self/mounts and statvfs.
"""

import getpass
import glob
import json
import os
import platform
import re
import shutil
import socket
import sys

from . import winapi

if sys.platform == "win32":
    OS = "windows"
elif sys.platform == "darwin":
    OS = "macos"
else:
    OS = "linux"

CASE_INSENSITIVE = OS in ("windows", "macos")


def norm(path):
    """Normalized key for path comparisons (case-folded on Windows/macOS)."""
    p = os.path.normpath(os.path.abspath(path))
    if OS == "windows":
        p = p.replace("/", "\\")
    return p.casefold() if CASE_INSENSITIVE else p


def is_under(path_key, root_key):
    """True if normalized path_key equals or is inside normalized root_key."""
    if path_key == root_key:
        return True
    root = root_key if root_key.endswith(os.sep) else root_key + os.sep
    return path_key.startswith(root)


def _env_path(name):
    v = os.environ.get(name)
    return os.path.normpath(v) if v else None


# Linux pseudo / virtual file systems that are never scanned.
PSEUDO_FS = {
    "proc", "sysfs", "devtmpfs", "devpts", "tmpfs", "securityfs", "cgroup", "cgroup2",
    "pstore", "efivarfs", "bpf", "autofs", "mqueue", "hugetlbfs", "debugfs", "tracefs",
    "fusectl", "configfs", "binfmt_misc", "rpc_pipefs", "nsfs", "overlay", "squashfs",
    "ramfs", "selinuxfs", "devfs", "nfsd", "fuse.portal", "fuse.gvfsd-fuse",
}
REAL_FS = {
    "ext2", "ext3", "ext4", "btrfs", "xfs", "zfs", "f2fs", "jfs", "reiserfs", "bcachefs",
    "ntfs", "ntfs3", "fuseblk", "vfat", "exfat", "msdos", "hfsplus", "apfs", "9p", "virtiofs",
}
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "sshfs", "fuse.sshfs", "fuse.rclone",
              "davfs", "fuse.s3fs", "afs"}


def _unescape_mount(s):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def linux_mounts():
    out = []
    try:
        with open("/proc/self/mounts", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 3:
                    out.append((_unescape_mount(parts[1]), parts[2], _unescape_mount(parts[0])))
    except OSError:
        pass
    return out


def disk_usage(path):
    try:
        u = shutil.disk_usage(path)
        return u.total, u.used, u.free
    except OSError:
        return None


class Environment:
    """Facts about the machine the scan runs on."""

    def __init__(self):
        self.os = OS
        self.home = os.path.normpath(os.path.expanduser("~"))
        self.host = socket.gethostname()
        try:
            self.user = getpass.getuser()
        except Exception:
            self.user = os.environ.get("USERNAME") or os.environ.get("USER") or "?"
        self.os_detail = "%s %s (%s)" % (platform.system(), platform.release(), platform.machine())
        if OS == "macos":
            self.os_detail = "macOS %s (%s)" % (platform.mac_ver()[0], platform.machine())
        elif OS == "windows":
            self.os_detail = "Windows %s %s (%s)" % (platform.release(), platform.version(),
                                                      platform.machine())
        self.python = platform.python_version()
        self.is_admin = winapi.is_admin()
        self.anchors = self._anchors()
        self.known = self._known_folders()
        self.cloud_roots = self._cloud_roots()
        self.disks = self._disks()

    # ------------------------------------------------------------------ anchors
    def _anchors(self):
        h = self.home
        a = {"HOME": h}
        if OS == "windows":
            a["LOCALAPPDATA"] = _env_path("LOCALAPPDATA") or os.path.join(h, "AppData", "Local")
            a["APPDATA"] = _env_path("APPDATA") or os.path.join(h, "AppData", "Roaming")
            a["LOCALLOW"] = os.path.join(os.path.dirname(a["LOCALAPPDATA"]), "LocalLow")
            a["TEMP"] = _env_path("TEMP") or os.path.join(a["LOCALAPPDATA"], "Temp")
            a["PROGRAMDATA"] = _env_path("PROGRAMDATA") or "C:\\ProgramData"
            a["WINDIR"] = _env_path("WINDIR") or _env_path("SystemRoot") or "C:\\Windows"
            sysdrive = os.environ.get("SystemDrive", "C:")
            a["SYSDRIVE"] = sysdrive.rstrip("\\") + "\\"
            a["PROGRAMFILES"] = _env_path("ProgramFiles") or a["SYSDRIVE"] + "Program Files"
            a["PROGRAMFILES86"] = (_env_path("ProgramFiles(x86)")
                                   or a["SYSDRIVE"] + "Program Files (x86)")
        elif OS == "macos":
            a["LIBRARY"] = os.path.join(h, "Library")
            a["CACHES"] = os.path.join(h, "Library", "Caches")
            a["APPSUPPORT"] = os.path.join(h, "Library", "Application Support")
            tmp = _env_path("TMPDIR")
            if tmp and tmp.startswith("/var/folders"):
                tmp = "/private" + tmp
            a["TEMP"] = tmp or "/tmp"
        else:
            a["XDG_CACHE"] = _env_path("XDG_CACHE_HOME") or os.path.join(h, ".cache")
            a["XDG_CONFIG"] = _env_path("XDG_CONFIG_HOME") or os.path.join(h, ".config")
            a["XDG_DATA"] = _env_path("XDG_DATA_HOME") or os.path.join(h, ".local", "share")
            a["TEMP"] = "/tmp"
        return a

    # ------------------------------------------------------------ known folders
    def _known_folders(self):
        """Personal folders, keyed by role. Values are lists of absolute paths."""
        h = self.home
        k = {
            "desktop": [os.path.join(h, "Desktop")],
            "documents": [os.path.join(h, "Documents")],
            "downloads": [os.path.join(h, "Downloads")],
            "pictures": [os.path.join(h, "Pictures")],
            "videos": [os.path.join(h, "Videos"), os.path.join(h, "Movies")],
            "music": [os.path.join(h, "Music")],
        }
        if OS == "windows":
            vals = winapi.read_registry_values(
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
            mapping = {
                "Desktop": "desktop", "Personal": "documents", "My Pictures": "pictures",
                "My Video": "videos", "My Music": "music",
                "{374DE290-123F-4565-9164-39C4925E467B}": "downloads",
            }
            for reg_name, role in mapping.items():
                v = vals.get(reg_name)
                if v and v not in k[role]:
                    k[role].append(os.path.normpath(v))
        elif OS == "linux":
            # XDG user dirs are localized (e.g. "Belgeler", "Resimler", "İndirilenler").
            cfg = os.path.join(self.anchors["XDG_CONFIG"], "user-dirs.dirs")
            mapping = {"XDG_DESKTOP_DIR": "desktop", "XDG_DOCUMENTS_DIR": "documents",
                       "XDG_DOWNLOAD_DIR": "downloads", "XDG_PICTURES_DIR": "pictures",
                       "XDG_VIDEOS_DIR": "videos", "XDG_MUSIC_DIR": "music"}
            try:
                with open(cfg, encoding="utf-8") as fh:
                    for line in fh:
                        m = re.match(r'\s*(XDG_\w+_DIR)\s*=\s*"(.*)"', line)
                        if m and m.group(1) in mapping:
                            p = m.group(2).replace("$HOME", h)
                            if os.path.normpath(p) != h:
                                k[mapping[m.group(1)]].append(os.path.normpath(p))
            except OSError:
                pass
        for role in k:
            seen, uniq = set(), []
            for p in k[role]:
                if norm(p) not in seen:
                    seen.add(norm(p))
                    uniq.append(p)
            k[role] = uniq
        return k

    # -------------------------------------------------------------- cloud roots
    def _cloud_roots(self):
        """Folders synchronized with a cloud service: deleting locally deletes remotely."""
        h = self.home
        roots = []

        def add(path, provider):
            if path and os.path.isdir(path) and all(norm(path) != norm(r[0]) for r in roots):
                roots.append((os.path.normpath(path), provider))

        for pattern, provider in [
            ("OneDrive*", "OneDrive"), ("Dropbox*", "Dropbox"), ("Google Drive*", "Google Drive"),
            ("My Drive", "Google Drive"), ("GoogleDrive*", "Google Drive"),
            ("iCloudDrive", "iCloud"), ("iCloud Drive*", "iCloud"), ("iCloud Photos", "iCloud"),
            ("Box", "Box"), ("Box Sync", "Box"), ("MEGA", "MEGA"), ("MEGAsync", "MEGA"),
            ("pCloudDrive", "pCloud"), ("pCloud Drive", "pCloud"), ("YandexDisk*", "Yandex Disk"),
            ("Nextcloud*", "Nextcloud"), ("ownCloud*", "ownCloud"), ("Sync", "Sync.com"),
            ("SynologyDrive*", "Synology Drive"), ("Insync", "Insync"),
        ]:
            for p in glob.glob(os.path.join(glob.escape(h), pattern)):
                add(p, provider)
        for p in glob.glob(os.path.join(glob.escape(os.path.join(h, "Pictures")), "iCloud Photos")):
            add(p, "iCloud")
        if OS == "windows":
            for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
                add(_env_path(var), "OneDrive")
            for sub in winapi.read_registry_subkeys(r"Software\Microsoft\OneDrive\Accounts"):
                v = winapi.read_registry_values(r"Software\Microsoft\OneDrive\Accounts\\" + sub)
                add(v.get("UserFolder"), "OneDrive")
            # Dropbox writes its sync locations to info.json
            for base in (self.anchors["LOCALAPPDATA"], self.anchors["APPDATA"]):
                info = os.path.join(base, "Dropbox", "info.json")
                try:
                    with open(info, encoding="utf-8") as fh:
                        for acct in json.load(fh).values():
                            if isinstance(acct, dict):
                                add(acct.get("path"), "Dropbox")
                except (OSError, ValueError):
                    pass
        elif OS == "macos":
            add(os.path.join(h, "Library", "CloudStorage"), "CloudStorage (OneDrive/Google/Dropbox…)")
            add(os.path.join(h, "Library", "Mobile Documents"), "iCloud Drive")
            # "Desktop & Documents" in iCloud: the real folders live in iCloud Drive.
            icloud_docs = os.path.join(h, "Library", "Mobile Documents", "com~apple~CloudDocs")
            if os.path.isdir(os.path.join(icloud_docs, "Desktop")):
                add(os.path.join(h, "Desktop"), "iCloud (Desktop & Documents)")
            if os.path.isdir(os.path.join(icloud_docs, "Documents")):
                add(os.path.join(h, "Documents"), "iCloud (Desktop & Documents)")
        return roots

    # -------------------------------------------------------------------- disks
    def _disks(self):
        disks = []
        if OS == "windows":
            for root, dtype, label, fs in winapi.list_drives():
                d = {"mount": root, "type": dtype, "label": label, "fs": fs}
                if dtype in ("fixed", "removable", "ramdisk"):
                    u = disk_usage(root)
                    if u:
                        d["total"], d["used"], d["free"] = u
                if "google drive" in label.lower():  # Drive for desktop's virtual drive
                    d["type"] = "cloud"
                disks.append(d)
        elif OS == "macos":
            seen_dev = set()
            for mount in ["/"] + sorted(glob.glob("/Volumes/*")):
                try:
                    if os.path.islink(mount):
                        continue
                    dev = os.stat(mount).st_dev
                except OSError:
                    continue
                if dev in seen_dev:
                    continue
                seen_dev.add(dev)
                d = {"mount": mount, "type": "fixed" if mount == "/" else "volume",
                     "label": os.path.basename(mount) or "Macintosh HD", "fs": "apfs" if mount == "/" else ""}
                u = disk_usage(mount)
                if u:
                    d["total"], d["used"], d["free"] = u
                disks.append(d)
        else:
            seen_src = set()
            for mount, fstype, src in linux_mounts():
                if fstype in PSEUDO_FS or fstype not in REAL_FS | NETWORK_FS:
                    continue
                if mount.startswith(("/proc", "/sys", "/dev", "/run", "/snap", "/var/lib/docker",
                                     "/var/snap")):
                    continue
                d = {"mount": mount, "fs": fstype, "label": src,
                     "type": "network" if fstype in NETWORK_FS else "fixed"}
                key = (src, fstype)
                if fstype not in NETWORK_FS and key in seen_src and src.startswith("/dev/"):
                    # bind mount / btrfs subvolume of an already listed device
                    d["type"] = "bind"
                seen_src.add(key)
                u = disk_usage(mount)
                if u:
                    d["total"], d["used"], d["free"] = u
                disks.append(d)
        return disks

    def disk_of(self, path):
        """Index of the disk (longest mount prefix) that holds path, or -1."""
        k = norm(path)
        best, best_len = -1, -1
        for i, d in enumerate(self.disks):
            if d.get("type") in ("bind",):
                continue
            mk = norm(d["mount"])
            if is_under(k, mk) and len(mk) > best_len:
                best, best_len = i, len(mk)
        return best

    # --------------------------------------------------------------- scan roots
    def default_roots(self, home_only=False, all_drives=False):
        """[(path, label)] in scan order: home first, then other areas/drives."""
        roots = [(self.home, "home")]
        if home_only:
            return roots
        if OS == "windows":
            for d in self.disks:
                if d["type"] == "fixed" or (all_drives and d["type"] in ("removable", "ramdisk")):
                    roots.append((d["mount"], "drive"))
        elif OS == "macos":
            for p in ("/Applications", "/Library", "/opt", "/usr/local", self.anchors.get("TEMP")):
                if p and os.path.isdir(p) and not os.path.islink(p):
                    roots.append((p, "system" if p != self.anchors.get("TEMP") else "temp"))
            for d in self.disks:
                if d["mount"] != "/":
                    roots.append((d["mount"], "volume"))
        else:
            for d in self.disks:
                if d["type"] == "fixed":
                    roots.append((d["mount"], "mount"))
        out, seen = [], set()
        for p, label in roots:
            if norm(p) not in seen:
                seen.add(norm(p))
                out.append((p, label))
        return out

    def to_dict(self):
        return {
            "os": self.os, "os_detail": self.os_detail, "host": self.host, "user": self.user,
            "home": self.home, "python": self.python, "is_admin": self.is_admin,
            "anchors": self.anchors, "known_folders": self.known,
            "cloud_roots": [{"path": p, "provider": prov} for p, prov in self.cloud_roots],
            "sep": os.sep,
        }
