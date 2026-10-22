# SPDX-License-Identifier: Apache-2.0
"""Install a built wheel offline; test outside the checkout with read-only code."""

import argparse
import gzip
import hashlib
import json
import os
import signal
import socket
import subprocess
import tempfile
import time
import venv
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


def check(wheel):
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        if any(
            part in {"tests", "fresh", "checks", "runs", "audits", ".env"}
            or part.startswith(".env.")
            for name in names
            for part in Path(name).parts
        ):
            raise AssertionError("Private/development data in wheel")
        metadata = next(name for name in names if name.endswith(".dist-info/METADATA"))
        if "License-Expression: Apache-2.0" not in archive.read(metadata).decode():
            raise AssertionError("Missing Apache license metadata")
    with tempfile.TemporaryDirectory(prefix="ternela-wheel-") as temporary:
        root = Path(temporary)
        venv.create(root / "env", with_pip=True)
        python = root / "env/bin/python"
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(wheel)],
            cwd=root,
            check=True,
            capture_output=True,
        )
        # Strip source-path injection and keep all test state under one temporary folder.
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME"}}
        env.update(TERNELA_DATA=str(root / "state"), PYTHONDONTWRITEBYTECODE="1")
        site = Path(
            subprocess.check_output(
                [str(python), "-I", "-c", "import ternela; print(ternela.__file__)"],
                cwd=root,
                env=env,
                text=True,
            ).strip()
        ).parent.parent
        # Make only this test's installed code read-only, not the interpreter/tools.
        protected = []
        for name in (
            "ternela",
            "workbench",
            "examples",
            "docs_lab",
            "expansion_lab",
            "source_lab",
            "safety_lab",
            "observation_lab",
        ):
            for path in (site / name).rglob("*"):
                if path.is_file() and path.suffix != ".pyc":
                    protected.append(path)
                    path.chmod(0o444)
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
        for module in ("ternela.setup_demo", "ternela.demo"):
            result = subprocess.run(
                [str(python), "-I", "-B", "-m", module],
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=45,
            )
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            if module == "ternela.demo" and "3 durable decision changes" not in result.stdout:
                raise AssertionError("Installed deterministic replay did not complete")
        # Validate compiler resources/imports without model inference or credentials.
        subprocess.run(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                "from ternela.compiler.feedback import HardenedEvidence; "
                "from ternela.compiler.run import code_manifest; "
                "from ternela.compiler.guides import load; import sys; assert code_manifest() and load(); assert not [n for n in sys.modules if n.split('.')[0].endswith('_lab') or n.split('.')[0] == 'workbench']",
            ],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            timeout=20,
        )
        compiler = subprocess.run(
            [str(python), "-I", "-B", str(Path(__file__).with_name("compiler_support.py"))],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        )
        compiler_report = json.loads(compiler.stdout)
        assert compiler_report["compiler_pipeline"] == "passed"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        command = root / "env/bin/ternela"
        version = subprocess.check_output(
            [str(command), "--version"], cwd=root, env=env, text=True
        ).strip()
        # --data must override the environment before any application import.
        state = root / "cli-state"
        log = root / "service.log"
        opener = build_opener(ProxyHandler({}))
        base = f"http://127.0.0.1:{port}"

        def get(path, body=None, token=None):
            headers = {"Content-Type": "application/json"}
            if token:
                headers["X-Observation-Token"] = token
                headers["Origin"] = base
            request = Request(
                base + path,
                data=json.dumps(body).encode() if body is not None else None,
                headers=headers,
            )
            with opener.open(request, timeout=5) as response:
                return response.read()

        with log.open("wb") as stream:
            process = subprocess.Popen(
                [str(command), "--port", str(port), "--data", str(state)],
                cwd=root,
                env=env,
                stdout=stream,
                stderr=stream,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        health = json.loads(get("/api/health"))
                        break
                    except (URLError, OSError):
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise AssertionError(log.read_text())
                        time.sleep(0.1)
                assert health["sources_present"] and health["version"] == version
                bootstrap_raw = get("/api/bootstrap")
                bootstrap = json.loads(bootstrap_raw)
                session_raw = get("/api/bootstrap?session=1")
                assert json.loads(session_raw) == {"token": bootstrap["token"]}
                assert bootstrap["templates"] and bootstrap["connectors"]
                for asset in ("/", "/app.js", "/style.css"):
                    assert len(get(asset)) > 100
                subprocess.run(
                    [
                        str(python),
                        "-I",
                        "-B",
                        "-c",
                        "from ternela import Client; "
                        f"c=Client({base!r}); w=c.release('pypi', 'httpx', '0.28.1'); "
                        "assert c.get(w['id'])['id'] == w['id']",
                    ],
                    cwd=root,
                    env=env,
                    check=True,
                    capture_output=True,
                    timeout=20,
                )
                get("/api/agent-demo", {}, bootstrap["token"])
                deadline = time.monotonic() + 40
                while time.monotonic() < deadline:
                    demo = json.loads(get("/api/agent-demo"))
                    if demo["status"] in {"completed", "failed"}:
                        break
                    time.sleep(0.2)
                assert demo["status"] == "completed", demo
                assert demo["answer"]["total_cents"] == 6800, demo
                assert (state / "workbench/watches.sqlite3").exists()
                assert not (root / "state/workbench/watches.sqlite3").exists()
            finally:
                # Only our process group; never stop a shared server or browser.
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=5)
        assert before == {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
        assert not (site / "data").exists()
    return {
        "wheel": wheel.name,
        "bytes": wheel.stat().st_size,
        "version": version,
        "offline_install": True,
        "read_only_code": True,
        "isolated_state": True,
        "deterministic_replay": True,
        "compiler_resources": True,
        "offline_compiler_pipeline": compiler_report,
        "ui_assets_and_bootstrap": True,
        "agent_stop_resume": True,
        "public_sdk": True,
        "bootstrap_bytes": len(bootstrap_raw),
        "session_bytes": len(session_raw),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        result = check(args.wheel.resolve())
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"{exc}\n{exc.stdout or ''}\n{exc.stderr or ''}") from exc
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(gzip.compress(json.dumps(result, indent=2).encode(), mtime=0))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
