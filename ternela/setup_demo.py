# SPDX-License-Identifier: Apache-2.0
"""Fetch public documentation locally; verify every pinned API specification."""

import gzip
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ternela.paths import DATA
from ternela.paths import source as source_path
from ternela.storage import atomic_write, sha


def download(url):
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("Install curl before preparing the demo")
    return subprocess.check_output(
        [
            curl,
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--max-time",
            "90",
            "--max-filesize",
            "40000000",
            url,
        ]
    )


def source(pin):
    path = source_path("sources/" + pin["provider"] + ".json.gz")
    if path.exists():
        raw = gzip.decompress(path.read_bytes())
        if sha(raw) != pin["sha256"]:
            raise ValueError("Cached source hash mismatch: " + pin["provider"])
        print("Verified", pin["provider"], flush=True)
        return
    raw = download(pin["url"])
    if sha(raw) != pin["sha256"]:
        raise ValueError("Downloaded source hash mismatch: " + pin["provider"])
    atomic_write(DATA / "sources" / path.name, gzip.compress(raw, compresslevel=9, mtime=0))
    print("Prepared", pin["provider"], flush=True)


def main():
    pins = json.loads(source_path("sources/manifest.json").read_text())
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(source, pins))
    from ternela.sources import SourceIndex, ingest

    if source_path("sources/supplements.json").exists():
        SourceIndex("slack")
        print("Verified supplemental evidence", flush=True)
    else:
        url = "https://docs.slack.dev/reference/methods/users.info/"
        ingest(
            "slack",
            "GET /users.info",
            url,
            download(url).decode(),
            datetime.now(timezone.utc).isoformat(),
        )
        print("Prepared separately attributed Slack example evidence", flush=True)
    from ternela.compiler.guides import main as prepare_guides

    prepare_guides()
    print("Verified compiler pagination guides", flush=True)
    print("Ready. Run python3 -m ternela.demo or python3 -m ternela", flush=True)


if __name__ == "__main__":
    main()
