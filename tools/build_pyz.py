"""Build dist/disk-cleaner.pyz: a single file that runs anywhere Python 3.8+ is installed.

    python tools/build_pyz.py
    python dist/disk-cleaner.pyz --help
"""

import os
import shutil
import sys
import tempfile
import zipapp

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    dist = os.path.join(ROOT, "dist")
    os.makedirs(dist, exist_ok=True)
    out = os.path.join(dist, "disk-cleaner.pyz")
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copytree(os.path.join(ROOT, "disk_cleaner"), os.path.join(tmp, "disk_cleaner"),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        with open(os.path.join(tmp, "__main__.py"), "w", encoding="utf-8") as fh:
            fh.write("import sys\nfrom disk_cleaner.cli import main\nsys.exit(main())\n")
        zipapp.create_archive(tmp, out, interpreter="/usr/bin/env python3", compressed=True)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
