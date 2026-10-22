# SPDX-License-Identifier: Apache-2.0
"""Build-time staging only; runtime assets keep one canonical source copy."""

import runpy
import shutil
from pathlib import Path

from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist

ROOT = Path(__file__).resolve().parent
ASSETS = ("examples/catalog.json", "docs/evaluation.json")


def seeds():
    try:
        return runpy.run_path(str(ROOT / "ternela/distribution.py"))["seed_files"](ROOT)
    except FileNotFoundError as exc:
        raise RuntimeError("Prepare public build inputs: python -m ternela.setup_demo") from exc


class BuildPy(build_py):
    def run(self):
        super().run()
        target = Path(self.build_lib) / "ternela/_resources"
        # Never retain assets from a previous build after the allowlist shrinks.
        if target.exists():
            shutil.rmtree(target)
        files = {name: (ROOT / name).read_bytes() for name in ASSETS}
        files.update({"data/" + name: raw for name, raw in seeds().items()})
        for name, raw in files.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)


class Sdist(sdist):
    def make_release_tree(self, base_dir, files):
        # Rebuilding a wheel from the sdist must need neither the checkout nor network.
        super().make_release_tree(base_dir, files)
        for name, raw in seeds().items():
            path = Path(base_dir) / "data" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
