# SPDX-License-Identifier: Apache-2.0
"""Loopback-only product workbench. python3 -m workbench.server"""

import argparse
import gzip
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ternela import __version__
from ternela.acquisition import ReadBatch
from ternela.acquisition import acquire as collect
from ternela.branding import BRAND, NAME
from ternela.connectors import (
    connector_descriptors,
    live_mode,
    recipe_plan,
    recipe_providers,
    release_plan,
    release_providers,
    supports,
)
from ternela.contracts import Acquisition
from ternela.migrations import SCHEMA_VERSION
from ternela.monitor import Monitors
from ternela.storage import PROJECT, Store, sha, strict_json, write_json
from ternela.watches import Watches

from . import demo


class Service:
    def __init__(self):
        self.store = Store()
        self.engine = Watches(PROJECT / "data/workbench/watches.sqlite3", self.store)
        self.monitors = Monitors(self.engine)
        self.token = secrets.token_urlsafe(32)
        self.jobs = {}
        self.pool = ThreadPoolExecutor(max_workers=1)

    def watch(self, identity):
        watch = self.engine.get(identity)
        watch["monitor"] = self.monitors.get(identity)
        return watch

    def recipe(self, provider, context, ttl=900):
        task, plan, phase = recipe_plan(provider, context)
        task.update(_origin_run="reviewed-recipe", _mode="live public GET")
        return self.engine.create(task, plan, phase, ttl)

    def job(self, identity):
        if not isinstance(identity, str):
            raise ValueError("Job ID must be a string")
        if identity in self.jobs:
            return self.jobs[identity]
        if (
            len(identity) != 37
            or not identity.startswith("task-")
            or any(char not in "0123456789abcdef" for char in identity[5:])
        ):
            raise KeyError("Job not found")
        task_path = PROJECT / "data/tasks" / identity / "public.json"
        if not task_path.exists():
            raise KeyError("Job not found")
        task = json.loads(task_path.read_text())["tasks"][0]
        manifest_path = PROJECT / "data/runs" / identity / "manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if (
                manifest.get("status") == "completed"
                and manifest.get("admission_status") == "accepted"
            ):
                plan = self.store.get(manifest["plans"])["plans"][0]
                return dict(id=identity, status="completed", task=task, plan=plan, run=identity)
            if manifest.get("status") == "failed" or manifest.get("admission_status") == "rejected":
                return dict(
                    id=identity,
                    status="failed",
                    task=task,
                    error="Compilation failed; inspect its saved run before retrying.",
                )
        return dict(
            id=identity,
            status="interrupted",
            task=task,
            error="The service restarted before this job completed. Start a new compilation.",
        )

    def compile(self, task):
        if task.get("provider") not in ["github", "stripe", "slack"]:
            raise ValueError("Choose a supported provider")
        if not isinstance(task.get("task"), str) or not 5 <= len(task["task"]) <= 8000:
            raise ValueError("Task must be 5–8000 characters")
        if not isinstance(task.get("context"), dict):
            raise ValueError("Context must be a JSON object")
        identity = "task-" + uuid.uuid4().hex
        task = dict(task, id=identity)
        self.jobs[identity] = {"id": identity, "status": "queued", "task": task}
        directory = PROJECT / "data/tasks" / identity
        write_json(directory / "public.json", {"tasks": [task]})
        write_json(directory / "private.json", {"probes": {}})
        write_json(
            directory / "manifest.json",
            {
                name: sha((directory / name).read_bytes())
                for name in ["public.json", "private.json"]
            },
        )

        def work():
            job = self.jobs[identity]
            job["status"] = "compiling"
            try:
                process = subprocess.run(
                    [sys.executable, "-m", "workbench.compiler", identity],
                    cwd=PROJECT,
                    capture_output=True,
                    text=True,
                    timeout=1200,
                )
                job["log"] = self.store.put(process.stdout + process.stderr, "text/plain")
                if process.returncode:
                    raise ValueError(
                        "Compilation did not finish. Inspect the saved run before retrying."
                    )
                manifest = json.loads(
                    (PROJECT / "data/runs" / identity / "manifest.json").read_text()
                )
                plan = self.store.get(manifest["plans"])["plans"][0]
                from ternela.validation import admit

                admit(plan, task)
                job.update(status="completed", plan=plan, run=identity)
            except Exception as exc:
                job.update(status="failed", error=str(exc))

        self.pool.submit(work)
        return self.jobs[identity]


class Handler(BaseHTTPRequestHandler):
    server_version = "EvidenceWorkbench/0.1"

    def log_message(self, format, *args):
        pass

    def encode_body(self, body):
        accepted = {}
        for item in self.headers.get("Accept-Encoding", "").lower().split(","):
            parts = item.strip().split(";")
            quality = 1.0
            try:
                for option in parts[1:]:
                    key, _, value = option.strip().partition("=")
                    if key == "q":
                        quality = float(value)
                accepted[parts[0]] = quality if 0 <= quality <= 1 else 0
            except ValueError:
                accepted[parts[0]] = 0
        self.send_header("Vary", "Accept-Encoding")
        if len(body) >= 1024 and accepted.get("gzip", accepted.get("*", 0)) > 0:
            packed = gzip.compress(body, compresslevel=3, mtime=0)
            if len(packed) < len(body):
                self.send_header("Content-Encoding", "gzip")
                return packed
        return body

    def respond(self, value, status=200):
        body = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        body = self.encode_body(body)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if not self.valid_host():
                return self.respond({"error": "Invalid host"}, 403)
            path = urlsplit(self.path).path
            if path == "/api/health":
                return self.respond(
                    {
                        "status": "ok",
                        "version": __version__,
                        "mode": "single-host loopback service",
                        "python": sys.version.split()[0],
                        "formats": {"database": SCHEMA_VERSION, "plan": 1, "receipt": 1},
                        "capabilities": {
                            "public_github_monitoring": True,
                            "public_package_monitoring": release_providers(),
                            "public_recipes": recipe_providers(),
                            "authenticated_connectors": False,
                            "verified_webhooks": False,
                            "task_compiler_available": shutil.which("codex") is not None,
                        },
                        "sources_present": all(
                            (PROJECT / "data/sources" / (provider + ".json.gz")).is_file()
                            for provider in ("github", "stripe", "slack")
                        ),
                    }
                )
            if path in ["/", "/app.js", "/style.css"]:
                file = (
                    Path(__file__).parent
                    / "static"
                    / ({"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path])
                )
                import html

                body = file.read_bytes()
                if path == "/":
                    body = body.replace(b"__PRODUCT_NAME__", html.escape(NAME).encode())
                self.send_response(200)
                body = self.encode_body(body)
                self.send_header(
                    "Content-Type",
                    {
                        "/": "text/html; charset=utf-8",
                        "/app.js": "text/javascript; charset=utf-8",
                        "/style.css": "text/css; charset=utf-8",
                    }[path],
                )
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
                )
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/bootstrap":
                recorded = json.loads((PROJECT / "docs/evaluation.json").read_text())
                evaluation = {
                    key: recorded[key] for key in ("run_id", "corrected", "structural_issues")
                }
                supplements = PROJECT / "data/sources/supplements.json"
                return self.respond(
                    {
                        "brand": BRAND,
                        "token": self.server.service.token,
                        "templates": demo.catalog(),
                        "connectors": connector_descriptors(),
                        "evaluation": evaluation,
                        "sources": (
                            json.loads(supplements.read_text()) if supplements.exists() else []
                        ),
                        "mode": "local prototype; simulated replay unless live public GET is selected",
                    }
                )
            if path == "/api/connectors":
                return self.respond(connector_descriptors())
            if path == "/api/watches":
                query = parse_qs(urlsplit(self.path).query)
                return self.respond(
                    self.server.service.engine.directory(
                        query.get("after", [""])[0], int(query.get("limit", [30])[0])
                    )
                )
            if path == "/api/usage":
                return self.respond(self.server.service.engine.usage())
            if path.startswith("/api/watches/"):
                parts = path.split("/")
                identity = parts[3]
                if len(parts) == 4:
                    watch = self.server.service.watch(identity)
                    if parse_qs(urlsplit(self.path).query).get("receipt_text") == ["1"]:
                        watch["receipt_text"] = (
                            json.dumps(watch["receipt"], indent=2, allow_nan=False)
                            if watch["receipt"]
                            else None
                        )
                    return self.respond(watch)
                if len(parts) == 5 and parts[4] == "status":
                    watch = self.server.service.engine.status(identity)
                    watch["monitor"] = self.server.service.monitors.get(identity)
                    return self.respond(watch)
                if len(parts) == 5 and parts[4] == "usage":
                    return self.respond(self.server.service.engine.usage(identity))
                if len(parts) == 5 and parts[4] == "deliveries":
                    query = parse_qs(urlsplit(self.path).query)
                    return self.respond(
                        self.server.service.engine.deliveries(
                            identity, query.get("consumer", [""])[0]
                        )
                    )
                if len(parts) == 5 and parts[4] == "receipt":
                    receipt = self.server.service.engine.get(identity)["receipt"]
                    if receipt is None:
                        raise KeyError("No receipt has been recorded")
                    return self.respond(receipt)
                if len(parts) == 5 and parts[4] == "outbox":
                    query = parse_qs(urlsplit(self.path).query)
                    return self.respond(
                        self.server.service.engine.outbox(
                            identity,
                            int(query.get("after", [0])[0]),
                            query.get("recent") == ["1"],
                            compact=query.get("compact") == ["1"],
                        )
                    )
            if path.startswith("/api/jobs/"):
                parts = path.split("/")
                if len(parts) != 4:
                    raise KeyError("Job not found")
                return self.respond(self.server.service.job(parts[3]))
            self.respond({"error": "Not found"}, 404)
        except KeyError as exc:
            self.respond({"error": str(exc)}, 404)
        except (ValueError, TypeError) as exc:
            self.respond(
                {"error": str(exc), **({"code": exc.code} if hasattr(exc, "code") else {})}, 400
            )
        except Exception:
            self.respond({"error": "Local service error; inspect terminal output"}, 500)

    def valid_host(self):
        port = getattr(self.server, "public_port", self.server.server_port)
        return self.headers.get("Host") in [
            f"127.0.0.1:{port}",
            f"localhost:{port}",
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        ]

    def do_POST(self):
        try:
            service = self.server.service
            if not self.valid_host() or self.headers.get("X-Observation-Token") != service.token:
                return self.respond({"error": "Invalid local session"}, 403)
            origin = self.headers.get("Origin")
            if origin and origin not in [
                f"http://127.0.0.1:{getattr(self.server, 'public_port', self.server.server_port)}",
                f"http://localhost:{getattr(self.server, 'public_port', self.server.server_port)}",
            ]:
                return self.respond({"error": "Cross-origin request denied"}, 403)
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536:
                return self.respond({"error": "JSON request must be at most 64 KiB"}, 413)
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked requests are not supported")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Expected application/json")
            body = strict_json(self.rfile.read(length))
            path = urlsplit(self.path).path
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            if path == "/api/compile":
                return self.respond(service.compile(body), 202)
            if path == "/api/watches":
                if "recipe" in body:
                    return self.respond(
                        service.recipe(
                            body["recipe"], body.get("context", {}), body.get("ttl", 900)
                        ),
                        201,
                    )
                if "package" in body:
                    task, plan = release_plan(body["provider"], body["package"], body["version"])
                    task.update(_origin_run="reviewed-recipe", _mode="live public GET")
                    return self.respond(
                        service.engine.create(task, plan, "after", body.get("ttl", 900)), 201
                    )
                if "template_id" in body:
                    template = next(
                        (t for t in demo.catalog() if t["task"]["id"] == body["template_id"]), None
                    )
                    if template is None:
                        raise ValueError("Unknown compiled task")
                    task = dict(
                        template["task"], _origin_run=template["run"], _mode=template["mode"]
                    )
                    return self.respond(
                        service.engine.create(
                            task, template["plan"], template["phase"], body.get("ttl", 30)
                        ),
                        201,
                    )
                job = service.job(body.get("job_id", ""))
                if job["status"] != "completed":
                    raise ValueError("Compile and validate a plan first")
                task = dict(job["task"], _origin_run=job["run"], _mode="caller-supplied snapshot")
                return self.respond(
                    service.engine.create(
                        task, job["plan"], body.get("phase", "after"), body.get("ttl", 30)
                    ),
                    201,
                )
            if path.startswith("/api/watches/"):
                parts = path.split("/")
                if len(parts) != 5:
                    return self.respond({"error": "Not found"}, 404)
                identity = parts[3]
                action = parts[4]
                watch = service.engine.get(identity)
                if action == "replay":
                    snapshots = demo.snapshots(
                        watch["task"], watch["plan"], body.get("case", "success"), watch["phase"]
                    )
                    result = service.engine.submit(
                        identity,
                        uuid.uuid4().hex,
                        watch["revision"] + 1,
                        snapshots,
                        time.time() - watch["ttl"] - 1 if body.get("stale") else None,
                        "simulated replay",
                    )
                    return self.respond(result)
                if action == "events":
                    return self.respond(
                        service.engine.submit(
                            identity,
                            body["event_id"],
                            body["revision"],
                            body["snapshots"],
                            body.get("observed_at"),
                        )
                    )
                if action == "live":
                    provider = watch["task"]["provider"]
                    if not supports(provider, watch["active_plan"]["requests"]):
                        raise ValueError("No reviewed anonymous connector for these operations")
                    batch = ReadBatch()
                    acquired = collect(
                        watch["plan"],
                        watch["task"]["context"],
                        watch["phase"],
                        provider=provider,
                        batch=batch,
                    )
                    if isinstance(acquired, Acquisition):
                        snapshots, started = acquired, acquired.observed_at
                    else:
                        snapshots, started = acquired
                    service.engine.record_acquisition(
                        identity, batch.network_reads, batch.shared_reads
                    )
                    return self.respond(
                        service.engine.submit(
                            identity,
                            uuid.uuid4().hex,
                            watch["revision"] + 1,
                            snapshots,
                            started,
                            live_mode(provider),
                        )
                    )
                if action == "monitor":
                    return self.respond(
                        service.monitors.start(
                            identity, body.get("interval", 300), body.get("duration", 86400)
                        )
                    )
                if action == "pause":
                    return self.respond(service.monitors.pause(identity))
                if action == "delivery-ack":
                    return self.respond(
                        service.engine.acknowledge_delivery(identity, body["consumer"], body["id"])
                    )
                if action == "ack":
                    service.engine.ack(identity, body["id"])
                    return self.respond({"acknowledged": body["id"]})
            return self.respond({"error": "Not found"}, 404)
        except KeyError as exc:
            self.respond({"error": str(exc)}, 404)
        except (ValueError, TypeError, IndexError, RecursionError) as exc:
            self.respond(
                {"error": str(exc), **({"code": exc.code} if hasattr(exc, "code") else {})}, 400
            )
        except Exception as exc:
            print("Service error:", type(exc).__name__, str(exc), flush=True)
            self.respond({"error": "Local service error; inspect terminal output"}, 500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--container",
        action="store_true",
        help="Bind the container interface; publish only to host loopback",
    )
    args = parser.parse_args()
    if args.container and os.environ.get("TERNELA_CONTAINER") != "1":
        parser.error("--container is reserved for the supplied local container image")
    server = ThreadingHTTPServer(("0.0.0.0" if args.container else "127.0.0.1", args.port), Handler)
    server.public_port = (
        int(os.environ.get("TERNELA_PUBLIC_PORT", args.port)) if args.container else args.port
    )
    server.service = Service()
    stop = threading.Event()

    def sweeper():
        while not stop.wait(1):
            try:
                server.service.engine.expire()
            except Exception as exc:
                print("Expiry sweep failed:", type(exc).__name__, flush=True)

    threading.Thread(target=sweeper, daemon=True).start()

    def monitor():
        while not stop.is_set():
            try:
                if server.service.monitors.run_batch()["watches"]:
                    continue
            except Exception as exc:
                print("Monitor attempt failed:", type(exc).__name__, flush=True)
            stop.wait(1)

    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()
    print(f"{NAME}: http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        worker.join(timeout=1)
        server.service.pool.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    main()
