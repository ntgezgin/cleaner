"""Walk the scanned tree, apply rules and produce recommendation items.

Every directory gets a category code used for colouring the treemap:
  s = safe, r = review, n = never (locked), u = unclassified.
"""

import os
import time

from . import rules as R
from .platform_info import is_under, norm

LOCK_RANK = {None: 0, "soft": 1, "exact": 2, "hard": 3}
CAT_CODE = {"safe": "s", "review": "r", "never": "n"}
MIN_ITEM_BYTES = 1024 * 1024
DOWNLOAD_INSTALLER_MIN = 50 * 1024 * 1024
STALE_DAYS = 180


class Lock(object):
    __slots__ = ("strength", "rule", "path", "title")

    def __init__(self, strength, rule, path, title=None):
        self.strength = strength
        self.rule = rule
        self.path = path
        self.title = title or (rule.title if rule else "")


class Classifier(object):
    def __init__(self, env, ruleset, config):
        self.env = env
        self.rs = ruleset
        self.config = config
        self.items = []
        self.repos = []          # repository nodes (NEVER: source code)
        self.now = time.time()
        self.stale_days = int(config.get("stale_days", STALE_DAYS))
        self._explicit_anc = self._explicit_ancestors()
        self._created = set()

    # ------------------------------------------------------------------ helpers
    def _explicit_ancestors(self):
        """Keys of every folder that is an ancestor of an explicit-path rule target."""
        anc = set()
        keys = list(self.rs.exact.keys()) + [p for p, _, _ in self.rs.wild]
        for k in keys:
            k = k.rstrip(os.sep)
            while True:
                parent = os.path.dirname(k)
                if not parent or parent == k:
                    break
                anc.add(parent)
                k = parent
        return anc

    @staticmethod
    def _allowed(rule, lock):
        if lock is None:
            return True
        if lock.strength == "hard":
            return False
        if lock.strength == "exact":
            return rule.explicit
        return rule.explicit or rule.dev   # soft

    def _age_days(self, t):
        return int((self.now - t) / 86400) if t else None

    @staticmethod
    def _nearest_repo(node):
        p = node.parent
        while p is not None:
            if p.has(".git"):
                return p
            p = p.parent
        return None

    @staticmethod
    def _largest_children(node, limit=8):
        """Largest sub-folders, descending through dominant single children (Steam/steamapps/common)."""
        cur, trail = node, []
        for _ in range(4):
            kids = sorted(cur.children, key=lambda c: c.alloc, reverse=True)
            if kids and cur.alloc and kids[0].alloc >= 0.8 * cur.alloc and len(kids[0].children) > 1:
                trail.append(kids[0].name)
                cur = kids[0]
                continue
            break
        kids = sorted(cur.children, key=lambda c: c.alloc, reverse=True)[:limit]
        return cur, [(k.name, k.alloc) for k in kids if k.alloc > 0]

    # -------------------------------------------------------------------- items
    def _resolve_cmd(self, rule, node):
        cmd = rule.cmd
        tool = rule.tool
        if isinstance(cmd, dict):
            tool = cmd.get("tool", tool)
            cmd = cmd.get("cmd")
        if callable(cmd):
            cmd = cmd(node)
        if callable(tool):
            tool = tool(node)
        return cmd, tool

    def make_item(self, node, rule, evidence=None, kind=None, title=None, path=None,
                  is_file=False, file_info=None):
        path = path or node.path
        key = norm(path)
        if key in self._created:
            return None
        kind = kind or rule.kind
        cat = rule.cat
        warnings, ev = [], []
        if evidence:
            ev.append(evidence)
        if rule.warn:
            warnings.append(rule.warn)
        if is_file:
            alloc, size, mtime, atime = file_info
            files = 1
            denied = False
        else:
            alloc, size, files, mtime, atime = node.alloc, node.size, node.files, node.mtime, node.atime
            denied = node.state in ("denied", "error")
            if kind == "trash_old" and rule.older:
                cutoff = self.now - rule.older * 86400
                alloc = node.old_alloc + sum(c.alloc for c in node.children
                                             if c.mtime and c.mtime < cutoff)
                ev.append("%d günden eski içerik tahmini; script çalışırken yeniden kontrol eder"
                          % rule.older)
        reclaim = rule.reclaim
        if denied:
            reclaim = "unknown"
            ev.append("Bu klasörün içi okunamadı; boyut ölçülemedi")
        if alloc < MIN_ITEM_BYTES and reclaim != "unknown" and kind not in ("report",):
            return None
        selectable = kind not in ("advice", "report") and cat != "never"
        if not is_file:
            valuable = node.prot
            if valuable and not rule.allow_valuable and (valuable >= 100 * R.MB or
                                                         valuable >= 0.2 * max(alloc, 1) and valuable >= 10 * R.MB):
                warnings.append("İçinde %s veri/medya/model uzantılı dosya var (csv, wav, pt, "
                                "jpg…). Kontrol et." % human(valuable))
                if cat == "safe":
                    cat = "review"
            if node.repos and not rule.allow_git:
                warnings.append("İçinde %d git deposu var: kaynak kod olabilir, seçilemez."
                                % node.repos)
                cat, selectable = "never", False
        age = self._age_days(mtime)
        cmd, tool = (None, None)
        if kind == "command":
            cmd, tool = self._resolve_cmd(rule, node)
            if not cmd:
                kind = rule.fallback or "advice"
        default = rule.default if rule.default is not None else (cat == "safe")
        item = {
            "id": len(self.items),
            "rule": rule.id,
            "title": title or rule.title,
            "path": path,
            "cat": cat,
            "kind": kind,
            "size": alloc,
            "apparent": size,
            "files": files,
            "mtime": int(mtime) if mtime else None,
            "atime": int(atime) if atime else None,
            "age_days": age,
            "stale": bool(age is not None and age >= self.stale_days),
            "desc": rule.desc,
            "evidence": ev,
            "warnings": warnings,
            "selectable": selectable,
            "default": bool(default and selectable),
            "reclaim": reclaim,
            "disk": self.env.disk_of(path),
            "admin": rule.admin,
            "procs": list(rule.procs),
            "older": rule.older,
            "cmd": cmd,
            "tool": tool,
            "fallback": rule.fallback if kind == "command" else None,
            "is_file": is_file,
            "note": rule.note,
        }
        if not is_file:
            node.item = item["id"]
            repo = self._nearest_repo(node)
            if repo is not None:
                item["repo"] = repo.path
        self._created.add(key)
        self.items.append(item)
        return item

    def report_item(self, node, rule, lock_title=None):
        item = self.make_item(node, rule, kind="report", title=lock_title)
        if item is None:
            return
        item["selectable"] = False
        item["default"] = False
        item["cat"] = "never"
        cur, kids = self._largest_children(node)
        if kids:
            where = "" if cur is node else " (%s içinde)" % os.path.relpath(cur.path, node.path)
            item["evidence"].append("En büyükler%s: " % where + ", ".join(
                "%s %s" % (n, human(s)) for n, s in kids))
        if rule.big_note:
            item["desc"] = rule.desc + " " + rule.big_note

    # --------------------------------------------------------------------- walk
    def run(self, scan):
        for root in scan.roots:
            self._walk(root)
        self._file_items(scan)
        self._synthetic_items()
        return self.items

    def _walk(self, root):
        stack = [(root, None, None, None)]
        while stack:
            node, lock, item, pc = stack.pop()
            key = norm(node.path)
            matches = self.rs.match(node, key)
            node.lock = lock
            if item is not None:
                for r in matches:
                    if r.cat == "never" and r.lock == "hard" and r.explicit:
                        it = self.items[item]
                        it["selectable"] = False
                        it["default"] = False
                        it["cat"] = "never"
                        it["warnings"].append("İçinde dokunulmaz klasör var: %s (%s)"
                                              % (node.path, r.title))
                node.cat = CAT_CODE[self.items[item]["cat"]]
                node.item = item
                for c in node.children:
                    stack.append((c, lock, item, None))
                continue
            if lock is not None and lock.strength == "hard":
                node.cat = "n"
                for c in node.children:
                    stack.append((c, lock, None, None))
                continue
            hard = None
            for r in matches:
                if r.cat == "never" and r.lock == "hard":
                    hard = r
                    break
            if hard is not None:
                node.cat = "n"
                lk = Lock("hard", hard, node.path)
                node.lock = lk
                if hard.kind == "report" or (hard.big_note and node.alloc >= 1024 ** 3):
                    self.report_item(node, hard)
                for c in node.children:
                    stack.append((c, lk, None, None))
                continue
            item_rule, evidence = None, None
            for r in matches:
                if r.cat in ("safe", "review") and self._allowed(r, lock):
                    if r.needs is not None:
                        evidence = r.needs(node)
                        if evidence is None:
                            continue
                    item_rule = r
                    break
            child_pc = None
            if item_rule is not None and item_rule.per_child:
                # e.g. Program Files: big children get uninstall advice, the folder stays NEVER
                never_lock = None
                for r in matches:
                    if r.cat == "never" and r.lock in ("soft", "exact"):
                        never_lock = self._stronger(never_lock, Lock(r.lock, r, node.path))
                node.cat = "n" if never_lock is not None else CAT_CODE[item_rule.cat]
                child_pc = (item_rule, evidence)
                lock = self._stronger(lock, never_lock or Lock("exact", None, node.path,
                                                               item_rule.title))
                node.lock = lock
            elif item_rule is not None:
                it = self.make_item(node, item_rule, evidence, title=self._title(item_rule, node))
                if it is not None:
                    node.cat = CAT_CODE[it["cat"]]
                    for c in node.children:
                        stack.append((c, lock, it["id"], None))
                    continue
                node.cat = CAT_CODE[item_rule.cat]
            elif pc is not None and key not in self._explicit_anc and node.alloc >= pc[0].child_min:
                rule, ev = pc
                it = self.make_item(node, rule, ev, kind=rule.child_kind or rule.kind,
                                    title="%s: %s" % (rule.title, node.name))
                if it is not None:
                    node.cat = CAT_CODE[it["cat"]]
                    for c in node.children:
                        stack.append((c, lock, it["id"], None))
                    continue
            if item_rule is None:
                for r in matches:
                    if r.cat == "never" and r.lock in ("soft", "exact"):
                        lock = self._stronger(lock, Lock(r.lock, r, node.path))
                if R.is_project(node):
                    lock = self._stronger(lock, Lock("soft", None, node.path,
                                                     "Kaynak kod / proje"))
                    if node.has(".git"):
                        self.repos.append(node)
                node.lock = lock
                node.cat = "n" if lock is not None else "u"
            for c in node.children:
                stack.append((c, lock, None, child_pc))

    @staticmethod
    def _stronger(cur, new):
        if cur is None or LOCK_RANK[new.strength] > LOCK_RANK[cur.strength]:
            return new
        return cur

    @staticmethod
    def _title(rule, node):
        if rule.dev and node.parent is not None:
            return "%s — %s" % (rule.title, node.parent.name)
        return rule.title

    # ------------------------------------------------------------- file items
    def _file_items(self, scan):
        downloads = [norm(p) for p in self.env.known.get("downloads", [])]
        rs = self.rs
        for rec in scan.big_files + scan.special_files:
            alloc, size, path, mtime, atime, node = rec
            if node.item is not None:
                continue
            key = norm(path)
            lock = node.lock
            if lock is not None and lock.strength == "hard":
                continue
            lname = os.path.basename(path).lower()
            done = False
            for r in rs.exact.get(key, ()):
                if r.cat in ("safe", "review") and self._allowed(r, lock):
                    self.make_item(node, r, kind=r.kind, path=path, is_file=True,
                                   file_info=(alloc, size, mtime, atime))
                    done = True
                    break
            if done:
                continue
            if lname in ("hiberfil.sys", "pagefile.sys", "swapfile.sys"):
                advice = {
                    "hiberfil.sys": ("Hazırda bekletme dosyası", "Elle silinmez. Hazırda "
                                     "bekletmeyi kullanmıyorsan yönetici olarak 'powercfg /h off' "
                                     "ile kapatılabilir (Hızlı Başlangıç da kapanır) ya da "
                                     "'powercfg /h /type reduced' ile küçültülebilir."),
                    "pagefile.sys": ("Sayfa dosyası (sanal bellek)", "Elle silinmez. Boyutu "
                                     "Sistem > Gelişmiş sistem ayarları > Performans > Sanal "
                                     "bellek'ten ayarlanır. Kapatmak önerilmez."),
                    "swapfile.sys": ("Swap dosyası", "Windows yönetir; elle silinmez."),
                }[lname]
                rule = R.Rule("system-file", "review", advice[0], advice[1], kind="advice")
                self.make_item(node, rule, path=path, is_file=True,
                               file_info=(alloc, size, mtime, atime))
                continue
            if alloc >= DOWNLOAD_INSTALLER_MIN and lname.endswith(R.INSTALLER_EXT) and \
                    any(is_under(key, d) for d in downloads):
                rule = R.Rule("download-installer", "review", "İndirilen kurulum/arşiv dosyası",
                              "Downloads'taki büyük installer/arşiv/disk imajı. Programı zaten "
                              "kurduysan genelde gereksiz; ama arşivin tek kopyası olabilir "
                              "(ör. veri seti .zip), içeriğini kontrol et.")
                self.make_item(node, rule, path=path, is_file=True,
                               file_info=(alloc, size, mtime, atime),
                               title=os.path.basename(path))

    def _synthetic_items(self):
        """Official system cleanup commands whose folders could not be measured."""
        for key, rules in self.rs.exact.items():
            for r in rules:
                if r.kind == "command" and r.reclaim == "unknown" and key not in self._created:
                    path = r.abs
                    if os.path.isdir(path) or r.id in ("cleanmgr", "dism"):
                        class _N(object):
                            pass
                        n = _N()
                        n.path, n.alloc, n.size, n.files, n.mtime, n.atime = path, 0, 0, 0, 0, 0
                        n.state, n.prot, n.repos, n.children, n.parent = "denied", 0, 0, [], None
                        n.old_alloc, n.item = 0, None
                        it = self.make_item(n, r)
                        if it is not None:
                            it["evidence"] = ["Boyut ölçülemedi (sistem klasörü / yönetici "
                                              "izni gerekir); toplamlara eklenmedi."]


def human(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return ("%.0f %s" if unit in ("B", "KB") else "%.1f %s") % (n, unit)
        n /= 1024
