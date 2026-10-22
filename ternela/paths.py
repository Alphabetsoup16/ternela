# SPDX-License-Identifier: Apache-2.0
"""One boundary for installed resources and writable local state."""

import os
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
RESOURCES = Path(__file__).with_name("_resources")
CHECKOUT = not RESOURCES.is_dir()


def data_root():
    configured = os.environ.get("TERNELA_DATA")
    if configured:
        return Path(configured).expanduser().resolve()
    if CHECKOUT:
        return PROJECT / "data"
    base = (
        Path.home() / "Library/Application Support"
        if sys.platform == "darwin"
        else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    )
    return base / "ternela/data"


DATA = data_root()


def resource(name):
    """Resolve a reviewed relative resource name; never create or modify it."""
    return (PROJECT if CHECKOUT else RESOURCES) / name


def source(name):
    """Prefer explicit/local evidence; installed wheels supply a read-only seed."""
    local = DATA / name
    return local if local.exists() else resource("data/" + name)
