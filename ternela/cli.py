# SPDX-License-Identifier: Apache-2.0
"""Installed command; choose state before loading application modules."""

import argparse
import os
from pathlib import Path

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="Reliable local waits for agents")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data", type=Path, help="Local data folder (or TERNELA_DATA)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--container", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.data is not None:
        os.environ["TERNELA_DATA"] = str(args.data.expanduser().resolve())
    from workbench.server import main as serve

    serve(["--port", str(args.port), *(["--container"] if args.container else [])])
