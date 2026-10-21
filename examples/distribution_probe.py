# SPDX-License-Identifier: Apache-2.0
"""Build synthetic upgrades from the current version and probe real Docker recovery."""

import argparse
import json
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from expansion_lab.storage import PROJECT
from ternela.branding import BRAND
from ternela.distribution import build_bundle, extract_bundle, version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="ternela-local:dev")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="ternela-probe-") as directory:
        root = Path(directory).resolve()
        base = build_bundle(PROJECT, root / "base")
        source = root / "source"
        current = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]["version"]
        extract_bundle(base, source, BRAND["repository"], current)
        shutil.copytree(source / "seed", source / "data")
        output = root / "fixtures"
        major, minor, patch = version(current)
        targets = {
            name: f"{major}.{minor}.{patch + offset}"
            for offset, name in enumerate(("good", "bad", "commit"), 1)
        }
        config, module, entry = (
            source / "pyproject.toml",
            source / "ternela/__init__.py",
            source / "ternela/__main__.py",
        )
        original_config, original_module, original_entry = (
            config.read_text(),
            module.read_text(),
            entry.read_text(),
        )
        for name, target in targets.items():
            config.write_text(
                original_config.replace(f'version = "{current}"', f'version = "{target}"')
            )
            module.write_text(
                original_module.replace(f'__version__ = "{current}"', f'__version__ = "{target}"')
            )
            entry.write_text(
                'from pathlib import Path\nPath("data/probe-sentinel").write_text("candidate corrupted data")\nraise SystemExit(17)\n'
                if name == "bad"
                else original_entry
            )
            build_bundle(source, output)
        (output / "probe.json").write_text(json.dumps(dict(targets, initial=current)))
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--tmpfs",
                "/tmp:size=128m,mode=1777",
                "--no-healthcheck",
                "--entrypoint",
                "python",
                "--mount",
                f"type=bind,source={output},target=/fixtures,readonly",
                "--mount",
                f"type=bind,source={PROJECT / 'scripts/test-container-upgrade.py'},target=/test.py,readonly",
                args.image,
                "/test.py",
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
