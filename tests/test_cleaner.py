import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fixture  # noqa: E402
from disk_cleaner import cli, platform_info, report, rules  # noqa: E402
from disk_cleaner.platform_info import norm  # noqa: E402

SCRIPTGEN = os.path.join(os.path.dirname(HERE), "disk_cleaner", "ui", "scriptgen.js")
NODE = shutil.which("node")
IS_WIN = sys.platform == "win32"


class EnvPatch(object):
    def __init__(self, values):
        self.values, self.saved = values, {}

    def __enter__(self):
        for k, v in self.values.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def snapshot(root):
    out = {}
    for dp, dns, fns in os.walk(root):
        for f in fns:
            fp = os.path.join(dp, f)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            out[fp] = (st.st_size, st.st_mtime)
    return out


def run_analysis(home, never):
    with EnvPatch(fixture.fake_env(home)):
        env = platform_info.Environment()
        cfg = {"never_paths": [never]}
        data, items = cli.analyze(env, cfg, [(home, "home")], no_docker=True,
                                  no_git=shutil.which("git") is None)
    return env, data


def make_fixture():
    tmp = tempfile.mkdtemp(prefix="dc-test-")
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    paths = fixture.build(home)
    return tmp, home, paths


def gen_script(data, extra, out_path):
    data_path = out_path + ".json"
    with open(data_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    js = ("const g=require(process.argv[1]);const fs=require('fs');"
          "const d=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));"
          "const sel=d.items.filter(g.actionable).concat(JSON.parse(process.argv[3]));"
          "const r=g.generate(d,sel);fs.writeFileSync(process.argv[4],(r.shell==='powershell'?'\\ufeff':'')+r.content,'utf8');")
    subprocess.check_call([NODE, "-e", js, SCRIPTGEN, data_path, json.dumps(extra), out_path])


def run_script(path, home, apply=False):
    env = dict(os.environ, HOME=home, USERPROFILE=home, DISK_CLEANER_ALLOW_ROOT="1")
    if IS_WIN:
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
        if apply:
            cmd += ["-Apply", "-Yes"]
    else:
        cmd = ["bash", path] + (["--apply", "--yes"] if apply else [])
    p = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=600)
    return p.returncode, p.stdout.decode("utf-8", "replace")


def forged_item(path):
    """An item that tries to trash a protected folder: the script must refuse it."""
    return {"id": 9999, "rule": "forged", "title": "forged", "path": path, "cat": "safe",
            "kind": "trash", "size": 1, "apparent": 1, "files": 1, "mtime": None,
            "atime": None, "age_days": None, "stale": False, "desc": "", "evidence": [],
            "warnings": [], "selectable": True, "default": True, "reclaim": "full",
            "disk": -1, "admin": False, "procs": [], "older": None, "cmd": None,
            "tool": None, "fallback": None, "is_file": False, "note": None}


class AnalysisTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.home, cls.p = make_fixture()
        cls.before = snapshot(cls.home)
        cls.env, cls.data = run_analysis(cls.home, cls.p["user_never"])
        cls.after = snapshot(cls.home)
        cls.items = {norm(i["path"]): i for i in cls.data["items"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def item(self, path):
        return self.items.get(norm(path))

    def assertNoItemUnder(self, root):
        k = norm(root)
        bad = [i["path"] for i in self.data["items"]
               if platform_info.is_under(norm(i["path"]), k) and i["selectable"]]
        self.assertEqual(bad, [], "selectable items under protected %s" % root)

    def test_read_only(self):
        self.assertEqual(self.before, self.after)

    def test_dev_artifacts_are_review(self):
        for key, rule in (("web_nm", "node_modules"), ("rust_target", "cargo-target"),
                          ("venv", "venv")):
            it = self.item(self.p[key])
            self.assertIsNotNone(it, key)
            self.assertEqual(it["cat"], "review", key)
            self.assertEqual(it["rule"], rule)
            self.assertFalse(it["default"], key)
            self.assertTrue(it["selectable"], key)
        self.assertEqual(self.item(self.p["rust_target"])["kind"], "command")
        self.assertIn("cargo clean", self.item(self.p["rust_target"])["cmd"])

    def test_valuable_files_warn(self):
        it = self.item(self.p["ml_build"])
        self.assertIsNotNone(it)
        self.assertEqual(it["cat"], "review")
        self.assertTrue(any("veri/medya/model" in w for w in it["warnings"]))

    def test_lookalikes_without_evidence_ignored(self):
        self.assertIsNone(self.item(self.p["fake_nm"]))
        self.assertIsNone(self.item(self.p["fake_build"]))

    def test_caches_are_safe_and_selected(self):
        for key in ("pip", "npm"):
            it = self.item(self.p[key])
            self.assertIsNotNone(it, key)
            self.assertEqual(it["cat"], "safe")
            self.assertTrue(it["default"])
            self.assertEqual(it["kind"], "command")

    def test_never_areas(self):
        for key in ("game", "pictures", "ssh", "onedrive_nm", "user_never"):
            self.assertNoItemUnder(self.p[key])
        self.assertIsNone(self.item(self.p["user_never_nm"]))
        self.assertIsNone(self.item(self.p["onedrive_nm"]))
        cloud = [c["path"] for c in self.data["env"]["cloud_roots"]]
        self.assertTrue(any(norm(c) == norm(os.path.join(self.home, "OneDrive")) for c in cloud))
        self.assertIn(norm(os.path.join(self.home, "OneDrive")),
                      [norm(g) for g in self.data["guard"]["hard"]])

    def test_downloads_installer(self):
        it = self.item(self.p["iso"])
        self.assertIsNotNone(it)
        self.assertEqual(it["cat"], "review")
        self.assertTrue(it["is_file"])
        self.assertIsNone(self.item(self.p["video"]))
        cats = {norm(f["path"]): f["cat"] for f in self.data["top_files"]}
        self.assertEqual(cats[norm(self.p["video"])], "n")  # personal media extension

    def test_hardlink_and_symlink(self):
        if self.p["hl_dst"]:
            self.assertIn("(1 tekrar", self.data["notes"][1])
        if self.p["loop"]:
            self.assertGreaterEqual(self.data["links_skipped"], 1)

    def test_tree_categories(self):
        def find(node, name):
            if node.get("n") == name:
                return node
            for c in node.get("k", []):
                r = find(c, name)
                if r:
                    return r
        self.assertEqual(find(self.data["tree"], "SteamLibrary")["c"], "n")
        self.assertEqual(find(self.data["tree"], "Pictures")["c"], "n")

    def test_html_is_offline_and_parses(self):
        html = report.render_html(self.data)
        self.assertNotIn("https://", html)
        self.assertNotIn("http://", html)
        self.assertNotRegex(html, r"<script[^>]+src=")
        start = html.index('id="report-data">') + len('id="report-data">')
        payload = html[start:html.index("</script>", start)]
        self.assertEqual(json.loads(payload)["meta"]["files"], self.data["meta"]["files"])


@unittest.skipUnless(NODE, "node is required to run the script generator")
class ScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp, self.home, self.p = make_fixture()
        self.env, self.data = run_analysis(self.home, self.p["user_never"])
        self.script = os.path.join(self.tmp, "cleanup." + ("ps1" if IS_WIN else "sh"))
        extra = [forged_item(self.p["pictures"]), forged_item(self.p["ssh"]),
                 forged_item(self.home)]
        gen_script(self.data, extra, self.script)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dry_run_changes_nothing(self):
        before = snapshot(self.home)
        rc, out = run_script(self.script, self.home)
        self.assertEqual(rc, 0, out)
        self.assertIn("DRY-RUN", out)
        self.assertIn("KORUMALI", out)
        self.assertIn(self.p["web_nm"], out)
        self.assertEqual(before, snapshot(self.home))

    @unittest.skipIf(sys.platform == "darwin", "Finder automation is not available in CI")
    def test_apply_moves_to_trash_and_respects_guards(self):
        if not IS_WIN and not (shutil.which("gio") or shutil.which("trash-put")):
            self.skipTest("no trash tool")
        # only trash-type items: official commands (npm/pip) would touch the real tools
        sel = [i for i in self.data["items"] if i["kind"] == "trash" and i["selectable"]]
        self.assertTrue(sel)
        data = dict(self.data, items=sel)
        gen_script(data, [forged_item(self.p["pictures"])], self.script)
        rc, out = run_script(self.script, self.home, apply=True)
        self.assertEqual(rc, 0, out)
        for it in sel:
            if it["rule"] != "download-installer":
                self.assertFalse(os.path.exists(it["path"]), out)
        self.assertTrue(os.path.isdir(self.p["pictures"]))
        self.assertTrue(os.path.isdir(self.p["ssh"]))
        self.assertTrue(os.path.isdir(self.p["game"]))


class QuotingTest(unittest.TestCase):
    def test_python_quoting(self):
        self.assertEqual(rules.q_sh("a'b"), "'a'\\''b'")
        self.assertEqual(rules.q_ps("a'b’c"), "'a''b’’c'")

    @unittest.skipUnless(NODE, "node required")
    def test_js_quoting_matches(self):
        s = "x 'y' ’z $HOME `c` ç"
        js = ("const g=require(process.argv[1]);"
              "process.stdout.write(JSON.stringify([g.qsh(process.argv[2]),g.qps(process.argv[2])]))")
        out = subprocess.check_output([NODE, "-e", js, SCRIPTGEN, s]).decode("utf-8")
        self.assertEqual(json.loads(out), [rules.q_sh(s), rules.q_ps(s)])


if __name__ == "__main__":
    unittest.main()
