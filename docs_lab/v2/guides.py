# SPDX-License-Identifier: Apache-2.0
"""Cache official pagination guides as unedited source text, separate from tool schemas."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).parent
URLS = {
    "github": "https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api",
    "stripe": "https://docs.stripe.com/api/pagination",
    "slack": "https://docs.slack.dev/apis/web-api/pagination/",
}


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.parts = []
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
    data = subprocess.check_output(["/usr/bin/curl", "--fail", "--silent", "--show-error", "--location", "--max-time", "60", url])
    raw = data.decode("utf-8")
    if "<html" in raw[:2000].lower() or "<!doctype" in raw[:200].lower():
        parser = Text(); parser.feed(raw); content = "\n".join(parser.parts)
    else:
        content = raw
    return {"provider": provider, "url": url, "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "raw_sha256": hashlib.sha256(data).hexdigest(), "text_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "text": content}


def main():
    target = ROOT / "guides.json"
    if target.exists():
        raise RuntimeError("Guide snapshot already exists; preserve it for the fresh-task run")
    with ThreadPoolExecutor(max_workers=3) as pool:
        guides = list(pool.map(fetch, URLS.items()))
    target.write_text(json.dumps(guides, indent=2) + "\n")
    print([(g["provider"], len(g["text"])) for g in guides])


if __name__ == "__main__":
    main()
