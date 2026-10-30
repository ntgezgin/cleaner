"""Builds a fake home folder that exercises the classification rules."""

import os
import sys

MB = 1024 * 1024
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def write(path, size=0, text=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        if text is not None:
            fh.write(text.encode("utf-8"))
        else:
            chunk = os.urandom(min(size, MB)) if size else b""
            left = size
            while left > 0:
                fh.write(chunk[:left])
                left -= len(chunk)


def cache_dir(home, name):
    """OS-specific location of a per-user cache folder used by the rules."""
    if IS_WIN:
        return {"pip": os.path.join(home, "AppData", "Local", "pip", "Cache"),
                "npm": os.path.join(home, "AppData", "Local", "npm-cache", "_cacache")}[name]
    if IS_MAC:
        return {"pip": os.path.join(home, "Library", "Caches", "pip"),
                "npm": os.path.join(home, ".npm", "_cacache")}[name]
    return {"pip": os.path.join(home, ".cache", "pip"),
            "npm": os.path.join(home, ".npm", "_cacache")}[name]


def build(home):
    p = {}
    j = os.path.join
    # --- projects
    web = j(home, "Documents", "web app 'ç' $x")
    write(j(web, "package.json"), text="{}")
    write(j(web, "src", "index.js"), text="console.log(1)")
    write(j(web, "node_modules", "lodash", "index.js"), 2 * MB)
    os.makedirs(j(web, ".git"), exist_ok=True)
    write(j(web, ".git", "HEAD"), text="ref: refs/heads/main\n")
    p["web"], p["web_nm"] = web, j(web, "node_modules")
    rust = j(home, "projects", "rusty")
    write(j(rust, "Cargo.toml"), text="[package]\nname='x'\n")
    write(j(rust, "target", "debug", "big.bin"), 3 * MB)
    p["rust_target"] = j(rust, "target")
    py = j(home, "projects", "pyproj")
    write(j(py, "pyproject.toml"), text="")
    write(j(py, ".venv", "pyvenv.cfg"), text="home = /usr/bin\n")
    write(j(py, ".venv", "lib", "mod.so"), 2 * MB)
    p["venv"] = j(py, ".venv")
    ml = j(home, "projects", "mlproj")
    write(j(ml, "setup.py"), text="")
    write(j(ml, "build", "weights.pt"), 12 * MB)
    write(j(ml, "build", "lib.so"), 1 * MB)
    p["ml_build"] = j(ml, "build")
    # look-alikes without evidence: must NOT become items
    write(j(home, "projects", "notes", "node_modules", "x.txt"), 2 * MB)
    write(j(home, "projects", "photos", "build", "img.raw"), 2 * MB)
    p["fake_nm"] = j(home, "projects", "notes", "node_modules")
    p["fake_build"] = j(home, "projects", "photos", "build")
    # --- caches
    pip = cache_dir(home, "pip")
    write(j(pip, "http", "a.bin"), 2 * MB)
    p["pip"] = pip
    npm = cache_dir(home, "npm")
    write(j(npm, "content-v2", "b.bin"), 2 * MB)
    p["npm"] = npm
    # hard link: the data must be counted once
    p["hl_src"] = j(home, "projects", "blob.bin")
    write(p["hl_src"], 2 * MB)
    p["hl_dst"] = j(home, "projects", "hardlink.bin")
    try:
        os.link(p["hl_src"], p["hl_dst"])
    except OSError:
        p["hl_dst"] = None
    # --- never
    game = j(home, "SteamLibrary", "steamapps", "common", "SomeGame")
    write(j(game, "data.pak"), 2 * MB)
    write(j(game, "cache", "shader.bin"), 2 * MB)
    p["game"] = game
    write(j(home, "Pictures", "photo.jpg"), 1 * MB)
    p["pictures"] = j(home, "Pictures")
    write(j(home, ".ssh", "id_ed25519"), text="secret")
    p["ssh"] = j(home, ".ssh")
    onedrive = j(home, "OneDrive")
    write(j(onedrive, "proj", "package.json"), text="{}")
    write(j(onedrive, "proj", "node_modules", "big.js"), 2 * MB)
    p["onedrive_nm"] = j(onedrive, "proj", "node_modules")
    protected = j(home, "Datasets")
    write(j(protected, "exp1", "package.json"), text="{}")
    write(j(protected, "exp1", "node_modules", "x.js"), 2 * MB)
    write(j(protected, "exp1", "data.csv"), 1 * MB)
    p["user_never"] = protected
    p["user_never_nm"] = j(protected, "exp1", "node_modules")
    # --- downloads
    write(j(home, "Downloads", "setup-tool.iso"), 51 * MB)
    write(j(home, "Downloads", "holiday.mp4"), 51 * MB)
    p["iso"] = j(home, "Downloads", "setup-tool.iso")
    p["video"] = j(home, "Downloads", "holiday.mp4")
    # --- symlink loop (must not be followed)
    try:
        os.symlink(home, j(home, "projects", "loop"), target_is_directory=True)
        p["loop"] = j(home, "projects", "loop")
    except (OSError, NotImplementedError):
        p["loop"] = None
    return p


def fake_env(home):
    """Environment variables that point the tool at the fake home."""
    env = {"HOME": home, "USERPROFILE": home}
    for k in ("XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "HF_HOME", "OLLAMA_MODELS",
              "OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        env[k] = None
    if IS_WIN:
        env["LOCALAPPDATA"] = os.path.join(home, "AppData", "Local")
        env["APPDATA"] = os.path.join(home, "AppData", "Roaming")
        env["TEMP"] = os.path.join(home, "AppData", "Local", "Temp")
    return env
