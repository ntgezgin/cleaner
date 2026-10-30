"""Optional read-only probes using external tools (git, docker).

Each probe has a timeout and failures are reported, never fatal. git is invoked with
--no-optional-locks and fsmonitor disabled so that `git status` does not write to the
repository's index.
"""

import os
import re
import shutil
import subprocess
import time

from .classify import human

GIT_ENV = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")


def _run(args, timeout, cwd=None, input_text=None):
    try:
        p = subprocess.run(args, cwd=cwd, input=input_text, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout, env=GIT_ENV,
                           universal_newlines=True, encoding="utf-8", errors="replace")
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return None, "", "timeout"
    except OSError as exc:
        return None, "", str(exc)


def probe_git(repos, items, max_repos=400, budget_s=120.0, progress=None):
    """Status of every repository found + whether repo-local items are git-ignored."""
    git = shutil.which("git")
    out = []
    repos = sorted(repos, key=lambda n: n.alloc, reverse=True)
    if not git:
        for n in repos:
            out.append({"path": n.path, "size": n.alloc, "files": n.files, "dirty": None,
                        "ahead": None, "branch": None, "error": "git bulunamadı"})
        return out
    by_repo = {}
    for it in items:
        if it.get("repo"):
            by_repo.setdefault(it["repo"], []).append(it)
    t0 = time.time()
    base = [git, "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
            "--no-optional-locks"]
    for i, n in enumerate(repos):
        info = {"path": n.path, "size": n.alloc, "files": n.files, "dirty": None, "ahead": None,
                "branch": None, "error": None, "age_days": None}
        if n.mtime:
            info["age_days"] = int((time.time() - n.mtime) / 86400)
        out.append(info)
        if i >= max_repos or time.time() - t0 > budget_s:
            info["error"] = "zaman/limit aşıldı, kontrol edilmedi"
            continue
        if progress:
            progress(i + 1, len(repos), n.path)
        rc, so, se = _run(base + ["-C", n.path, "status", "--porcelain=v1", "-b",
                                  "--untracked-files=normal"], timeout=20)
        if rc != 0:
            info["error"] = (se.strip().splitlines() or ["git status başarısız"])[0][:200]
        else:
            lines = so.splitlines()
            head = lines[0] if lines and lines[0].startswith("##") else ""
            info["dirty"] = sum(1 for ln in lines if ln and not ln.startswith("##"))
            m = re.match(r"## (?:No commits yet on )?([^.\s]+)", head)
            info["branch"] = m.group(1) if m else None
            m = re.search(r"ahead (\d+)", head)
            info["ahead"] = int(m.group(1)) if m else 0
            if "..." not in head and info["branch"]:
                info["upstream"] = False
        for it in by_repo.get(n.path, []):
            if info["dirty"]:
                it["evidence"].append("Bağlı repo'da %d commit edilmemiş değişiklik var "
                                      "(repo'nun kendisi NEVER, dokunulmaz)" % info["dirty"])
        candidates = by_repo.get(n.path, [])
        if candidates and info["error"] is None:
            rels = [os.path.relpath(it["path"], n.path) for it in candidates]
            rc, so, _ = _run(base + ["-C", n.path, "check-ignore", "--stdin"], timeout=20,
                             input_text="\n".join(rels) + "\n")
            if rc in (0, 1):
                ignored = set(os.path.normpath(p) for p in so.splitlines() if p)
                for it, rel in zip(candidates, rels):
                    if os.path.normpath(rel) in ignored:
                        it["evidence"].append("git: .gitignore kapsamında ✓")
                    else:
                        it["warnings"].append("git bu klasörü ignore ETMİYOR: içindeki "
                                              "dosyalar repoda takip ediliyor olabilir.")
                        it["default"] = False
    return out


_SIZE_RE = re.compile(r"([\d.]+)\s*([kKMGTP]?B)")
_UNITS = {"B": 1, "KB": 1000, "kB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4,
          "PB": 1000 ** 5}


def _parse_size(s):
    m = _SIZE_RE.search(s or "")
    if not m:
        return 0
    return int(float(m.group(1)) * _UNITS.get(m.group(2), 1))


def probe_docker(os_name):
    """Docker usage via `docker system df` (read-only). Returns (rows, note)."""
    docker = shutil.which("docker")
    if not docker:
        return None, None
    rc, so, se = _run([docker, "system", "df", "--format", "{{json .}}"], timeout=25)
    if rc != 0:
        return None, "Docker kurulu ama 'docker system df' çalışmadı (daemon kapalı olabilir): %s" \
            % (se.strip()[:200] or "zaman aşımı")
    import json
    rows = {}
    for line in so.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        rows[d.get("Type", "")] = {"size": _parse_size(d.get("Size")),
                                   "reclaimable": _parse_size(d.get("Reclaimable")),
                                   "count": d.get("TotalCount"), "active": d.get("Active")}
    return rows, None


def docker_items(rows, os_name, next_id):
    if not rows:
        return []
    items = []
    vm_note = ("Docker Desktop'ta (Windows/macOS) prune sonrası sanal disk dosyası otomatik "
               "küçülmeyebilir; gerçek kazanç daha az olabilir.") if os_name != "linux" else None
    img = rows.get("Images", {})
    cont = rows.get("Containers", {})
    cache = rows.get("Build Cache", {})
    vols = rows.get("Local Volumes", {})

    def base(**kw):
        d = {"rule": "docker", "path": "docker://", "apparent": 0, "files": 0, "mtime": None,
             "atime": None, "age_days": None, "stale": False, "evidence": [], "warnings": [],
             "selectable": True, "default": False, "reclaim": "partial", "disk": -1,
             "admin": False, "procs": [], "older": None, "tool": "docker", "fallback": None,
             "is_file": False, "note": None, "kind": "command", "cat": "review"}
        d.update(kw)
        if vm_note:
            d["warnings"].append(vm_note)
        return d

    up = cache.get("reclaimable", 0) + cont.get("reclaimable", 0)
    items.append(base(
        title="Docker: durmuş container'lar, dangling image'lar, build cache",
        size=up,
        desc="Resmi komut 'docker system prune -f' (ASLA --volumes değil). Durmuş "
             "container'ların içindeki yazılmış veriler silinir; volume'lara dokunulmaz.",
        cmd="docker system prune -f",
        evidence=["docker system df: build cache geri kazanılabilir %s, container %s"
                  % (human(cache.get("reclaimable")), human(cont.get("reclaimable")))]))
    items.append(base(
        title="Docker: kullanılmayan tüm image'lar",
        size=img.get("reclaimable", 0),
        desc="'docker image prune -a -f': hiçbir container'ın kullanmadığı image'ları siler. "
             "Registry'den yeniden çekilebilir; ama lokal build edip push etmediğin image'lar "
             "kaybolur.",
        cmd="docker image prune -a -f",
        evidence=["docker system df: %s image, %s aktif, geri kazanılabilir %s"
                  % (img.get("count"), img.get("active"), human(img.get("reclaimable")))]))
    if vols.get("size"):
        items.append(base(
            title="Docker volume'ları", size=vols.get("size", 0), cat="never", kind="report",
            selectable=False, reclaim="full", tool=None,
            desc="Veritabanı vb. kalıcı veri. Script volume'lara asla dokunmaz.",
            evidence=["%s volume" % vols.get("count")]))
    for i, it in enumerate(items):
        it["id"] = next_id + i
    return items


def ollama_models(models_dir):
    man = os.path.join(models_dir, "manifests")
    names = []
    for dirpath, dirnames, filenames in os.walk(man):
        for f in filenames:
            rel = os.path.relpath(os.path.join(dirpath, f), man).split(os.sep)
            if len(rel) >= 3:
                model = rel[-2] if rel[-3] == "library" else "/".join(rel[-3:-1])
                names.append("%s:%s" % (model, rel[-1]))
        if len(names) > 50:
            break
    return sorted(names)
