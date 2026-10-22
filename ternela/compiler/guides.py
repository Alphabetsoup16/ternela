# SPDX-License-Identifier: Apache-2.0
"""Cache official pagination guides as unedited source text, separate from tool schemas."""

import gzip
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser

from ternela.paths import DATA, source
from ternela.storage import atomic_write, canonical

GUIDES = source("sources/guides.json.gz")
URLS = {
    "github": "https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api",
    "stripe": "https://docs.stripe.com/api/pagination",
    "slack": "https://docs.slack.dev/apis/web-api/pagination/",
}


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "header", "footer"):
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "header", "footer"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def fetch(item):
    provider, url = item
    data = subprocess.check_output(
        [
            "/usr/bin/curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--max-time",
            "60",
            "--max-filesize",
            "40000000",
            url,
        ]
    )
    raw = data.decode("utf-8")
    if "<html" in raw[:2000].lower() or "<!doctype" in raw[:200].lower():
        parser = Text()
        parser.feed(raw)
        content = "\n".join(parser.parts)
    else:
        content = raw
    return {
        "provider": provider,
        "url": url,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "raw_sha256": hashlib.sha256(data).hexdigest(),
        "text_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "text": content,
    }


def load():
    guides = json.loads(gzip.decompress(GUIDES.read_bytes()))
    if {guide["provider"] for guide in guides} != set(URLS) or len(guides) != len(URLS):
        raise ValueError("Pagination guides have missing or duplicate providers")
    for guide in guides:
        if (
            guide["url"] != URLS[guide["provider"]]
            or hashlib.sha256(guide["text"].encode()).hexdigest() != guide["text_sha256"]
        ):
            raise ValueError("Pagination guide provenance mismatch")
    return guides


def main():
    if GUIDES.exists():
        load()
        return
    with ThreadPoolExecutor(max_workers=3) as pool:
        guides = list(pool.map(fetch, URLS.items()))
    atomic_write(
        DATA / "sources/guides.json.gz", gzip.compress(canonical(guides), compresslevel=9, mtime=0)
    )
    print([(g["provider"], len(g["text"])) for g in guides])


if __name__ == "__main__":
    main()
