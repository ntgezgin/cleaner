"""Classification rules: SAFE (regenerable), REVIEW (ask the user), NEVER (untouchable).

Design rules (in priority order):
  1. Anything under a *hard* NEVER lock (games, cloud-sync folders, media, secrets,
     VMs, mail, user-configured paths…) can never become a cleanup item.
  2. Under an *exact* lock (system folders, AppData, ~/Library, ~/.config, browser
     profiles…) only rules that name an explicit path may create items.
  3. Under a *soft* lock (Documents, Desktop, source-code projects) explicit path rules
     and evidence-backed developer artifacts (node_modules next to package.json,
     a folder containing pyvenv.cfg, target/ next to Cargo.toml…) may create REVIEW items.
  4. When a rule cannot be verified it does not match; uncertain things go to REVIEW.

Path patterns use "/" and {ANCHOR} placeholders (see platform_info.Environment._anchors).
"*" matches exactly one path segment.
"""

import os
import re

from .platform_info import OS, is_under, norm

GB = 1024 ** 3
MB = 1024 ** 2

# Files whose loss would be real data loss. Caches holding many of these get downgraded.
VALUABLE_EXT = frozenset("""
.csv .tsv .parquet .feather .arrow .h5 .hdf5 .mat .npy .npz .jsonl .tfrecord .nc .fits
.edf .bdf .tdms .lvm .xdf .sav .dta .rds .rdata .xlsx .xls .ods .sqlite .sqlite3 .db .mdb
.accdb .mdf .ldf .ibd
.wav .flac .aiff .aif .alac .ape .dsf .mp3 .ogg .m4a .opus .wv
.pt .pth .ckpt .onnx .tflite .safetensors .gguf .ggml .keras .pb .mlmodel .engine
.vdi .vmdk .vhd .vhdx .qcow2 .ova .ovf .vmem .hdd .pvm .utm
.pst .ost .mbox .eml .olm
.cr2 .cr3 .nef .arw .dng .raf .orf .rw2 .heic .psd .psb .tif .tiff .jpg .jpeg .png
.mov .mp4 .mkv .avi .mts .m2ts .braw .r3d
.kdbx .key .pem .pfx .p12 .gpg .asc .ovpn
""".split())

INSTALLER_EXT = (".exe", ".msi", ".msix", ".appx", ".dmg", ".pkg", ".iso", ".img", ".zip",
                 ".7z", ".rar", ".tar", ".gz", ".tgz", ".xz", ".bz2", ".zst", ".deb", ".rpm",
                 ".appimage", ".flatpak", ".snap", ".apk", ".xapk", ".vsix", ".whl", ".jar")

# Folders that indicate a source-code project (soft lock: the project is NEVER,
# regenerable build artifacts inside it may be REVIEW).
PROJECT_MARKERS = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "cargo.toml", "pom.xml",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    "cmakelists.txt", "go.mod", "pubspec.yaml", "composer.json", "gemfile", "mix.exs",
    "stack.yaml", "cabal.project", "build.zig", "deno.json", "meson.build", "platformio.ini",
    "package.swift", ".project", ".cproject", ".git", ".hg", ".svn",
}
PROJECT_SUFFIXES = (".csproj", ".fsproj", ".vbproj", ".sln", ".xcodeproj", ".ioc", ".uvprojx",
                    ".xpr", ".uproject")


def is_project(node):
    if not node.flags:
        return False
    for f in node.flags:
        if f in PROJECT_MARKERS or f.endswith(PROJECT_SUFFIXES):
            return True
    return False


# --------------------------------------------------------------------------- rules
class Rule(object):
    def __init__(self, rid, cat, title, desc, path=None, name=None, name_re=None, marker=None,
                 oses=None, lock=None, kind="trash", cmd=None, tool=None, fallback="trash",
                 admin=False, procs=(), older=None, reclaim="full", per_child=False,
                 child_min=50 * MB, child_kind=None, needs=None, dev=False, root_child=False,
                 allow_git=None, allow_valuable=False, default=None, note=None, warn=None,
                 big_note=None):
        self.id = rid
        self.cat = cat              # "safe" | "review" | "never"
        self.title = title
        self.desc = desc
        self.path = path
        self.name = name.lower() if name else None
        self.name_re = re.compile(name_re, re.I) if name_re else None
        self.marker = marker
        self.oses = oses
        self.lock = lock            # never rules: "hard" | "exact" | "soft"
        self.kind = kind            # trash | trash_children | trash_old | command | advice | report
        self.cmd = cmd              # str or callable(node) -> str (already shell-quoted)
        self.tool = tool            # executable that must exist for cmd
        self.fallback = fallback    # kind used when tool is missing (None = skip)
        self.admin = admin
        self.procs = tuple(procs)   # skip if any of these processes are running
        self.older = older          # trash_old: days without modification
        self.reclaim = reclaim      # full | partial | unknown
        self.per_child = per_child
        self.child_min = child_min
        self.child_kind = child_kind
        self.needs = needs          # callable(node) -> evidence str | None
        self.dev = dev
        self.root_child = root_child
        # caches legitimately contain git checkouts (cargo, uv, go, pre-commit)
        self.allow_git = (path is not None) if allow_git is None else allow_git
        self.allow_valuable = allow_valuable
        self.default = default
        self.note = note
        self.warn = warn
        self.big_note = big_note    # never rules: advice for big children (games)
        self.explicit = path is not None
        self.abs = None             # expanded absolute path (set by RuleSet)


# ------------------------------------------------------------------ shell quoting
def q_sh(s):
    return "'" + s.replace("'", "'\\''") + "'"


def q_ps(s):
    for ch in ("'", "‘", "’", "‚", "‛"):
        s = s.replace(ch, ch + ch)
    return "'" + s + "'"


Q = q_ps if OS == "windows" else q_sh


def _join(*parts):
    return os.path.join(*parts)


# ----------------------------------------------------------------- evidence checks
def parent_has(*markers):
    def check(node):
        p = node.parent
        if p is None or not p.flags:
            return None
        for m in markers:
            if m.startswith("*"):
                for f in p.flags:
                    if f.endswith(m[1:]):
                        return "üst klasörde %s var" % f
            elif m in p.flags:
                return "üst klasörde %s var" % m
        return None
    return check


def parent_has_dirs(*markers):
    return parent_has(*markers)


def self_has(marker):
    def check(node):
        return ("klasörde %s var" % marker) if node.has(marker) else None
    return check


def both(*checks):
    def check(node):
        ev = []
        for c in checks:
            r = c(node)
            if r is None:
                return None
            ev.append(r)
        return "; ".join(ev)
    return check


PY_PROJECT = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile")
JS_PROJECT = ("package.json",)
ANY_BUILD = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
             "cmakelists.txt", "package.json", "pyproject.toml", "setup.py", "pubspec.yaml",
             "meson.build", "build.zig", "makefile", "platformio.ini")

BROWSERS = ("chrome", "msedge", "brave", "firefox", "vivaldi", "opera")
CHROMIUM_CACHES = ("Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache",
                   "DawnWebGPUCache")


def _cmd(text, tool):
    return {"cmd": text, "tool": tool}


def build_rules(env, config):
    """Return the rule list for the current OS. Order inside each group matters only
    for ties; priority between groups is decided in classify.py."""
    a = env.anchors
    rules = []

    home_key = norm(env.home)

    def R(*args, **kw):
        oses = kw.get("oses")
        if oses and OS not in oses:
            return
        if args[0] == "system" and kw.get("path") and "{" not in kw["path"] and \
                is_under(home_key, norm(kw["path"])):
            return  # e.g. /home or /root: the user's own home is classified by its own rules
        rules.append(Rule(*args, **kw))

    W, M, L = ("windows",), ("macos",), ("linux",)
    WM, ML = ("windows", "macos"), ("macos", "linux")

    # =================================================================== NEVER
    # --- user configured and cloud-synced paths (hard)
    for p in config.get("never_paths", []):
        R("user-never", "never", "Senin dokunulmaz klasörün", "Ayarlarda NEVER olarak "
          "işaretledin (config never_paths).", path=os.path.normpath(os.path.expanduser(p)),
          lock="hard")
    for p, provider in env.cloud_roots:
        R("cloud", "never", "Bulut senkron klasörü (%s)" % provider,
          "Senkronize klasör: lokalde silmek buluttan da siler. Yer açmak için "
          "%s uygulamasında 'Yer aç / Free up space' (çevrimiçi-yalnız) kullan." % provider,
          path=p, lock="hard", kind="report")
    for role, label in (("pictures", "Resimler"), ("videos", "Videolar"), ("music", "Müzik")):
        for p in env.known.get(role, []):
            R("personal-media", "never", label, "Kişisel medya klasörü (fotoğraf, video, müzik).",
              path=p, lock="hard")
    for role, label in (("documents", "Belgeler"), ("desktop", "Masaüstü")):
        for p in env.known.get(role, []):
            R("personal-docs", "never", label, "Kişisel dosyalar. İçindeki projelerde sadece "
              "kanıtı olan build/bağımlılık klasörleri REVIEW olarak önerilir.", path=p,
              lock="soft")

    # --- games, launchers, emulators, saves (hard)
    games = ("steamapps", "steam", "steamlibrary", "epic games", "epic games launcher",
             "riot games", "riot client", "battle.net", "blizzard entertainment", "ea games",
             "ea desktop", "electronic arts", "origin games", "ubisoft", "ubisoft game launcher",
             "xboxgames", "gog games", "gog galaxy", "rockstar games", "my games", "saved games",
             "retroarch", "dolphin emulator", "yuzu", "ryujinx", "suyu", "sudachi", "pcsx2",
             "rpcs3", "cemu", "citra", "lime3ds", "duckstation", "ppsspp", "xemu", "xenia",
             "melonds", "desmume", "mame", "roms", ".minecraft", "minecraft", "prismlauncher",
             "heroic", "lutris", "playnite", "amazon games", "itch", "bottles", "games")
    for g in games:
        R("game", "never", "Oyun / oyun kaydı", "Oyun, launcher, emülatör ya da oyun kaydı. "
          "Yer gerekiyorsa oyunu kendi launcher'ından kaldır.", name=g, lock="hard",
          big_note="Bu oyunu kendi launcher'ından (Steam/Epic/…) kaldırabilirsin; script "
          "oyunlara asla dokunmaz.")
    if OS == "windows":
        R("game-saves", "never", "AppData\\LocalLow (oyun kayıtları)",
          "Unity ve birçok oyun kayıtlarını burada tutar. Sadece bilinen GPU shader cache "
          "yolları önerilir.", path="{LOCALLOW}", lock="exact")

    # --- secrets, credentials, password managers (hard)
    for n in (".ssh", ".gnupg", ".password-store", ".aws", ".azure", ".kube", ".gcloud",
              ".pki", ".docker", ".terraform.d", "keyrings", "keychains", "1password",
              "bitwarden", "keepass", "keepassxc", "lastpass", "dashlane", "enpass", "protect",
              "credentials", "systemcertificates", "crypto", "vault", ".vault", "gnupg",
              "certificates", "licenses", "license"):
        R("secrets", "never", "Anahtarlar / şifreler / lisanslar", "SSH/GPG anahtarları, "
          "şifre yöneticileri, sertifikalar veya lisanslar. Kaybı geri alınamaz.", name=n,
          lock="hard")

    # --- VMs, containers with volumes, databases (hard)
    for n in ("virtualbox vms", "virtual machines", "virtual hard disks", "parallels", "vmware",
              ".vagrant.d", ".lima", ".colima", "multipass", "com.utmapp.utm", "wsl",
              "com.docker.docker", "docker desktop", "postgres", "postgresql", "mysql",
              "mariadb", "mongodb", "redis", "influxdb", "neo4j", "elasticsearch"):
        R("vm-db", "never", "Sanal makine / Docker verisi / veritabanı",
          "Sanal disk, Docker volume'ları veya veritabanı dosyaları içerir.", name=n,
          lock="hard")
    R("wsl", "never", "WSL dağıtımı", "WSL Linux dağıtımının sanal diski (ext4.vhdx).",
      name_re=r"^CanonicalGroupLimited|^TheDebianProject|^\w+\.(Ubuntu|Debian|Kali|openSUSE)",
      lock="hard", oses=W)
    R("docker-wsl", "never", "Docker Desktop verisi", "Docker image, container ve VOLUME "
      "verisini taşıyan sanal disk. Image'lar için aşağıdaki Docker önerilerine bak.",
      path="{LOCALAPPDATA}/Docker", lock="hard", oses=W)
    R("docker-rootless", "never", "Docker verisi (rootless)", "Docker volume'ları içerir.",
      path="{XDG_DATA}/docker", lock="hard", oses=L)
    R("docker-system", "never", "Docker verisi", "Docker volume'ları içerir.",
      path="/var/lib/docker", lock="hard", oses=L)

    # --- mail archives (hard)
    for n in ("thunderbird", ".thunderbird", "outlook", "outlook files", "mail", "messages",
              "com.apple.mail", "evolution", "mailspring", "postbox", "em client"):
        R("mail", "never", "E-posta arşivi", "E-posta / mesaj arşivi (.pst/.ost/mbox).",
          name=n, lock="hard")

    # --- photo libraries, version control internals (hard)
    R("photo-lib", "never", "Fotoğraf kütüphanesi", "Fotoğraf/Lightroom kütüphanesi.",
      name_re=r"\.(photoslibrary|lrdata|lrcat-data|aplibrary|photolibrary)$", lock="hard")
    for n in (".git", ".hg", ".svn"):
        R("vcs", "never", "Sürüm kontrol verisi", "Git/Mercurial/SVN geçmişi. Commit edilmemiş "
          "veya push edilmemiş işler burada olabilir.", name=n, lock="hard")

    # --- trash bins: report only
    R("recycle", "never", "Geri Dönüşüm Kutusu", "Çöp kutusundaki öğeler. Boşaltmak sana "
      "kalmış; script buna dokunmaz.", name="$recycle.bin", lock="hard", kind="report",
      oses=W)
    R("trash", "never", "Çöp Kutusu", "Çöp kutusundaki öğeler. Boşaltmak sana kalmış; script "
      "buna dokunmaz.", path="{HOME}/.Trash", lock="hard", kind="report", oses=M)
    R("trash", "never", "Çöp Kutusu", "Çöp kutusundaki öğeler. Boşaltmak sana kalmış; script "
      "buna dokunmaz.", path="{XDG_DATA}/Trash", lock="hard", kind="report", oses=L)
    R("sysvol", "never", "System Volume Information", "Sistem geri yükleme noktaları. "
      "Sistem Koruması ayarlarından yönetilir.", name="system volume information",
      lock="hard", oses=W)

    # --- system and application folders (exact: only explicit rules inside)
    if OS == "windows":
        for key, title in (("WINDIR", "Windows"), ("PROGRAMFILES", "Program Files"),
                           ("PROGRAMFILES86", "Program Files (x86)"),
                           ("PROGRAMDATA", "ProgramData")):
            R("system", "never", title, "İşletim sistemi / kurulu programlar. Programlar kendi "
              "uninstaller'ı ile kaldırılır.", path="{%s}" % key, lock="exact")
        for n in ("windows", "program files", "program files (x86)", "programdata", "users",
                  "recovery", "boot", "efi", "$winreagent", "$windows.~bt", "$windows.~ws",
                  "$sysreset", "perflogs", "intel", "amd", "nvidia", "drivers", "msocache",
                  "config.msi", "onedrivetemp", "documents and settings"):
            R("system", "never", "Sistem klasörü", "Sürücü kökündeki sistem klasörü.",
              name=n, root_child=True, lock="exact")
        R("appdata", "never", "AppData", "Uygulama verileri ve ayarları. Sadece bilinen "
          "cache yolları önerilir.", path="{HOME}/AppData", lock="exact")
        R("appdata", "never", "AppData\\Roaming (ayarlar)", "Uygulama ayarları ve profilleri.",
          path="{APPDATA}", lock="exact")
        R("appdata", "never", "AppData\\Local", "Uygulama verileri. Sadece bilinen cache "
          "yolları önerilir.", path="{LOCALAPPDATA}", lock="exact")
        R("store-apps", "never", "Store uygulama verileri", "Microsoft Store uygulamalarının "
          "verileri (WSL, oyun kayıtları dahil olabilir).", path="{LOCALAPPDATA}/Packages",
          lock="exact")
        R("user-programs", "never", "Kullanıcı programları", "Kurulu programlar.",
          path="{LOCALAPPDATA}/Programs", lock="exact")
    elif OS == "macos":
        for p in ("/System", "/Library", "/Applications", "/usr", "/opt", "/private",
                  "{HOME}/Applications"):
            R("system", "never", "Sistem / uygulamalar", "macOS sistemi veya kurulu "
              "uygulamalar. Uygulamalar Finder'dan / kendi uninstaller'ıyla kaldırılır.",
              path=p, lock="exact")
        R("library", "never", "~/Library", "Uygulama verileri ve ayarları. Sadece bilinen "
          "cache yolları önerilir.", path="{LIBRARY}", lock="exact")
        for n in ("keychains", "safari", "photos", "group containers", "containers",
                  "mobile documents", "cloudstorage", "accounts", "cookies", "preferences",
                  "application scripts", "passes", "personas", "sharing", "calendars",
                  "contacts", "notes", "reminders", "voice memos"):
            pass  # covered by the ~/Library exact lock; listed here for documentation
    else:
        for p in ("/usr", "/etc", "/var", "/opt", "/boot", "/srv", "/root", "/snap", "/bin",
                  "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/home", "/nix", "/gnu"):
            R("system", "never", "Sistem klasörü", "İşletim sistemi dosyaları. Paket "
              "yöneticisiyle yönetilir.", path=p, lock="exact")
        R("config", "never", "~/.config (ayarlar)", "Uygulama ayarları ve tarayıcı profilleri.",
          path="{XDG_CONFIG}", lock="exact")
        R("data", "never", "~/.local/share", "Uygulama verileri. Sadece bilinen yollar "
          "önerilir.", path="{XDG_DATA}", lock="exact")
        R("browser", "never", "Firefox profili", "Tarayıcı profili.", path="{HOME}/.mozilla",
          lock="exact")

    # --- browser profiles (exact: only explicit cache rules inside)
    for p in ("{LOCALAPPDATA}/Google/Chrome/User Data", "{LOCALAPPDATA}/Microsoft/Edge/User Data",
              "{LOCALAPPDATA}/BraveSoftware/Brave-Browser/User Data",
              "{LOCALAPPDATA}/Vivaldi/User Data", "{LOCALAPPDATA}/Mozilla",
              "{APPDATA}/Mozilla", "{APPDATA}/Opera Software"):
        R("browser", "never", "Tarayıcı profili", "Şifreler, yer imleri, geçmiş. Sadece "
          "içindeki cache klasörleri önerilir.", path=p, lock="exact", oses=W)

    # =================================================================== SAFE
    def S(rid, title, desc, path, **kw):
        kw.setdefault("oses", None)
        R(rid, "safe", title, desc, path=path, **kw)

    # --- package manager caches
    npm = _cmd("npm cache clean --force", "npm")
    S("npm", "npm cache", "npm'in indirme cache'i; gerektiğinde yeniden indirilir.",
      "{HOME}/.npm/_cacache", cmd=npm, kind="command", oses=ML)
    S("npm", "npm cache", "npm'in indirme cache'i; gerektiğinde yeniden indirilir.",
      "{LOCALAPPDATA}/npm-cache/_cacache", cmd=npm, kind="command", oses=W)
    S("npm-logs", "npm logları", "Eski npm log dosyaları.", "{HOME}/.npm/_logs", oses=ML,
      kind="trash_children")
    S("npm-npx", "npx cache", "npx'in geçici paket kurulumları.", "{HOME}/.npm/_npx", oses=ML)
    S("npm-npx", "npx cache", "npx'in geçici paket kurulumları.",
      "{LOCALAPPDATA}/npm-cache/_npx", oses=W)
    yarn = _cmd("yarn cache clean", "yarn")
    S("yarn", "Yarn cache", "Yarn indirme cache'i; yeniden indirilebilir.",
      "{XDG_CACHE}/yarn", cmd=yarn, kind="command", oses=L)
    S("yarn", "Yarn cache", "Yarn indirme cache'i; yeniden indirilebilir.",
      "{CACHES}/Yarn", cmd=yarn, kind="command", oses=M)
    S("yarn", "Yarn cache", "Yarn indirme cache'i; yeniden indirilebilir.",
      "{LOCALAPPDATA}/Yarn/Cache", cmd=yarn, kind="command", oses=W)
    S("yarn-berry", "Yarn Berry cache", "Yarn 2+ global cache'i.", "{HOME}/.yarn/berry/cache",
      kind="trash_children")
    pnpm = _cmd("pnpm store prune", "pnpm")
    pnpm_desc = ("pnpm içerik deposu. 'pnpm store prune' sadece hiçbir projenin kullanmadığı "
                 "paketleri siler; açılacak alan gösterilenden az olabilir (hardlink).")
    for p, o in (("{XDG_DATA}/pnpm/store", L), ("{HOME}/Library/pnpm/store", M),
                 ("{LOCALAPPDATA}/pnpm/store", W), ("{HOME}/.pnpm-store", None)):
        S("pnpm", "pnpm store", pnpm_desc, p, cmd=pnpm, kind="command", fallback=None,
          reclaim="partial", oses=o, allow_git=True)
    R("pnpm", "safe", "pnpm store (sürücü kökü)", pnpm_desc, name=".pnpm-store",
      root_child=True, cmd=pnpm, kind="command", fallback=None, reclaim="partial")
    pip = _cmd("pip cache purge" if OS == "windows" else "pip3 cache purge",
               "pip" if OS == "windows" else "pip3")
    S("pip", "pip cache", "pip'in indirilmiş wheel/http cache'i; yeniden indirilir.",
      "{XDG_CACHE}/pip", cmd=pip, kind="command", oses=L)
    S("pip", "pip cache", "pip'in indirilmiş wheel/http cache'i; yeniden indirilir.",
      "{CACHES}/pip", cmd=pip, kind="command", oses=M)
    S("pip", "pip cache", "pip'in indirilmiş wheel/http cache'i; yeniden indirilir.",
      "{LOCALAPPDATA}/pip/Cache", cmd=pip, kind="command", oses=W)
    uv = _cmd("uv cache clean", "uv")
    uv_desc = ("uv paket cache'i. Ortamlar dosyaları hardlink ile paylaşabilir; açılacak alan "
               "gösterilenden az olabilir.")
    S("uv", "uv cache", uv_desc, "{HOME}/.cache/uv", cmd=uv, kind="command", oses=ML,
      reclaim="partial")
    S("uv", "uv cache", uv_desc, "{LOCALAPPDATA}/uv/cache", cmd=uv, kind="command", oses=W,
      reclaim="partial")
    for p, o in (("{XDG_CACHE}/pypoetry", L), ("{CACHES}/pypoetry", M),
                 ("{LOCALAPPDATA}/pypoetry/Cache", W)):
        S("poetry", "Poetry cache", "Poetry indirme cache'i.", p + "/cache", oses=o)
        S("poetry", "Poetry artifacts", "Poetry indirilmiş paket dosyaları.",
          p + "/artifacts", oses=o)
        R("poetry-venvs", "review", "Poetry sanal ortamları", "Poetry'nin proje ortamları. "
          "Silinirse 'poetry install' ile yeniden kurulur.", path=p + "/virtualenvs",
          per_child=True, child_min=20 * MB, oses=o)
    S("pipenv-cache", "pipenv cache", "pipenv indirme cache'i.", "{XDG_CACHE}/pipenv", oses=L)
    S("pre-commit", "pre-commit cache", "pre-commit hook ortamları; gerektiğinde yeniden "
      "kurulur.", "{HOME}/.cache/pre-commit", cmd=_cmd("pre-commit clean", "pre-commit"),
      kind="command", allow_git=True)
    gradle_desc = "Gradle bağımlılık ve build cache'i; build sırasında yeniden indirilir."
    S("gradle", "Gradle cache", gradle_desc + " Çalışan Gradle daemon'ları önce durdurulur.",
      "{HOME}/.gradle/caches", procs=("gradle", "GradleDaemon"))
    S("gradle-wrapper", "Gradle wrapper dağıtımları", "İndirilmiş Gradle sürümleri; "
      "projeler ihtiyaç duyunca yeniden indirir.", "{HOME}/.gradle/wrapper/dists")
    S("gradle-daemon", "Gradle daemon logları", "Gradle daemon log dosyaları.",
      "{HOME}/.gradle/daemon", kind="trash_old", older=7)
    nuget = _cmd("dotnet nuget locals global-packages --clear", "dotnet")
    S("nuget", "NuGet paketleri", "NuGet global paket klasörü; restore sırasında yeniden "
      "indirilir.", "{HOME}/.nuget/packages", cmd=nuget, kind="command")
    S("nuget-http", "NuGet http cache", "NuGet http cache'i.", "{LOCALAPPDATA}/NuGet/v3-cache",
      cmd=_cmd("dotnet nuget locals http-cache --clear", "dotnet"), kind="command", oses=W)
    S("nuget-http", "NuGet http cache", "NuGet http cache'i.", "{HOME}/.local/share/NuGet/v3-cache",
      cmd=_cmd("dotnet nuget locals http-cache --clear", "dotnet"), kind="command", oses=ML)
    S("nuget-old", "NuGet eski cache", "Eski NuGet cache klasörü.", "{LOCALAPPDATA}/NuGet/Cache",
      oses=W)
    for sub in ("registry/cache", "registry/src", "git/checkouts", "git/db"):
        S("cargo", "Cargo %s" % sub, "Cargo'nun indirilmiş crate'leri; build sırasında yeniden "
          "indirilir.", "{HOME}/.cargo/" + sub, allow_git=True)
    go_cache = _cmd("go clean -cache", "go")
    S("go-build", "Go build cache", "Go derleme cache'i.", "{XDG_CACHE}/go-build", cmd=go_cache,
      kind="command", oses=L)
    S("go-build", "Go build cache", "Go derleme cache'i.", "{CACHES}/go-build", cmd=go_cache,
      kind="command", oses=M)
    S("go-build", "Go build cache", "Go derleme cache'i.", "{LOCALAPPDATA}/go-build",
      cmd=go_cache, kind="command", oses=W)
    S("go-mod", "Go modül cache'i", "İndirilmiş Go modülleri; 'go clean -modcache' ile "
      "temizlenir, build'de yeniden iner.", "{HOME}/go/pkg/mod",
      cmd=_cmd("go clean -modcache", "go"), kind="command", fallback=None, allow_git=True)
    for p, o in (("{XDG_CACHE}/deno", L), ("{CACHES}/deno", M), ("{LOCALAPPDATA}/deno", W)):
        S("deno", "Deno cache", "Deno modül cache'i.", p, oses=o)
    S("bun", "Bun cache", "Bun paket cache'i.", "{HOME}/.bun/install/cache",
      cmd=_cmd("bun pm cache rm", "bun"), kind="command")
    composer = _cmd("composer clear-cache", "composer")
    for p, o in (("{XDG_CACHE}/composer", L), ("{CACHES}/composer", M),
                 ("{LOCALAPPDATA}/Composer", W), ("{HOME}/.composer/cache", None)):
        S("composer", "Composer cache", "PHP Composer indirme cache'i.", p, cmd=composer,
          kind="command", oses=o)
    for p, o in (("{XDG_CACHE}", L), ("{CACHES}", M), ("{LOCALAPPDATA}", W)):
        S("electron", "Electron indirme cache'i", "electron/electron-builder indirmeleri.",
          p + ("/electron" if OS != "windows" else "/electron/Cache"), oses=o)
        S("electron-builder", "electron-builder cache", "electron-builder indirmeleri.",
          p + "/electron-builder", oses=o)
        S("node-gyp", "node-gyp header cache", "Native modül derleme header'ları.",
          p + "/node-gyp", oses=o)
        R("playwright", "review", "Playwright tarayıcıları", "Testler için indirilmiş "
          "tarayıcılar. Silinirse 'npx playwright install' ile yeniden iner.",
          path=p + "/ms-playwright", per_child=True, child_min=20 * MB, oses=o)
        R("cypress", "review", "Cypress binary'leri", "Cypress test runner sürümleri.",
          path=p + "/Cypress", per_child=True, child_min=20 * MB, oses=o)
    R("puppeteer", "review", "Puppeteer tarayıcıları", "Puppeteer için indirilmiş Chrome.",
      path="{HOME}/.cache/puppeteer")
    S("arduino-staging", "Arduino indirme cache'i", "Arduino IDE'nin indirdiği arşivler.",
      "{LOCALAPPDATA}/Arduino15/staging", oses=W)
    S("arduino-staging", "Arduino indirme cache'i", "Arduino IDE'nin indirdiği arşivler.",
      "{HOME}/.arduino15/staging", oses=L)
    S("arduino-staging", "Arduino indirme cache'i", "Arduino IDE'nin indirdiği arşivler.",
      "{LIBRARY}/Arduino15/staging", oses=M)
    S("android-cache", "Android cache", "Android araçlarının cache'i.", "{HOME}/.android/cache")
    S("conda-pkgs-user", "conda pkgs cache", "conda paket cache'i. Ortamlar hardlink "
      "kullanabilir; açılacak alan daha az olabilir.", "{HOME}/.conda/pkgs",
      cmd=_cmd("conda clean --all -y", "conda"), kind="command", fallback=None,
      reclaim="partial")

    # conda/mamba installations: <base>/pkgs next to <base>/conda-meta
    def conda_exe(base):
        if OS == "windows":
            return _join(base, "Scripts", "conda.exe")
        return _join(base, "bin", "conda")

    def run_exe(exe):
        return ("& " if OS == "windows" else "") + Q(exe)

    def conda_cmd(node):
        return "%s clean --all -y" % run_exe(conda_exe(node.parent.path))

    def conda_tool(node):
        return conda_exe(node.parent.path)

    R("conda-pkgs", "safe", "conda pkgs cache", "conda/mamba'nın indirilmiş paket arşivleri ve "
      "kullanılmayan paketler ('conda clean --all'). Ortamlar hardlink kullanabilir; açılacak "
      "alan daha az olabilir.", name="pkgs", needs=parent_has("conda-meta"), cmd=conda_cmd,
      tool=conda_tool, kind="command", fallback=None, reclaim="partial")

    # --- browsers & electron app caches
    if OS == "windows":
        for base, label, proc in (
                ("{LOCALAPPDATA}/Google/Chrome/User Data", "Chrome", "chrome"),
                ("{LOCALAPPDATA}/Microsoft/Edge/User Data", "Edge", "msedge"),
                ("{LOCALAPPDATA}/BraveSoftware/Brave-Browser/User Data", "Brave", "brave"),
                ("{LOCALAPPDATA}/Vivaldi/User Data", "Vivaldi", "vivaldi")):
            for c in CHROMIUM_CACHES:
                S("browser-cache", "%s cache (%s)" % (label, c), "Tarayıcı cache'i (profil değil); "
                  "site verisi, şifre ve geçmiş etkilenmez. Tarayıcı açıksa atlanır.",
                  base + "/*/" + c, kind="trash_children", procs=(proc,))
            for c in ("ShaderCache", "GrShaderCache", "GraphiteDawnCache", "component_crx_cache"):
                S("browser-cache", "%s %s" % (label, c), "Tarayıcının GPU/bileşen cache'i.",
                  base + "/" + c, kind="trash_children", procs=(proc,))
        S("browser-cache", "Firefox cache", "Firefox disk cache'i (profil değil).",
          "{LOCALAPPDATA}/Mozilla/Firefox/Profiles/*/cache2", kind="trash_children",
          procs=("firefox",))
        S("browser-cache", "Opera cache", "Opera disk cache'i.",
          "{LOCALAPPDATA}/Opera Software/Opera Stable/Cache", kind="trash_children",
          procs=("opera",))
        S("inetcache", "Windows INetCache", "Eski IE/WebView internet cache'i.",
          "{LOCALAPPDATA}/Microsoft/Windows/INetCache", kind="trash_children",
          procs=("msedge",))
        for app, proc in (("Code", "Code"), ("Code - Insiders", "Code - Insiders"),
                          ("Cursor", "Cursor"), ("Slack", "slack"), ("discord", "Discord"),
                          ("Microsoft/Teams", "Teams"), ("Notion", "Notion"),
                          ("Postman", "Postman"), ("Spotify", "Spotify")):
            for c in ("Cache", "Code Cache", "GPUCache", "CachedData", "CachedExtensionVSIXs",
                      "DawnCache", "logs"):
                S("app-cache", "%s %s" % (app.split("/")[-1], c), "Uygulamanın cache/log "
                  "klasörü; ayarlar ve veriler etkilenmez. Uygulama açıksa atlanır.",
                  "{APPDATA}/%s/%s" % (app, c), kind="trash_children", procs=(proc,))
        S("spotify-cache", "Spotify cache", "Spotify akış cache'i (çevrimdışı indirmeler değil).",
          "{LOCALAPPDATA}/Spotify/Storage", kind="trash_children", procs=("Spotify",))
    elif OS == "linux":
        for c, proc in (("google-chrome", "chrome"), ("chromium", "chromium"),
                        ("BraveSoftware", "brave"), ("microsoft-edge", "msedge"),
                        ("vivaldi", "vivaldi"), ("mozilla", "firefox"), ("opera", "opera")):
            S("browser-cache", "Tarayıcı cache (%s)" % c, "Tarayıcı disk cache'i; profil "
              "~/.config veya ~/.mozilla altında ve etkilenmez. Tarayıcı açıksa atlanır.",
              "{XDG_CACHE}/" + c, kind="trash_children", procs=(proc,))
        for app, proc in (("Code", "code"), ("Code - Insiders", "code-insiders"),
                          ("Cursor", "cursor"), ("Slack", "slack"), ("discord", "Discord")):
            for c in ("Cache", "Code Cache", "GPUCache", "CachedData", "CachedExtensionVSIXs",
                      "logs"):
                S("app-cache", "%s %s" % (app, c), "Uygulamanın cache/log klasörü. Uygulama "
                  "açıksa atlanır.", "{XDG_CONFIG}/%s/%s" % (app, c), kind="trash_children",
                  procs=(proc,))
        S("thumbnails", "Küçük resim cache'i", "Dosya yöneticisi önizlemeleri; yeniden "
          "oluşturulur.", "{XDG_CACHE}/thumbnails", kind="trash_children")
        for c in ("mesa_shader_cache", "mesa_shader_cache_db", "nvidia", "radv_builtin_shaders",
                  "fontconfig"):
            S("gpu-cache", "GPU/font cache (%s)" % c, "Shader/font cache'i; yeniden oluşturulur.",
              "{XDG_CACHE}/" + c, kind="trash_children")
        S("tmp", "/tmp (7 günden eski)", "Geçici dosyalar. Sadece sana ait ve son 7 günde "
          "değişmemiş öğeler; açık programların dosyaları atlanır.", "/tmp", kind="trash_old",
          older=7)
        S("jetbrains-logs", "JetBrains logları", "IDE log dosyaları.",
          "{XDG_CACHE}/JetBrains/*/log", kind="trash_old", older=7)
    elif OS == "macos":
        for app, proc in (("Code", "Code"), ("Code - Insiders", "Code - Insiders"),
                          ("Cursor", "Cursor"), ("Slack", "Slack"), ("discord", "Discord"),
                          ("Microsoft Teams", "Teams"), ("Notion", "Notion")):
            for c in ("Cache", "Code Cache", "GPUCache", "CachedData", "CachedExtensionVSIXs",
                      "logs"):
                S("app-cache", "%s %s" % (app, c), "Uygulamanın cache/log klasörü. Uygulama "
                  "açıksa atlanır.", "{APPSUPPORT}/%s/%s" % (app, c), kind="trash_children",
                  procs=(proc,))
        for c, proc, label in (("Google", "Google Chrome", "Chrome"), ("Firefox", "firefox", "Firefox"),
                               ("BraveSoftware", "Brave Browser", "Brave"),
                               ("Microsoft Edge", "Microsoft Edge", "Edge"),
                               ("com.microsoft.edgemac", "Microsoft Edge", "Edge"),
                               ("com.operasoftware.Opera", "Opera", "Opera"),
                               ("Vivaldi", "Vivaldi", "Vivaldi")):
            S("browser-cache", "%s cache" % label, "Tarayıcı disk cache'i (profil "
              "Application Support altında ve etkilenmez). Tarayıcı açıksa atlanır.",
              "{CACHES}/" + c, kind="trash_children", procs=(proc,))
        S("xcode-dd", "Xcode DerivedData", "Xcode build ara çıktıları; bir sonraki build'de "
          "yeniden oluşturulur.", "{LIBRARY}/Developer/Xcode/DerivedData",
          kind="trash_children", procs=("Xcode",))
        S("sim-caches", "Simulator cache'leri", "iOS Simulator cache'i.",
          "{LIBRARY}/Developer/CoreSimulator/Caches", kind="trash_children",
          procs=("Simulator",))
        S("mac-logs", "Kullanıcı logları (30+ gün)", "~/Library/Logs içindeki 30 günden eski "
          "loglar ve crash raporları.", "{LIBRARY}/Logs", kind="trash_old", older=30)
        S("mac-temp", "Kullanıcı temp klasörü (7+ gün)", "macOS'un kullanıcıya ait geçici "
          "klasörü; son 7 günde değişmeyen öğeler.", "{TEMP}", kind="trash_old", older=7)
        R("homebrew", "review", "Homebrew cache", "İndirilmiş Homebrew arşivleri. "
          "'brew cleanup -s' cache'i temizler ve kurulu paketlerin ESKİ sürümlerini de kaldırır.",
          path="{CACHES}/Homebrew", cmd=_cmd("brew cleanup -s", "brew"), kind="command",
          fallback=None)
        R("mac-caches", "review", "~/Library/Caches", "Apple'a göre yeniden oluşturulabilir "
          "cache'ler. Uygulamayı tanımadığım için REVIEW; silinirse uygulama yeniden indirir/"
          "oluşturur.", path="{CACHES}", per_child=True, child_min=20 * MB,
          child_kind="trash_children")

    # --- GPU shader caches (Windows)
    for p in ("{LOCALAPPDATA}/NVIDIA/DXCache", "{LOCALAPPDATA}/NVIDIA/GLCache",
              "{LOCALAPPDATA}/NVIDIA Corporation/NV_Cache", "{LOCALAPPDATA}/AMD/DxCache",
              "{LOCALAPPDATA}/AMD/DxcCache", "{LOCALAPPDATA}/AMD/GLCache",
              "{LOCALAPPDATA}/AMD/VkCache", "{LOCALAPPDATA}/D3DSCache",
              "{LOCALAPPDATA}/Intel/ShaderCache", "{LOCALLOW}/NVIDIA/PerDriverVersion/DXCache",
              "{LOCALLOW}/NVIDIA/PerDriverVersion/GLCache", "{LOCALLOW}/Intel/ShaderCache"):
        S("shader-cache", "GPU shader cache (%s)" % p.split("/", 1)[1], "Sürücünün derlenmiş "
          "shader cache'i; oyun/uygulama ilk açılışta yeniden oluşturur. Kilitli dosyalar "
          "atlanır.", p, kind="trash_children", oses=W)

    # --- temp, crash dumps, logs (Windows)
    if OS == "windows":
        S("temp", "Kullanıcı TEMP (7+ gün)", "%TEMP% içindeki, son 7 günde değişmemiş öğeler. "
          "Açık programların kilitlediği dosyalar atlanır.", "{TEMP}", kind="trash_old",
          older=7)
        S("win-temp", "Windows\\Temp (7+ gün)", "Sistem temp klasörü; yönetici gerekir.",
          "{WINDIR}/Temp", kind="trash_old", older=7, admin=True)
        S("crashdumps", "Crash dump'lar", "Uygulama çökme dökümleri.",
          "{LOCALAPPDATA}/CrashDumps", kind="trash_children")
        for sub in ("ReportArchive", "ReportQueue"):
            S("wer", "Windows hata raporları (%s)" % sub, "Gönderilmiş/bekleyen hata raporları.",
              "{LOCALAPPDATA}/Microsoft/Windows/WER/" + sub, kind="trash_children")
            S("wer-sys", "Sistem hata raporları (%s)" % sub, "Yönetici gerekir.",
              "{PROGRAMDATA}/Microsoft/Windows/WER/" + sub, kind="trash_children", admin=True)
        S("minidump", "Minidump'lar", "Mavi ekran mini dökümleri; yönetici gerekir.",
          "{WINDIR}/Minidump", kind="trash_children", admin=True)
        S("memory-dmp", "MEMORY.DMP", "Son sistem çökmesinin tam bellek dökümü; yönetici "
          "gerekir.", "{WINDIR}/MEMORY.DMP", admin=True)
        S("cleanmgr", "Windows Disk Temizleme", "Windows Update cache'i, Delivery "
          "Optimization dosyaları, eski Windows kurulumu vb. için Windows'un kendi aracı. "
          "Pencere açılır; ne silineceğini sen seçersin ('Sistem dosyalarını temizle').",
          "{WINDIR}/SoftwareDistribution/Download",
          cmd=_cmd("Start-Process cleanmgr.exe -ArgumentList '/d %s'" % a["SYSDRIVE"][:2],
                   "cleanmgr.exe"), kind="command", fallback=None, reclaim="unknown")
        S("delivery-opt", "Delivery Optimization cache", "Windows Update eşler arası "
          "paylaşım cache'i; resmi cmdlet ile temizlenir (yönetici).",
          "{WINDIR}/ServiceProfiles/NetworkService/AppData/Local/Microsoft/Windows/"
          "DeliveryOptimization/Cache",
          cmd=_cmd("Delete-DeliveryOptimizationCache -Force", "Delete-DeliveryOptimizationCache"),
          kind="command", fallback=None, admin=True, reclaim="unknown")

    # =================================================================== REVIEW
    def V(rid, title, desc, **kw):
        R(rid, "review", title, desc, **kw)

    # --- developer artifacts inside projects (evidence-backed; allowed in soft locks)
    V("node_modules", "node_modules", "npm/yarn/pnpm bağımlılıkları; 'npm install' ile yeniden "
      "kurulur. Uzun süredir dokunulmamış projelerde iyi bir aday.", name="node_modules",
      needs=parent_has("package.json"), dev=True, allow_git=True)
    V("venv", "Python sanal ortamı", "Python venv; requirements/pyproject'ten yeniden "
      "kurulabilir. Elle kurulmuş paketler kaybolur.", marker="pyvenv.cfg",
      needs=self_has("pyvenv.cfg"), dev=True, allow_git=True)
    V("cargo-target", "Rust target/", "Cargo build çıktısı; 'cargo clean' ile resmi olarak "
      "temizlenir, sonraki build'de yeniden oluşur.", name="target",
      needs=parent_has("cargo.toml"), dev=True, kind="command", tool="cargo",
      cmd=lambda n: "cargo clean --manifest-path %s" % Q(_join(n.parent.path, "Cargo.toml")))
    V("maven-target", "Maven target/", "Maven build çıktısı; 'mvn package' ile yeniden "
      "oluşur.", name="target", needs=parent_has("pom.xml"), dev=True)
    for n in ("build", "dist", "out"):
        V("build-dir", "%s/ (build çıktısı)" % n, "Projenin build çıktısı; build komutuyla "
          "yeniden oluşur. Bazı projelerde elle yazılmış dosya içerebilir, kontrol et.",
          name=n, needs=parent_has(*ANY_BUILD), dev=True)
    for n in (".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache", ".angular", ".expo",
              ".docusaurus", ".vercel", ".output", ".astro", ".vite"):
        V("js-cache", "%s (framework cache)" % n, "JS framework build/cache klasörü; yeniden "
          "oluşturulur.", name=n, needs=parent_has("package.json", "angular.json"), dev=True)
    for n in (".tox", ".nox", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis"):
        V("py-cache", "%s" % n, "Python test/lint cache'i; yeniden oluşturulur.", name=n,
          dev=True)
    for n in ("bin", "obj"):
        V("dotnet-out", "%s/ (.NET build)" % n, ".NET build çıktısı; 'dotnet build' ile yeniden "
          "oluşur.", name=n, needs=parent_has("*.csproj", "*.fsproj", "*.vbproj"), dev=True)
    V("gradle-project", ".gradle (proje)", "Projenin Gradle cache'i.", name=".gradle",
      needs=parent_has("build.gradle", "build.gradle.kts", "settings.gradle",
                       "settings.gradle.kts"), dev=True)
    V("cxx", ".cxx (Android NDK)", "Android native build ara çıktıları.", name=".cxx",
      needs=parent_has("build.gradle", "build.gradle.kts"), dev=True)
    V("cmake-build", "CMake build klasörü", "CLion/CMake build çıktısı.",
      name_re=r"^cmake-build-", needs=parent_has("cmakelists.txt"), dev=True)
    V("pods", "Pods/ (CocoaPods)", "'pod install' ile yeniden kurulur.", name="Pods",
      needs=parent_has("podfile"), dev=True)
    V("dart-tool", ".dart_tool", "Flutter/Dart cache'i.", name=".dart_tool",
      needs=parent_has("pubspec.yaml"), dev=True)
    V("terraform", ".terraform", "Terraform provider indirmeleri; 'terraform init' ile geri "
      "gelir. State dosyaları burada değil, yine de kontrol et.", name=".terraform",
      needs=parent_has("*.tf"), dev=True)
    V("stack-work", ".stack-work", "Haskell Stack build çıktısı.", name=".stack-work",
      needs=parent_has("stack.yaml"), dev=True)
    V("dist-newstyle", "dist-newstyle", "Cabal build çıktısı.", name="dist-newstyle",
      needs=parent_has("cabal.project", "*.cabal"), dev=True)
    for n in ("zig-cache", ".zig-cache", "zig-out"):
        V("zig", n, "Zig build çıktısı.", name=n, needs=parent_has("build.zig"), dev=True)
    V("elixir-build", "_build (Elixir)", "Mix build çıktısı.", name="_build",
      needs=parent_has("mix.exs"), dev=True)
    V("pio", ".pio (PlatformIO)", "PlatformIO build çıktısı ve kütüphaneleri.", name=".pio",
      needs=parent_has("platformio.ini"), dev=True)
    for n in ("Debug", "Release"):
        V("embedded-build", "%s/ (gömülü build)" % n, "STM32CubeIDE/Eclipse CDT/Keil build "
          "çıktısı (.elf/.o); projeden yeniden derlenir.", name=n,
          needs=parent_has(".cproject", "*.ioc", "*.uvprojx", "*.csproj", "*.sln"), dev=True)
    V("vivado-cache", "Vivado cache (.cache/.Xil)", "Vivado proje cache'i; yeniden oluşur.",
      name_re=r"^(.+\.cache|\.Xil)$", needs=parent_has("*.xpr"), dev=True)
    V("vivado-runs", "Vivado .runs", "Sentez/implementasyon çıktıları (bitstream dahil). "
      "Yeniden üretmek saatler sürebilir; bitstream'i ayrıca sakladıysan silebilirsin.",
      name_re=r"\.runs$", needs=parent_has("*.xpr"), dev=True,
      warn="İçinde tek kopya bitstream (.bit) olabilir.")
    V("simulink", "slprj (Simulink cache)", "Simulink model referans/kod üretim cache'i.",
      name="slprj", dev=True)
    V("unity-library", "Unity Library/", "Unity proje cache'i (import edilmiş asset'ler). "
      "Proje açılınca yeniden oluşturulur (uzun sürebilir).", name="Library",
      needs=parent_has("projectsettings"), dev=True)
    for n in ("Intermediate", "DerivedDataCache"):
        V("unreal", "%s/ (Unreal)" % n, "Unreal Engine proje ara çıktısı; yeniden oluşur.",
          name=n, needs=parent_has("*.uproject"), dev=True)

    # --- environments & toolchains (explicit paths)
    V("conda-envs", "conda ortamı", "'conda env remove' ile resmi olarak kaldırılır; "
      "environment.yml'in yoksa paket listesi kaybolur (önce 'conda env export').",
      name="envs", needs=parent_has("conda-meta"), per_child=True, child_min=50 * MB,
      child_kind="command", fallback=None,
      cmd=lambda n: "%s env remove -y -p %s" % (run_exe(conda_exe(n.parent.parent.path)),
                                                 Q(n.path)),
      tool=lambda n: conda_exe(n.parent.parent.path))
    V("conda-envs", "conda ortamı (~/.conda/envs)", "'conda env remove' ile kaldırılır; "
      "önce 'conda env export' ile paket listesini saklayabilirsin.",
      path="{HOME}/.conda/envs", per_child=True, child_min=50 * MB, child_kind="command",
      cmd=lambda n: "conda env remove -y -p %s" % Q(n.path), tool="conda")
    V("pyenv", "pyenv Python sürümü", "Kurulu Python sürümü; resmi komut 'pyenv uninstall'.",
      path="{HOME}/.pyenv/versions", per_child=True, child_min=50 * MB, child_kind="command",
      cmd=lambda n: "pyenv uninstall -f %s" % Q(n.name), tool="pyenv", oses=ML)
    V("pyenv", "pyenv-win Python sürümleri", "Kurulu Python sürümleri.",
      path="{HOME}/.pyenv/pyenv-win/versions", per_child=True, child_min=50 * MB, oses=W)
    V("virtualenvs", "virtualenvwrapper/pipenv ortamları", "Sanal ortamlar; projeden yeniden "
      "kurulabilir.", path="{HOME}/.virtualenvs", per_child=True, child_min=20 * MB)
    V("pipenv-venvs", "pipenv ortamları", "pipenv sanal ortamları.",
      path="{XDG_DATA}/virtualenvs", per_child=True, child_min=20 * MB, oses=L)
    V("nvm", "nvm Node sürümleri", "Kurulu Node.js sürümleri; 'nvm uninstall' ile kaldırılır.",
      path="{HOME}/.nvm/versions/node", per_child=True, child_min=30 * MB, oses=ML)
    V("nvm", "nvm-windows Node sürümleri", "Kurulu Node.js sürümleri.", path="{APPDATA}/nvm",
      per_child=True, child_min=30 * MB, oses=W)
    V("rustup", "Rust toolchain", "Kurulu Rust sürümü; resmi komut 'rustup toolchain "
      "uninstall'. Projelerinin kullandığı sürümü silme.", path="{HOME}/.rustup/toolchains",
      per_child=True, child_min=100 * MB, child_kind="command",
      cmd=lambda n: "rustup toolchain uninstall %s" % Q(n.name), tool="rustup")
    V("maven", "Maven yerel deposu", "İndirilmiş Maven bağımlılıkları; çoğu yeniden iner ama "
      "'mvn install' ile yerelde kurduğun kendi artifact'ların da burada olabilir.",
      path="{HOME}/.m2/repository")
    V("jetbrains", "JetBrains IDE cache'leri", "Her IDE sürümü için cache/index; eski "
      "sürümlerin klasörleri güvenle gidebilir, güncel olanı silmek yeniden indekslemeye yol "
      "açar.", path={"windows": "{LOCALAPPDATA}/JetBrains", "macos": "{CACHES}/JetBrains",
                     "linux": "{XDG_CACHE}/JetBrains"}[OS], per_child=True, child_min=50 * MB)
    V("gradle-jdks", "Gradle JDK'ları", "Gradle toolchain'in indirdiği JDK'lar.",
      path="{HOME}/.gradle/jdks", per_child=True, child_min=50 * MB)

    # --- downloaded models (public caches; REVIEW, never SAFE)
    hf = os.environ.get("HF_HOME")
    for base in ([hf] if hf else []) + ["{HOME}/.cache/huggingface"]:
        V("hf-hub", "HuggingFace model", "HuggingFace hub'dan indirilmiş model; tekrar "
          "indirilebilir. Fine-tune ettiğin ağırlıklar burada değil, kendi klasörlerinde.",
          path=base + "/hub", per_child=True, child_min=20 * MB, allow_valuable=True)
        V("hf-datasets", "HuggingFace veri seti cache'i", "datasets kütüphanesinin indirip "
          "işlediği public veri setleri.", path=base + "/datasets", per_child=True,
          child_min=20 * MB, allow_valuable=True)
        S("hf-xet", "HuggingFace xet cache", "HF indirme parça cache'i.", base + "/xet",
          allow_valuable=True)
    V("torch-hub", "torch hub modeli", "torch.hub ile indirilmiş model/kod.",
      path="{HOME}/.cache/torch/hub", per_child=True, child_min=20 * MB, allow_valuable=True)
    V("keras", "Keras indirmeleri", "Keras'ın indirdiği model/veri setleri.",
      path="{HOME}/.keras", per_child=True, child_min=20 * MB, allow_valuable=True)
    V("whisper", "Whisper modelleri", "openai-whisper model dosyaları.",
      path="{HOME}/.cache/whisper", allow_valuable=True)
    V("lmstudio", "LM Studio modeli", "LM Studio'nun indirdiği modeller.",
      path="{HOME}/.lmstudio/models", per_child=True, child_min=50 * MB, allow_valuable=True)
    V("lmstudio", "LM Studio modeli", "LM Studio'nun indirdiği modeller.",
      path="{HOME}/.cache/lm-studio/models", per_child=True, child_min=50 * MB,
      allow_valuable=True)
    om = os.environ.get("OLLAMA_MODELS")
    for p in ([om] if om else []) + ["{HOME}/.ollama/models"]:
        V("ollama", "Ollama modelleri", "Ollama blob'ları modeller arasında paylaşılır; "
          "klasörü silme. 'ollama list' ile bak, 'ollama rm <model>' ile kaldır.", path=p,
          kind="advice", allow_valuable=True)

    # --- SDKs, emulators, toolchains
    sdk = {"windows": "{LOCALAPPDATA}/Android/Sdk", "macos": "{LIBRARY}/Android/sdk",
           "linux": "{HOME}/Android/Sdk"}[OS]
    V("android-images", "Android emülatör image'ı", "SDK Manager'dan da kaldırılabilir; "
      "gerekirse yeniden indirilir.", path=sdk + "/system-images/*", per_child=True,
      child_min=100 * MB)
    V("android-ndk", "Android NDK sürümü", "Eski NDK sürümleri.", path=sdk + "/ndk",
      per_child=True, child_min=100 * MB)
    V("android-avd", "Android sanal cihaz (AVD)", "Emülatör cihazı; içindeki uygulama "
      "verileri de silinir.", path="{HOME}/.android/avd", per_child=True, child_min=100 * MB)
    if OS == "macos":
        V("xcode-archives", "Xcode Archives", "Yayınladığın sürümlerin arşivleri ve dSYM "
          "dosyaları. Yeniden oluşturulamaz; crash sembolikasyonu için gerekebilir.",
          path="{LIBRARY}/Developer/Xcode/Archives", per_child=True, child_min=50 * MB)
        V("ios-devicesupport", "iOS DeviceSupport", "Bağlanan cihazlar için indirilen "
          "semboller; cihaz yeniden bağlanınca tekrar iner.",
          path="{LIBRARY}/Developer/Xcode/iOS DeviceSupport", per_child=True, child_min=100 * MB)
        V("simulators", "iOS Simulator cihazları", "Kullanılamayan (eski runtime) simülatörleri "
          "resmi komutla kaldırır: xcrun simctl delete unavailable.",
          path="{LIBRARY}/Developer/CoreSimulator/Devices",
          cmd=_cmd("xcrun simctl delete unavailable", "xcrun"), kind="command",
          fallback=None, reclaim="partial")
    V("stm32-repo", "STM32Cube firmware paketi", "CubeMX/CubeIDE'nin indirdiği firmware "
      "paketleri; gerekirse yeniden indirilir.", path="{HOME}/STM32Cube/Repository",
      per_child=True, child_min=50 * MB)
    V("arduino-packages", "Arduino board paketi", "Board çekirdekleri ve toolchain'ler; Board "
      "Manager'dan yeniden kurulur.", path={"windows": "{LOCALAPPDATA}/Arduino15/packages",
                                             "macos": "{LIBRARY}/Arduino15/packages",
                                             "linux": "{HOME}/.arduino15/packages"}[OS],
      per_child=True, child_min=50 * MB)
    uninstall = "Kurulu program; dosyaları silme, kendi uninstaller'ı ile kaldır " \
                "(Windows: Ayarlar > Uygulamalar)."
    if OS == "windows":
        for p in ("{PROGRAMFILES}/MATLAB", "{SYSDRIVE}/Program Files/MATLAB"):
            V("matlab", "MATLAB sürümü", uninstall + " Birden fazla sürüm varsa eskisini "
              "kaldırmayı düşün.", path=p, per_child=True, child_min=500 * MB, kind="advice")
        for p in ("{SYSDRIVE}/Xilinx/*", "{SYSDRIVE}/AMDDesignTools/*", "{SYSDRIVE}/intelFPGA",
                  "{SYSDRIVE}/intelFPGA_lite", "{SYSDRIVE}/ST", "{SYSDRIVE}/Keil_v5",
                  "{SYSDRIVE}/ti", "{SYSDRIVE}/NXP", "{SYSDRIVE}/Altium"):
            V("toolchain", "Geliştirme aracı / sürüm", uninstall + " Eski sürümleri kendi "
              "uninstaller'ı (veya Xilinx/AMD Installer, STM32CubeIDE uninstaller) ile "
              "kaldırabilirsin.", path=p, per_child=True, child_min=500 * MB, kind="advice")
        for p in ("{PROGRAMFILES}", "{PROGRAMFILES86}", "{LOCALAPPDATA}/Programs"):
            V("big-program", "Büyük kurulu program", uninstall, path=p, per_child=True,
              child_min=2 * GB, kind="advice")
        V("windows-old", "Windows.old (önceki Windows)", "Önceki Windows kurulumu; geri dönüş "
          "imkânı sağlar. Disk Temizleme > 'Önceki Windows yüklemeleri' ile kaldırılır.",
          path="{SYSDRIVE}/Windows.old", kind="advice")
        V("dism", "WinSxS bileşen deposu (DISM)", "Resmi komut: DISM /Online /Cleanup-Image "
          "/StartComponentCleanup. Yönetici gerekir; eski güncellemeler sonradan "
          "kaldırılamaz. Hardlink'ler yüzünden açılacak alan önceden ölçülemez.",
          path="{WINDIR}/WinSxS",
          cmd=_cmd("DISM.exe /Online /Cleanup-Image /StartComponentCleanup", "DISM.exe"),
          kind="command", fallback=None, admin=True, reclaim="unknown")
        V("ios-backup", "iPhone/iPad yedeği", "iTunes/Apple Devices yedekleri. Tek kopyaysa "
          "silme; cihazdan veya iCloud'dan güncel yedeğin olduğundan emin ol.",
          path="{APPDATA}/Apple Computer/MobileSync/Backup", per_child=True, child_min=50 * MB)
        V("ios-backup", "iPhone/iPad yedeği", "Apple Devices (Store) yedekleri.",
          path="{HOME}/Apple/MobileSync/Backup", per_child=True, child_min=50 * MB)
    elif OS == "macos":
        V("big-app", "Büyük uygulama", "Kurulu uygulama; çöp kutusuna sürüklemek yerine "
          "kendi uninstaller'ı varsa onu kullan.", path="/Applications", per_child=True,
          child_min=1 * GB, kind="advice")
        V("ios-backup", "iPhone/iPad yedeği", "Finder yedekleri. Tek kopyaysa silme; güncel "
          "yedeğin olduğundan emin ol.", path="{APPSUPPORT}/MobileSync/Backup", per_child=True,
          child_min=50 * MB)
    else:
        V("xdg-cache", "~/.cache alt klasörü", "XDG cache'i (yeniden oluşturulabilir olmalı). "
          "Uygulamayı tanımadığım için REVIEW.", path="{XDG_CACHE}", per_child=True,
          child_min=20 * MB, child_kind="trash_children")
        V("flatpak", "Flatpak kullanılmayan runtime'lar", "Resmi komut: flatpak uninstall "
          "--unused (kullanıcı kurulumları).", path="{XDG_DATA}/flatpak",
          cmd=_cmd("flatpak uninstall --user --unused -y", "flatpak"), kind="command",
          fallback=None, reclaim="unknown")
        V("snap-cache", "Snap/APT/journal (sudo)", "Sistem cache'leri sudo ister; script "
          "çalıştırmaz. Öneri: sudo apt clean; sudo journalctl --vacuum-time=2weeks.",
          path="/var/cache/apt", kind="advice")
        V("journal", "systemd journal", "Öneri: sudo journalctl --vacuum-size=500M.",
          path="/var/log/journal", kind="advice")
    return rules


# --------------------------------------------------------------------------- ruleset
STAR = "\u2603star\u2603"


class RuleSet(object):
    """Fast lookup: exact paths (dict), wildcard paths (prefix + regex), names (dict)."""

    def __init__(self, env, rules):
        self.env = env
        self.exact = {}
        self.wild = []
        self.names = {}
        self.name_res = []
        self.markers = []
        anchors = env.anchors
        for r in rules:
            if r.path is not None:
                pat = r.path
                missing = False
                for k in re.findall(r"\{(\w+)\}", pat):
                    if k not in anchors:
                        missing = True
                        break
                    pat = pat.replace("{%s}" % k, anchors[k].replace("\\", "/").rstrip("/"))
                if missing:
                    continue
                pat = pat.replace("/", os.sep)
                r.abs = os.path.normpath(pat)
                if "*" in pat:
                    key = norm(pat.replace("*", STAR)).replace(STAR, "*")
                    prefix = key.split("*", 1)[0]
                    segs = [re.escape(s) if s != "*" else r"[^%s]+" % re.escape(os.sep)
                            for s in key.split(os.sep)]
                    rx = re.compile("^" + re.escape(os.sep).join(segs) + "$")
                    self.wild.append((prefix, rx, r))
                else:
                    self.exact.setdefault(norm(pat), []).append(r)
            elif r.name is not None:
                self.names.setdefault(r.name, []).append(r)
            elif r.name_re is not None:
                self.name_res.append(r)
            elif r.marker is not None:
                self.markers.append(r)

    def match(self, node, key):
        """Rules matching node (key = norm(node.path)), explicit path rules first."""
        out = list(self.exact.get(key, ()))
        for prefix, rx, r in self.wild:
            if key.startswith(prefix) and rx.match(key):
                out.append(r)
        lname = node.name.lower()
        is_root_child = node.parent is not None and node.parent.is_root and \
            node.parent.label in ("drive", "mount", "volume")
        for r in self.names.get(lname, ()):
            if r.root_child and not is_root_child:
                continue
            out.append(r)
        for r in self.name_res:
            if r.root_child and not is_root_child:
                continue
            if r.name_re.search(node.name):
                out.append(r)
        if node.flags:
            for r in self.markers:
                if r.marker in node.flags:
                    out.append(r)
        return out
