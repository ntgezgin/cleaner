"""Read-only directory tree scanner.

* Uses os.scandir (one directory listing per folder; on Windows the size/attributes
  come straight from the listing without opening files).
* Never follows symlinks, junctions or other directory reparse points.
* Does not cross into other file systems (they are scanned as their own roots).
* De-duplicates hard links (POSIX: every file; Windows: files >= 256 KiB).
* Measures allocated (on-disk) size separately from apparent size, so cloud
  "files on demand" placeholders count as ~0 bytes on disk.
"""

import os
import stat as stat_mod
import sys
import time

from . import platform_info, winapi
from .platform_info import norm

IS_WINDOWS = sys.platform == "win32"

# Names (lower-case) of files whose presence is recorded as evidence on their folder.
MARKER_FILES = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile",
    "cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
    "settings.gradle.kts", "cmakelists.txt", "makefile", "go.mod", "pubspec.yaml", "podfile",
    "angular.json", "pyvenv.cfg", "composer.json", "gemfile", "mix.exs", "stack.yaml",
    "cabal.project", "build.zig", "deno.json", "meson.build", "conanfile.txt", "vcpkg.json",
    "platformio.ini", ".project", ".cproject", "package.swift", "environment.yml",
    ".git", "tox.ini",
}
MARKER_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".sln", ".tf", ".xcodeproj", ".cabal",
                   ".ioc", ".uvprojx", ".xpr", ".uproject")
MARKER_DIRS = {".git", "conda-meta", ".hg", ".svn", ".idea", ".vscode", "projectsettings"}

# Files that are always recorded regardless of size (system files worth explaining).
SPECIAL_FILES = {"hiberfil.sys", "pagefile.sys", "swapfile.sys", "memory.dmp"}

BIG_FILE_BYTES = 10 * 1024 * 1024
WIN_HARDLINK_CHECK_BYTES = 256 * 1024
OLD_DAYS = 7


class Node(object):
    __slots__ = ("name", "path", "parent", "children", "depth", "alloc", "size", "files",
                 "dirs", "mtime", "atime", "own_alloc", "old_alloc", "prot", "flags",
                 "state", "repos", "cat", "lock", "item", "is_root", "label")

    def __init__(self, name, path, parent, depth):
        self.name = name
        self.path = path
        self.parent = parent
        self.children = []
        self.depth = depth
        self.alloc = 0       # on-disk bytes (subtree, after aggregation)
        self.size = 0        # apparent bytes (subtree)
        self.files = 0
        self.dirs = 0
        self.mtime = 0.0     # newest modification time in subtree
        self.atime = 0.0     # newest access time in subtree (unreliable on many systems)
        self.own_alloc = 0   # bytes of files directly in this folder
        self.old_alloc = 0   # bytes of direct files older than OLD_DAYS
        self.prot = 0        # bytes of "valuable" files (datasets, weights, media…) in subtree
        self.flags = None    # set of marker names found directly in this folder
        self.state = None    # None | "denied" | "error" | "skipped"
        self.repos = 0       # number of git repositories in subtree
        self.cat = "u"
        self.lock = None
        self.item = None
        self.is_root = False
        self.label = ""

    def has(self, marker):
        return self.flags is not None and marker in self.flags


class ScanResult(object):
    def __init__(self):
        self.roots = []          # list[Node]
        self.nodes = []          # every directory node, creation order
        self.big_files = []      # (alloc, size, path, mtime, atime, node)
        self.special_files = []  # (alloc, size, path, mtime, atime, node)
        self.not_scanned = []    # {"path", "reason"}
        self.links_skipped = 0
        self.link_samples = []
        self.reparse_skipped = 0
        self.reparse_samples = []
        self.mounts_skipped = []
        self.hardlink_dupes = 0
        self.hardlink_dupe_bytes = 0
        self.cloud_apparent = 0  # apparent bytes of cloud placeholders
        self.cloud_alloc = 0
        self.file_errors = 0
        self.total_files = 0
        self.seconds = 0.0


class Scanner(object):
    def __init__(self, valuable_ext, skip_paths=(), progress=None, old_days=OLD_DAYS):
        self.valuable_ext = valuable_ext
        self.skip_keys = set(norm(p) for p in skip_paths)
        self.progress = progress
        self.old_cutoff = time.time() - old_days * 86400
        self.result = ScanResult()
        self._seen_inodes = set()
        self._seen_dirs = set()
        self._cluster = {}
        self._last_progress = 0.0

    # ------------------------------------------------------------------ helpers
    def _cluster_for(self, path):
        drive = os.path.splitdrive(path)[0] + "\\"
        c = self._cluster.get(drive)
        if c is None:
            c = self._cluster[drive] = winapi.cluster_size(drive)
        return c

    def _not_scanned(self, path, reason):
        self.result.not_scanned.append({"path": path, "reason": reason})

    def _tick(self, path):
        now = time.time()
        if self.progress and now - self._last_progress > 1.0:
            self._last_progress = now
            self.progress(len(self.result.nodes), self.result.total_files, path)

    # --------------------------------------------------------------------- scan
    def scan_root(self, path, label, extra_skip=()):
        """Scan one root. Other roots / skip paths inside it are not entered."""
        path = os.path.normpath(path)
        res = self.result
        root = Node(path, path, None, 0)
        root.is_root = True
        root.label = label
        try:
            st = os.stat(winapi.long_path(path), follow_symlinks=False)
        except OSError as exc:
            root.state = "error"
            self._not_scanned(path, "erişilemedi: %s" % exc.strerror)
            res.roots.append(root)
            res.nodes.append(root)
            return root
        if stat_mod.S_ISLNK(st.st_mode):
            root.state = "skipped"
            self._not_scanned(path, "sembolik bağlantı (takip edilmedi)")
            res.roots.append(root)
            res.nodes.append(root)
            return root
        root_dev = st.st_dev
        skip = self.skip_keys | set(norm(p) for p in extra_skip)
        res.roots.append(root)
        res.nodes.append(root)
        if not IS_WINDOWS:
            self._seen_dirs.add((st.st_dev, st.st_ino))
        t0 = time.time()
        stack = [root]
        valuable = self.valuable_ext
        old_cutoff = self.old_cutoff
        big = BIG_FILE_BYTES
        while stack:
            node = stack.pop()
            dpath = node.path
            self._tick(dpath)
            try:
                it = os.scandir(winapi.long_path(dpath))
            except PermissionError:
                node.state = "denied"
                self._not_scanned(dpath, "erişim izni yok")
                continue
            except FileNotFoundError:
                node.state = "error"
                continue
            except OSError as exc:
                node.state = "error"
                self._not_scanned(dpath, "okunamadı: %s" % (exc.strerror or exc))
                continue
            child_depth = node.depth + 1
            with it:
                while True:
                    try:
                        entry = next(it)
                    except StopIteration:
                        break
                    except OSError:
                        res.file_errors += 1
                        continue
                    name = entry.name
                    fpath = os.path.join(dpath, name)
                    try:
                        if entry.is_symlink():
                            res.links_skipped += 1
                            if len(res.link_samples) < 50:
                                res.link_samples.append(fpath)
                            continue
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        res.file_errors += 1
                        continue
                    mode = st.st_mode
                    attrs = getattr(st, "st_file_attributes", 0)
                    tag = getattr(st, "st_reparse_tag", 0) if attrs & winapi.FILE_ATTRIBUTE_REPARSE_POINT else 0
                    if stat_mod.S_ISDIR(mode):
                        if attrs & winapi.FILE_ATTRIBUTE_REPARSE_POINT and not winapi.is_cloud_tag(tag):
                            # junction, volume mount point, or unknown directory reparse point
                            res.reparse_skipped += 1
                            if len(res.reparse_samples) < 50:
                                res.reparse_samples.append(fpath)
                            continue
                        key = norm(fpath)
                        if key in skip:
                            continue
                        if not IS_WINDOWS:
                            if st.st_dev != root_dev:
                                res.mounts_skipped.append(fpath)
                                continue
                            ino_key = (st.st_dev, st.st_ino)
                            if ino_key in self._seen_dirs:
                                continue
                            self._seen_dirs.add(ino_key)
                        lname = name.lower()
                        if lname in MARKER_DIRS:
                            if node.flags is None:
                                node.flags = set()
                            node.flags.add(lname)
                        child = Node(name, fpath, node, child_depth)
                        node.children.append(child)
                        res.nodes.append(child)
                        stack.append(child)
                        continue
                    if not stat_mod.S_ISREG(mode):
                        continue  # sockets, fifos, devices
                    size = st.st_size
                    res.total_files += 1
                    # ---- on-disk (allocated) size
                    if IS_WINDOWS:
                        if attrs & winapi.NEEDS_REAL_ALLOC or (tag and winapi.is_data_tag(tag)):
                            alloc = winapi.compressed_size(fpath)
                            if alloc is None:
                                alloc = size
                            if attrs & winapi.PLACEHOLDER_ATTRS or winapi.is_cloud_tag(tag):
                                res.cloud_apparent += size
                                res.cloud_alloc += alloc
                        elif tag:
                            alloc = 0  # app execution aliases and similar stubs
                        else:
                            c = self._cluster_for(fpath)
                            alloc = (size + c - 1) // c * c
                        if size >= WIN_HARDLINK_CHECK_BYTES:
                            try:
                                full = os.stat(winapi.long_path(fpath), follow_symlinks=False)
                                if full.st_nlink > 1:
                                    ikey = (full.st_dev, full.st_ino)
                                    if ikey in self._seen_inodes:
                                        res.hardlink_dupes += 1
                                        res.hardlink_dupe_bytes += alloc
                                        node.files += 1
                                        continue
                                    self._seen_inodes.add(ikey)
                            except OSError:
                                pass
                    else:
                        alloc = getattr(st, "st_blocks", None)
                        alloc = alloc * 512 if alloc is not None else size
                        if st.st_nlink > 1:
                            ikey = (st.st_dev, st.st_ino)
                            if ikey in self._seen_inodes:
                                res.hardlink_dupes += 1
                                res.hardlink_dupe_bytes += alloc
                                node.files += 1
                                continue
                            self._seen_inodes.add(ikey)
                        if sys.platform == "darwin" and getattr(st, "st_flags", 0) & 0x40000000:
                            res.cloud_apparent += size  # SF_DATALESS (iCloud evicted file)
                            res.cloud_alloc += alloc
                    node.files += 1
                    node.own_alloc += alloc
                    node.size += size
                    mt = st.st_mtime
                    if mt > node.mtime:
                        node.mtime = mt
                    at = st.st_atime
                    if at > node.atime:
                        node.atime = at
                    if mt < old_cutoff:
                        node.old_alloc += alloc
                    lname = name.lower()
                    dot = lname.rfind(".")
                    if dot > 0 and lname[dot:] in valuable:
                        node.prot += alloc
                    if lname in MARKER_FILES or lname.endswith(MARKER_SUFFIXES):
                        if node.flags is None:
                            node.flags = set()
                        node.flags.add(lname)
                    if alloc >= big or size >= big:
                        res.big_files.append((alloc, size, fpath, mt, at, node))
                    elif lname in SPECIAL_FILES:
                        res.special_files.append((alloc, size, fpath, mt, at, node))
        res.seconds += time.time() - t0
        return root

    # ---------------------------------------------------------------- aggregate
    def finish(self):
        """Roll file counters up into ancestors (children were created after parents)."""
        nodes = self.result.nodes
        for n in nodes:
            n.alloc += n.own_alloc
            if n.has(".git"):
                n.repos += 1
        for n in reversed(nodes):
            p = n.parent
            if p is None:
                continue
            p.alloc += n.alloc
            p.size += n.size
            p.files += n.files
            p.dirs += n.dirs + 1
            p.prot += n.prot
            p.repos += n.repos
            if n.mtime > p.mtime:
                p.mtime = n.mtime
            if n.atime > p.atime:
                p.atime = n.atime
        return self.result
