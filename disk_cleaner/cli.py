"""Command line entry point.

    python -m disk_cleaner                 # scan home + other drives, write HTML report
    python -m disk_cleaner --home-only     # only the home folder
    python -m disk_cleaner render r.json   # re-render a saved JSON report

The scan is strictly read-only. It never deletes, moves, renames or modifies files.
"""

import argparse
import json
import os
import sys
import time

from . import __version__, classify, platform_info, probes, report, rules, scanner
from .platform_info import is_under, norm


def config_locations(env):
    locs = [os.path.join(os.getcwd(), "disk-cleaner.json")]
    if env.os == "windows":
        locs.append(os.path.join(env.anchors["APPDATA"], "disk-cleaner", "config.json"))
    elif env.os == "macos":
        locs.append(os.path.join(env.home, "Library", "Application Support", "disk-cleaner",
                                 "config.json"))
    else:
        locs.append(os.path.join(env.anchors["XDG_CONFIG"], "disk-cleaner", "config.json"))
    return locs


def load_config(path, env):
    candidates = [path] if path else config_locations(env)
    for p in candidates:
        if p and os.path.isfile(p):
            with open(p, encoding="utf-8-sig") as fh:
                try:
                    cfg = json.load(fh)
                except ValueError as exc:
                    raise SystemExit("Config dosyası okunamadı (%s): %s" % (p, exc))
            if not isinstance(cfg, dict):
                raise SystemExit("Config bir JSON nesnesi olmalı: %s" % p)
            return cfg, p
    if path:
        raise SystemExit("Config dosyası bulunamadı: %s" % path)
    return {}, None


def _expand(p):
    return os.path.normpath(os.path.abspath(os.path.expandvars(os.path.expanduser(p))))


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="disk-cleaner",
        description="Salt-okunur disk analizi + internetsiz interaktif temizlik raporu. "
                    "Hiçbir dosyayı silmez/değiştirmez.")
    ap.add_argument("command", nargs="?", default="scan", choices=["scan", "render"],
                    help="scan (varsayılan) ya da render <json>")
    ap.add_argument("input", nargs="?", help="render için JSON rapor dosyası")
    ap.add_argument("-o", "--out", help="HTML çıktı yolu (varsayılan: disk-report-<host>-<tarih>.html)")
    ap.add_argument("--json", dest="json_out", help="Ham veriyi JSON olarak da kaydet")
    ap.add_argument("-c", "--config", help="JSON ayar dosyası (never_paths, exclude_paths…)")
    ap.add_argument("--never", action="append", default=[], metavar="PATH",
                    help="Dokunulmaz klasör ekle (tekrarlanabilir)")
    ap.add_argument("--exclude", action="append", default=[], metavar="PATH",
                    help="Bu klasörü hiç tarama (tekrarlanabilir)")
    ap.add_argument("--root", action="append", default=[], metavar="PATH",
                    help="Sadece bu kökleri tara (varsayılan: ev klasörü + diğer sürücüler)")
    ap.add_argument("--home-only", action="store_true", help="Sadece ev klasörünü tara")
    ap.add_argument("--skip-system", action="store_true",
                    help="Windows/System klasörlerini tarama (daha hızlı)")
    ap.add_argument("--all-drives", action="store_true",
                    help="Çıkarılabilir sürücüleri de tara")
    ap.add_argument("--no-git", action="store_true", help="git status kontrollerini atla")
    ap.add_argument("--no-docker", action="store_true", help="docker system df çalıştırma")
    ap.add_argument("-q", "--quiet", action="store_true")
    ap.add_argument("--version", action="version", version="disk-cleaner " + __version__)
    return ap.parse_args(argv)


class Progress(object):
    def __init__(self, quiet):
        self.quiet = quiet
        self.tty = sys.stderr.isatty()
        self.t0 = time.time()

    def log(self, msg):
        if not self.quiet:
            self.clear()
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()

    def clear(self):
        if self.tty and not self.quiet:
            sys.stderr.write("\r\033[K")

    def scan(self, dirs, files, path):
        if self.quiet:
            return
        line = "  %d klasör, %d dosya  %s" % (dirs, files, path)
        if self.tty:
            width = 110
            sys.stderr.write("\r\033[K" + (line[:width - 1] if len(line) > width else line))
        else:
            sys.stderr.write(line[:200] + "\n")
        sys.stderr.flush()


def analyze(env, cfg, roots, excludes=(), system_skip=(), all_drives=False, no_git=False,
            no_docker=False, progress=None, config_path=None, options=None):
    """Scan, classify and build the report data. Read-only. Returns (data, items)."""
    pr = progress or Progress(True)
    cfg = dict(cfg)
    cfg.setdefault("never_paths", [])
    root_keys = [norm(p) for p, _ in roots]
    sc = scanner.Scanner(rules.VALUABLE_EXT, skip_paths=excludes, progress=pr.scan if pr else None)
    for p in excludes:
        sc.result.not_scanned.append({"path": p, "reason": "hariç tutuldu (exclude)"})
    for p in system_skip:
        if os.path.isdir(p):
            sc.result.not_scanned.append({"path": p, "reason": "--skip-system ile atlandı"})
    for d in env.disks:
        if d.get("type") in ("network", "cloud", "removable", "cdrom") and \
                not (all_drives and d.get("type") == "removable"):
            sc.result.not_scanned.append({"path": d["mount"], "reason": "%s sürücü taranmadı" %
                                          {"network": "ağ", "cloud": "bulut (sanal)",
                                           "removable": "çıkarılabilir", "cdrom": "CD/DVD"}
                                          .get(d["type"], d["type"])})
    if env.os == "macos":
        sc.result.not_scanned.append({"path": "/System", "reason": "macOS korumalı sistem "
                                      "birimi (SIP, salt-okunur) taranmadı"})

    for i, (path, label) in enumerate(roots):
        if not os.path.isdir(path):
            continue
        key = root_keys[i]
        inner = [roots[j][0] for j, k in enumerate(root_keys) if j != i and is_under(k, key)
                 and k != key]
        pr.log("[%d/%d] Taranıyor: %s" % (i + 1, len(roots), path))
        sc.scan_root(path, label, extra_skip=inner + list(system_skip))
    pr.clear()
    scan = sc.finish()
    pr.log("  %d klasör, %d dosya, %.1f sn" % (len(scan.nodes), scan.total_files, scan.seconds))

    ruleset = rules.RuleSet(env, rules.build_rules(env, cfg))
    cl = classify.Classifier(env, ruleset, cfg)
    items = cl.run(scan)

    docker_note = None
    if not no_docker:
        rows, docker_note = probes.probe_docker(env.os)
        items.extend(probes.docker_items(rows, env.os, len(items)))
    for it in items:
        if it["rule"] == "ollama":
            names = probes.ollama_models(it["path"])
            if names:
                it["evidence"].append("Modeller: " + ", ".join(names[:30]))
    repos = []
    if not no_git:
        if cl.repos:
            pr.log("  %d git deposu kontrol ediliyor (salt-okunur git status)…" % len(cl.repos))
        repos = probes.probe_git(cl.repos, items)
    else:
        repos = [{"path": n.path, "size": n.alloc, "files": n.files, "dirty": None,
                  "ahead": None, "branch": None, "error": "--no-git"} for n in cl.repos]

    options = dict(options or {})
    options.update({"roots": [p for p, _ in roots], "excludes": list(excludes),
                    "never_paths": cfg["never_paths"]})
    data = report.build_report(env, scan, cl, items, repos, options, docker_note, config_path)
    return data, items


def main(argv=None):
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "render":
        if not args.input:
            raise SystemExit("render için JSON dosyası ver: disk-cleaner render rapor.json")
        with open(args.input, encoding="utf-8") as fh:
            data = json.load(fh)
        out = args.out or os.path.splitext(args.input)[0] + ".html"
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(report.render_html(data))
        print(out)
        return 0

    pr = Progress(args.quiet)
    env = platform_info.Environment()
    cfg, cfg_path = load_config(args.config, env)
    cfg = dict(cfg)
    cfg["never_paths"] = [_expand(p) for p in list(cfg.get("never_paths", [])) + args.never]
    excludes = [_expand(p) for p in list(cfg.get("exclude_paths", [])) + args.exclude]
    home_only = args.home_only or bool(cfg.get("home_only"))
    skip_system = args.skip_system or bool(cfg.get("skip_system"))
    all_drives = args.all_drives or bool(cfg.get("all_drives"))
    pr.log("disk-cleaner %s — %s, kullanıcı %s, %s" % (__version__, env.os_detail, env.user,
                                                     "YÖNETİCİ" if env.is_admin else "normal yetki"))
    if env.is_admin:
        pr.log("  Not: yönetici/root olarak çalışıyor. Gerek yok; normal kullanıcıyla çalıştırman önerilir.")
    if cfg_path:
        pr.log("  Ayarlar: %s" % cfg_path)

    if args.root:
        roots = [(_expand(p), "custom") for p in args.root]
    else:
        roots = env.default_roots(home_only=home_only, all_drives=all_drives)
        roots += [(_expand(p), "custom") for p in cfg.get("extra_roots", [])]

    system_skip = []
    if skip_system:
        if env.os == "windows":
            system_skip = [env.anchors["WINDIR"]]
        elif env.os == "linux":
            system_skip = ["/usr", "/var/lib", "/opt"]
        else:
            system_skip = ["/Library", "/System"]

    data, items = analyze(env, cfg, roots, excludes, system_skip, all_drives=all_drives,
                          no_git=args.no_git or bool(cfg.get("no_git")),
                          no_docker=args.no_docker or bool(cfg.get("no_docker")),
                          progress=pr, config_path=cfg_path,
                          options={"home_only": home_only, "skip_system": skip_system,
                                   "all_drives": all_drives})
    out = args.out or report.default_output_name(env)
    out = os.path.abspath(out)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(report.render_html(data))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

    safe = sum(i["size"] for i in items if i["cat"] == "safe" and i["selectable"]
               and i["reclaim"] == "full")
    review = sum(i["size"] for i in items if i["cat"] == "review" and i["selectable"]
                 and i["reclaim"] == "full")
    pr.log("")
    pr.log("Safe (tam kazanç): %s   Review: %s   Öneri sayısı: %d"
           % (classify.human(safe), classify.human(review), len(items)))
    pr.log("Rapor: %s" % out)
    pr.log("Hiçbir dosya değiştirilmedi. Raporu tarayıcıda aç, seçimlerini yap, script'i üret.")
    if args.quiet:
        print(out)
    return 0
