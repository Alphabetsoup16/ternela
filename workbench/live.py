# SPDX-License-Identifier: Apache-2.0
"""Bounded public GitHub GETs; no caller-supplied hosts, credentials or mutations."""

import json
import subprocess
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from docs_lab.v2.runtime import execute

from .engine import phase_plan


def collect(plan, context, phase):
    captured, observed, total = {}, {}, 0
    started = time.time()
    budget_started = time.monotonic()
    for request in phase_plan(plan, phase)["requests"]:
        pages = []

        def read(call):
            nonlocal total
            total += 1
            remaining = 60 - (time.monotonic() - budget_started)
            if total > 20 or remaining <= 0:
                raise ValueError("Live read budget exhausted")
            operation = call["operation"]
            if not operation.startswith("GET /repos/"):
                raise ValueError("Live demo supports public repository GETs only")
            binding = call["bindings"]
            path = operation[4:]
            for key, value in binding.get("path", {}).items():
                if not isinstance(value, (str, int)) or isinstance(value, bool):
                    raise ValueError("Invalid path parameter")
                path = path.replace("{" + key + "}", quote(str(value), safe=""))
            if "{" in path or ".." in path.split("/"):
                raise ValueError("Unresolved or invalid path")
            url = call.get("next_url") or "https://api.github.com" + path + (
                "?" + urlencode(binding["query"], doseq=True) if binding.get("query") else ""
            )
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or parsed.netloc != "api.github.com"
                or not parsed.path.startswith("/repos/")
            ):
                raise ValueError("Unapproved observation origin")
            with tempfile.TemporaryDirectory(prefix="observation-http-") as temporary:
                headers = Path(temporary) / "headers"
                body = Path(temporary) / "body"
                process = subprocess.run(
                    [
                        "/usr/bin/curl",
                        "--silent",
                        "--show-error",
                        "--max-time",
                        str(min(15, remaining)),
                        "--max-filesize",
                        "2097152",
                        "--proto",
                        "=https",
                        "-H",
                        "Accept: application/vnd.github+json",
                        "-H",
                        "User-Agent: EvidenceWorkbench/0.1",
                        "-D",
                        str(headers),
                        "-o",
                        str(body),
                        "-w",
                        "%{http_code}",
                        url,
                    ],
                    text=True,
                    capture_output=True,
                    timeout=min(15, remaining) + 5,
                )
                if process.returncode:
                    raise ValueError("Public GitHub request failed; retry the observation")
                parsed_headers = {}
                for line in headers.read_text().splitlines():
                    if line.startswith("HTTP/"):
                        parsed_headers.clear()
                    if ":" in line:
                        key, value = line.split(":", 1)
                        key = key.lower()
                        parsed_headers[key] = (
                            parsed_headers[key] + ", " + value.strip()
                            if key in parsed_headers
                            else value.strip()
                        )
                result = {
                    "http_status": int(process.stdout),
                    "body": json.loads(body.read_text()),
                    "url": url,
                    "headers": parsed_headers,
                    "headers_complete": True,
                }
            return result

        def transport(call):
            try:
                result = read(call)
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                result = {"http_status": 0, "body": None, "error": str(exc)}
            pages.append(deepcopy(result))
            return result

        try:
            observed[request["id"]] = execute(request, context, transport, observed)
        except (ValueError, KeyError, TypeError, IndexError):
            observed[request["id"]] = {"http_status": 0, "body": None, "collection_complete": False}
        captured[request["id"]] = pages
    return captured, started
