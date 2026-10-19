# SPDX-License-Identifier: Apache-2.0
"""Run the complete offline-after-setup test suite with one command."""

import subprocess
import sys

from expansion_lab.storage import PROJECT


def main():
    for directory in ["tests", "safety_lab/tests", "workbench/tests"]:
        result = subprocess.run(
            [
                sys.executable,
                "-W",
                "error::ResourceWarning",
                "-m",
                "unittest",
                "discover",
                "-s",
                directory,
                "-v",
            ],
            cwd=PROJECT,
        )
        if result.returncode:
            raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
