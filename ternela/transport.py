# SPDX-License-Identifier: Apache-2.0
"""Bounded anonymous JSON reads for explicitly allowed public API origins."""

import gzip
import io
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from ternela.storage import strict_json

MAX_RESPONSE = 2 * 1024 * 1024


def decode_body(raw, encoding, limit=MAX_RESPONSE):
    if encoding.lower() == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            decoded = stream.read(limit + 1)
    elif encoding.lower() in ("", "identity"):
        decoded = raw
    else:
        raise ValueError("Unsupported response content encoding")
    if len(decoded) > limit:
        raise ValueError("Decoded response exceeds its size limit")
    return decoded


def read_json(url, host, timeout=15):
    origin = urlsplit(url)
    if (
        origin.scheme != "https"
        or origin.netloc != host
        or origin.username
        or origin.password
        or origin.fragment
    ):
        raise ValueError("Unapproved public API origin")
    with tempfile.TemporaryDirectory(prefix="ternela-http-") as directory:
        headers_path, body_path = Path(directory) / "headers", Path(directory) / "body"
        process = subprocess.run(
            [
                "/usr/bin/curl",
                "--silent",
                "--show-error",
                "--proto",
                "=https",
                "--max-time",
                str(timeout),
                "--max-filesize",
                str(MAX_RESPONSE),
                "-H",
                "Accept: application/json",
                "-H",
                "Accept-Encoding: gzip",
                "-H",
                "User-Agent: Ternela-public-observer/0.1",
                "-D",
                str(headers_path),
                "-o",
                str(body_path),
                "-w",
                "%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        if process.returncode:
            raise ValueError("Public API acquisition failed")
        headers = {}
        for line in headers_path.read_text().splitlines():
            if line.startswith("HTTP/"):
                headers.clear()
            elif ":" in line:
                key, value = line.split(":", 1)
                key = key.lower()
                headers[key] = (
                    headers[key] + ", " + value.strip() if key in headers else value.strip()
                )
        status = int(process.stdout)
        raw = body_path.read_bytes()
        try:
            body = strict_json(decode_body(raw, headers.get("content-encoding", "")))
        except (ValueError, OSError, EOFError):
            if 200 <= status < 300:
                raise ValueError("Public API returned invalid or oversized JSON") from None
            body = None
        return {
            "http_status": status,
            "body": body,
            "url": url,
            "headers": headers,
            "headers_complete": True,
            "wire_bytes": len(raw),
        }
