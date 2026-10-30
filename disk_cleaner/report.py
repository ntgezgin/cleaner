"""Assemble the report data and render the single-file offline HTML."""

import datetime
import json
import os
import pkgutil

from . import __version__
from . import rules as R
from .classify import CAT_CODE
from .platform_info import norm

MAX_TREE_NODES = 25000
MAX_CHILDREN = 300


def _ui(name):
    data = pkgutil.get_data("disk_cleaner", "ui/" + name)
    if data is None:
        raise RuntimeError("UI asset missing: %s" % name)
    return data.decode("utf-8")


def _file_cat(rec, items):
    alloc, size, path, mtime, atime, node = rec
    if node.item is not None:
        return CAT_CODE[items[node.item]["cat"]]
    if node.lock is not None:
        return "n"
    ext = os.path.splitext(path)[1].lower()
    if ext in R.VALUABLE_EXT:
        return "n"
    return "u"


def build_tree(scan, items, item_nodes, file_items):
    """Prune the full tree to something a browser can draw, keeping every big folder."""
    big_by_node = {}
    for rec in scan.big_files:
        big_by_node.setdefault(id(rec[5]), []).append(rec)

    # bytes of selectable SAFE / REVIEW items in each subtree (for the treemap bars)
    sub = {}
    for it, node in item_nodes:
        if not it["selectable"] or it["reclaim"] == "unknown":
            continue
        idx = 0 if it["cat"] == "safe" else 1
        n = node
        while n is not None:
            v = sub.setdefault(id(n), [0, 0])
            v[idx] += it["size"]
            n = n.parent

    total = sum(r.alloc for r in scan.roots) or 1

    def convert(threshold):
        count = [0]

        def conv(node):
            count[0] += 1
            d = {"n": node.name, "s": node.alloc, "f": node.files, "c": node.cat}
            if node.size and abs(node.size - node.alloc) > 0.05 * max(node.size, 1) \
                    and node.size - node.alloc > 1024 * 1024:
                d["a"] = node.size
            if node.mtime:
                d["m"] = int(node.mtime)
            if node.item is not None and items[node.item]["path"] == node.path:
                d["i"] = node.item
            if node.state:
                d["st"] = node.state
            if node.lock is not None and node.lock.path == node.path:
                d["lk"] = node.lock.title
            v = sub.get(id(node))
            if v:
                if v[0]:
                    d["rs"] = v[0]
                if v[1]:
                    d["rr"] = v[1]
            kids = []
            rest, rest_n = 0, 0
            entries = [(c.alloc, 0, c) for c in node.children]
            for rec in big_by_node.get(id(node), ()):
                entries.append((rec[0], 1, rec))
            entries.sort(key=lambda e: e[0], reverse=True)
            for alloc, is_file, obj in entries:
                if alloc >= threshold and len(kids) < MAX_CHILDREN:
                    if is_file:
                        rec = obj
                        fd = {"n": os.path.basename(rec[2]), "s": rec[0], "F": 1,
                              "c": _file_cat(rec, items), "m": int(rec[3]) if rec[3] else 0}
                        fi = file_items.get(norm(rec[2]))
                        if fi is not None:
                            fd["i"] = fi
                            fd["c"] = CAT_CODE[items[fi]["cat"]]
                        kids.append(fd)
                        count[0] += 1
                    else:
                        kids.append(conv(obj))
                else:
                    rest += alloc
                    rest_n += 1
            # "rest" = small sub-folders + small files directly in this folder
            shown = sum(k["s"] for k in kids)
            rest = max(node.alloc - shown, 0)
            if not node.children and not big_by_node.get(id(node)):
                rest_n = 0
            if kids:
                d["k"] = kids
            if rest:
                d["x"] = rest
                d["xn"] = rest_n
            return d

        roots = []
        for r in scan.roots:
            rd = conv(r)
            rd["p"] = r.path
            rd["root"] = r.label
            roots.append(rd)
        return roots, count[0]

    threshold = max(1024 * 1024, total // 20000)
    while True:
        roots, n = convert(threshold)
        if n <= MAX_TREE_NODES:
            break
        threshold *= 2
    return {"n": "Bu bilgisayar", "s": sum(r["s"] for r in roots), "c": "u", "k": roots,
            "virtual": True, "f": sum(r.get("f", 0) for r in roots)}, threshold


def build_report(env, scan, classifier, items, repos, options, docker_note=None,
                 config_path=None):
    item_nodes = []
    file_items = {}
    for n in scan.nodes:
        if n.item is not None and items[n.item]["path"] == n.path:
            item_nodes.append((items[n.item], n))
    for it in items:
        if it.get("is_file"):
            file_items[norm(it["path"])] = it["id"]
    file_rec = {norm(rec[2]): rec for rec in scan.big_files + scan.special_files}
    for key, iid in file_items.items():
        rec = file_rec.get(key)
        if rec is not None:
            item_nodes.append((items[iid], rec[5]))

    tree, threshold = build_tree(scan, items, item_nodes, file_items)

    top_files = []
    for rec in sorted(scan.big_files + scan.special_files, key=lambda r: r[0], reverse=True)[:50]:
        alloc, size, path, mtime, atime, node = rec
        cat = _file_cat(rec, items)
        fi = file_items.get(norm(path))
        if fi is not None:
            cat = CAT_CODE[items[fi]["cat"]]
        top_files.append({"path": path, "size": alloc, "apparent": size,
                          "mtime": int(mtime) if mtime else None,
                          "atime": int(atime) if atime else None, "cat": cat,
                          "ext": os.path.splitext(path)[1].lower(),
                          "item": fi if fi is not None else node.item})

    top_dirs = []
    for n in sorted((n for n in scan.nodes if not n.is_root), key=lambda n: n.alloc,
                    reverse=True)[:300]:
        biggest = max((c.alloc for c in n.children), default=0)
        top_dirs.append({"path": n.path, "size": n.alloc, "files": n.files,
                         "mtime": int(n.mtime) if n.mtime else None, "cat": n.cat,
                         "item": n.item, "depth": n.depth,
                         "wrap": bool(n.alloc and biggest >= 0.9 * n.alloc)})

    disks = []
    scanned_by_disk = {}
    for r in scan.roots:
        di = env.disk_of(r.path)
        scanned_by_disk[di] = scanned_by_disk.get(di, 0) + r.alloc
    for i, d in enumerate(env.disks):
        dd = dict(d)
        dd["scanned"] = scanned_by_disk.get(i, 0)
        if "used" in d:
            dd["unaccounted"] = max(d["used"] - dd["scanned"], 0)
        disks.append(dd)

    guard_hard = set()
    for rules in classifier.rs.exact.values():
        for r in rules:
            if r.cat == "never" and r.lock == "hard":
                guard_hard.add(r.abs)
    guard_exact = set()
    for p in [env.home] + [r.path for r in scan.roots]:
        guard_exact.add(p)
    for role, paths in env.known.items():
        guard_exact.update(paths)
    for rules in classifier.rs.exact.values():
        for r in rules:
            if r.cat == "never":
                guard_exact.add(r.abs)
    for d in env.disks:
        guard_exact.add(d["mount"])

    notes = [
        "Boyutlar diskte kapladığı alandır (allocated / size on disk). Bulut 'files on demand' "
        "yer tutucuları ~0 sayılır.",
        "Hardlink'ler bir kez sayıldı (%d tekrar, %s). pnpm/uv/conda gibi araçlar hardlink "
        "kullanır; bu yüzden onların 'kısmi' kazancı gösterilenden az olabilir."
        % (scan.hardlink_dupes, human(scan.hardlink_dupe_bytes)),
        "Sembolik bağlantılar (%d) ve junction/reparse point'ler (%d) takip edilmedi."
        % (scan.links_skipped, scan.reparse_skipped),
        "Son erişim (atime) birçok sistemde güvenilmezdir (noatime/relatime, Windows'ta "
        "varsayılan kapalı); kararlar son DEĞİŞTİRME zamanına göre verildi.",
        "Çöp Kutusu'na taşınan dosyalar, çöp kutusu boşaltılana kadar yer kaplamaya devam eder.",
    ]
    if env.os == "macos":
        notes.append("APFS klonları ve snapshot'ları (Time Machine yerel snapshot'ları) "
                     "ölçülemez; 'tmutil listlocalsnapshots /' ile bakabilirsin.")
    if env.os == "windows" and not options.get("skip_system"):
        notes.append("Windows klasörü (WinSxS) hardlink'lerle dolu; 256 KB altı dosyalarda "
                     "hardlink tekilleştirmesi yapılmadı, bu yüzden sistem boyutu biraz "
                     "şişkin görünebilir. Bu klasörler zaten NEVER.")
    if scan.cloud_apparent:
        notes.append("Bulut yer tutucuları: görünen %s, diskte %s."
                     % (human(scan.cloud_apparent), human(scan.cloud_alloc)))
    if docker_note:
        notes.append(docker_note)
    if scan.mounts_skipped:
        notes.append("Ayrı dosya sistemlerine geçilmedi (%d bağlama noktası); bunlar kendi "
                     "kökleri olarak tarandıysa listede ayrıca görünür." % len(scan.mounts_skipped))

    return {
        "meta": {
            "tool": "disk-cleaner", "version": __version__,
            "generated": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "scan_seconds": round(scan.seconds, 1), "files": scan.total_files,
            "dirs": len(scan.nodes), "tree_threshold": threshold,
            "options": options, "config": config_path,
        },
        "env": env.to_dict(),
        "disks": disks,
        "tree": tree,
        "items": items,
        "top_files": top_files,
        "top_dirs": top_dirs,
        "repos": repos,
        "not_scanned": scan.not_scanned[:1000],
        "not_scanned_count": len(scan.not_scanned),
        "links_skipped": scan.links_skipped,
        "link_samples": scan.link_samples[:20],
        "reparse_skipped": scan.reparse_skipped,
        "reparse_samples": scan.reparse_samples[:20],
        "mounts_skipped": scan.mounts_skipped[:50],
        "file_errors": scan.file_errors,
        "notes": notes,
        "guard": {"hard": sorted(guard_hard), "exact": sorted(guard_exact)},
    }


def human(n):
    from .classify import human as h
    return h(n)


def render_html(data):
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c")
    html = _ui("template.html")
    html = html.replace("/*__STYLE__*/", _ui("style.css"))
    html = html.replace("/*__SCRIPTGEN__*/", _ui("scriptgen.js"))
    html = html.replace("/*__APP__*/", _ui("app.js"))
    title = "Disk raporu — %s — %s" % (data["env"]["host"], data["meta"]["generated"][:10])
    html = html.replace("__TITLE__", _esc(title))
    return html.replace("__DATA__", payload)


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def default_output_name(env):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    safe_host = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in env.host)
    return "disk-report-%s-%s.html" % (safe_host, stamp)
