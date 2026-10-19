# SPDX-License-Identifier: Apache-2.0
"""Fetch public documentation locally; verify every pinned API specification."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import shutil
import subprocess

from expansion_lab.storage import PROJECT, sha, write_json


def download(url):
    curl=shutil.which("curl")
    if not curl: raise RuntimeError("Install curl before preparing the demo")
    return subprocess.check_output([curl,"--fail","--silent","--show-error","--location","--proto","=https","--max-time","90","--max-filesize","40000000",url])


def source(pin):
    path=PROJECT/"data/sources"/(pin["provider"]+".json.gz")
    if path.exists():
        raw=gzip.decompress(path.read_bytes())
        if sha(raw)!=pin["sha256"]: raise ValueError("Cached source hash mismatch: "+pin["provider"])
        print("Verified",pin["provider"],flush=True); return
    raw=download(pin["url"])
    if sha(raw)!=pin["sha256"]: raise ValueError("Downloaded source hash mismatch: "+pin["provider"])
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(".tmp"); temporary.write_bytes(gzip.compress(raw,compresslevel=9,mtime=0));temporary.replace(path)
    print("Prepared",pin["provider"],flush=True)


def main():
    pins=json.loads((PROJECT/"data/sources/manifest.json").read_text())
    with ThreadPoolExecutor(max_workers=3) as pool: list(pool.map(source,pins))
    from source_lab.evidence import ingest, MANIFEST, SourceIndex
    if MANIFEST.exists():
        SourceIndex("slack")
        print("Verified supplemental evidence",flush=True)
    else:
        url="https://docs.slack.dev/reference/methods/users.info/"
        ingest("slack","GET /users.info",url,download(url).decode(),datetime.now(timezone.utc).isoformat())
        print("Prepared separately attributed Slack example evidence",flush=True)
    guide_path=PROJECT/"docs_lab/v2/guides.json"
    if not guide_path.exists():
        from docs_lab.v2.guides import fetch,URLS
        with ThreadPoolExecutor(max_workers=3) as pool: guides=list(pool.map(fetch,URLS.items()))
        write_json(guide_path,guides)
        print("Prepared compiler pagination guides",flush=True)
    print("Ready. Run python3 -m thenwake.demo or python3 -m thenwake",flush=True)


if __name__=="__main__": main()
