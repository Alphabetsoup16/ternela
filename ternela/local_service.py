# SPDX-License-Identifier: Apache-2.0
"""Single-container supervisor; no Docker socket, host scheduler or host Python."""

import argparse
import fcntl
import hashlib
import json
import logging
import math
import os
import random
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.request import urlopen

try:  # Immutable launcher in the final image; normal package imports in tests.
    from .distribution import download_asset, extract_bundle, safe_name, unique_object, version
except ImportError:
    from distribution import download_asset, extract_bundle, safe_name, unique_object, version


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x") as stream:
            json.dump(value, stream, sort_keys=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read(path, default=None):
    return (
        json.loads(Path(path).read_text(), object_pairs_hook=unique_object)
        if Path(path).is_file()
        else default
    )


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class LocalService:
    def __init__(self, state, bootstrap):
        self.state, self.bootstrap = Path(state), Path(bootstrap)
        self.state.mkdir(parents=True, exist_ok=True)
        self.releases = self.state / "releases"
        self.releases.mkdir(exist_ok=True)
        self.stop = threading.Event()
        self.child = None
        self.log = logging.getLogger("ternela.local")
        self.repository = None

    def consent(self):
        try:
            config = read(self.state / "settings.json", {})
            return isinstance(config, dict) and config.get("automatic_updates") is True
        except (OSError, ValueError):
            self.log.warning("Unreadable update consent; automatic updates remain disabled")
            return False

    def current(self):
        record = read(self.state / "current.json")
        version(record["version"])
        return record

    def app(self, target):
        version(target)
        return self.releases / target

    def seed(self, target):
        source = self.app(target) / "seed"
        destination = self.state / "data"
        destination.mkdir(exist_ok=True)
        for path in source.rglob("*"):
            if path.is_file():
                output = destination / path.relative_to(source)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, output)
        link = self.app(target) / "data"
        if not link.is_symlink():
            link.symlink_to(destination, target_is_directory=True)

    def initialize(self):
        with tarfile.open(self.bootstrap, "r:gz") as archive:
            manifest = json.load(archive.extractfile("release.json"))
        self.repository = manifest["repository"]
        target = manifest["version"]
        if not self.app(target).exists():
            extract_bundle(self.bootstrap, self.app(target), self.repository, target)
        fresh_install = not (self.state / "current.json").exists()
        if not (self.state / "settings.json").exists():
            # Only the exact opt-in value enables updates on the first run.
            save(
                self.state / "settings.json",
                {
                    "automatic_updates": fresh_install
                    and os.environ.get("TERNELA_AUTO_UPDATE") == "true"
                },
            )
        if not (self.state / "current.json").exists():
            self.seed(target)
            save(self.state / "current.json", {"version": target, "previous": None})
        self.recover()
        # Replacing the image with a newer bundled app is an explicit manual upgrade.
        old_version = self.current()["version"]
        if version(target) > version(old_version) and not self.blocked_version(target):
            try:
                self.apply(target)
            except Exception as exc:
                if (
                    (self.state / "transaction.json").exists()
                    or self.current()["version"] != old_version
                    or not self.healthy(old_version)
                ):
                    raise
                save(self.state / "failed-version.json", {"version": target})
                save(
                    self.state / "update-status.json",
                    {"status": "error", "failed_version": target, "reason": str(exc)},
                )
                self.log.warning("Bundled app rejected; restored version %s: %s", old_version, exc)
        self.prune()

    def blocked_version(self, target):
        try:
            record = read(self.state / "failed-version.json", {})
            if not isinstance(record, dict):
                return True
            if not record:
                return False
            version(record.get("version"))
            return record["version"] == target
        except (OSError, ValueError):
            self.log.warning("Unreadable failure record; automatic application remains blocked")
            return True

    def start_app(self, target):
        self.seed(target)
        self.child = subprocess.Popen(
            [sys.executable, "-u", "-m", "ternela", "--container", "--port", "8765"],
            cwd=self.app(target),
            start_new_session=True,
        )

    def stop_app(self):
        if self.child is not None:
            try:
                os.killpg(self.child.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            try:
                self.child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
            # A dead leader can leave compiler children alive. Fence the entire group
            # before taking a data snapshot or starting another application version.
            try:
                os.killpg(self.child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.child.wait()
            self.child = None

    def healthy(self, target, timeout=45):
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self.stop.is_set():
            if self.child is None or self.child.poll() is not None:
                return False
            try:
                with urlopen("http://127.0.0.1:8765/api/health", timeout=2) as response:
                    body = json.load(response)
                if (
                    body.get("status") == "ok"
                    and body.get("version") == target
                    and body.get("sources_present") is True
                ):
                    # Exercise the catalog/source path as well as the process liveness check.
                    with urlopen("http://127.0.0.1:8765/api/bootstrap", timeout=3) as response:
                        return isinstance(json.load(response).get("templates"), list)
            except (OSError, ValueError):
                pass
            self.stop.wait(0.25)
        return False

    def backup(self):
        path = self.state / "pre-update.tar.gz"
        temporary = self.state / "pre-update.tmp"
        with tarfile.open(temporary, "w:gz", compresslevel=6, dereference=True) as archive:
            for source in sorted((self.state / "data").rglob("*")):
                if source.is_symlink():
                    raise ValueError("Data backup refuses symbolic links")
                if source.is_file():
                    archive.add(
                        source, arcname=source.relative_to(self.state / "data"), recursive=False
                    )
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        temporary.replace(path)
        save(
            self.state / "pre-update.json", {"sha256": file_sha(path), "bytes": path.stat().st_size}
        )

    def restore(self):
        archive_path = self.state / "pre-update.tar.gz"
        expected = read(self.state / "pre-update.json", {})
        if archive_path.stat().st_size != expected.get("bytes") or file_sha(
            archive_path
        ) != expected.get("sha256"):
            raise ValueError("Pre-update backup integrity check failed; data left in place")
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=self.state) as temporary:
            root = Path(temporary) / "data"
            root.mkdir()
            with tarfile.open(archive_path, "r:gz") as archive:
                seen = set()
                for entry in archive:
                    safe_name(entry.name)
                    if not entry.isfile() or entry.name in seen:
                        raise ValueError("Invalid pre-update backup")
                    seen.add(entry.name)
                    output = root / entry.name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(entry) as source, output.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
            if (self.state / "data").exists():
                shutil.rmtree(self.state / "data")
            root.rename(self.state / "data")

    def recover(self):
        transaction = read(self.state / "transaction.json")
        if not transaction:
            return
        current = self.current()
        version(transaction["old"])
        version(transaction["target"])
        if current["version"] not in {transaction["old"], transaction["target"]}:
            raise ValueError(
                "Recovery journal does not match the current application; data left in place"
            )
        if current["version"] != transaction["target"]:
            # An uncommitted candidate may have changed data. Always restore first.
            self.restore()
            save(self.state / "failed-version.json", {"version": transaction["target"]})
            save(
                self.state / "update-status.json",
                {
                    "status": "rolled_back",
                    "failed_version": transaction["target"],
                    "reason": "Interrupted update recovered",
                },
            )
        (self.state / "transaction.json").unlink()

    def apply(self, target):
        old = self.current()
        self.stop_app()
        try:
            self.backup()
            save(self.state / "transaction.json", {"old": old["version"], "target": target})
            self.start_app(target)
            if not self.healthy(target):
                raise RuntimeError("Candidate did not pass startup checks")
            save(self.state / "current.json", {"version": target, "previous": old["version"]})
        except Exception:
            # replace() may publish the commit before directory fsync reports an
            # error. Preserve the journal and candidate data; restart recovery
            # decides from the visible marker instead of rolling back underneath it.
            if self.current()["version"] == target:
                raise
            self.stop_app()
            # A backup failure happens before any candidate can change data.
            if (self.state / "transaction.json").exists():
                self.restore()
            self.start_app(old["version"])
            if not self.healthy(old["version"]):
                raise RuntimeError("Rollback failed; stop and inspect the saved backup") from None
            (self.state / "transaction.json").unlink(missing_ok=True)
            raise
        (self.state / "transaction.json").unlink()
        (self.state / "failed-version.json").unlink(missing_ok=True)
        self.prune()

    def prune(self):
        record = self.current()
        keep = {record["version"], record.get("previous")}
        for path in self.releases.iterdir():
            if path.name not in keep and path.is_dir():
                shutil.rmtree(path)
        results = sorted(
            (self.state / "results").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for path in results[10:]:
            path.unlink()

    def probe(self):
        target = self.current()["version"]
        result = subprocess.run(
            [sys.executable, "-m", "ternela.update_probe", target],
            cwd=self.app(target),
            capture_output=True,
            text=True,
            timeout=90,
        )
        if result.returncode:
            raise RuntimeError("Release evidence unavailable: " + result.stderr[-1000:])
        return json.loads(result.stdout)

    def check(self, automatic=False):
        if automatic and not self.consent():
            return {"status": "disabled"}
        from_asset = None
        target = None
        try:
            evidence = self.probe()
            status = {
                "checked_at": time.time(),
                "watch_id": evidence.get("watch_id"),
                "automatic": automatic,
            }
            if evidence.get("decision") != "complete":
                decision = evidence.get("decision")
                result = dict(
                    status,
                    status="no_update" if decision == "pending" else "unavailable",
                    decision=decision,
                )
            else:
                expires = evidence.get("expires_at")
                if (
                    type(expires) not in (int, float)
                    or not math.isfinite(expires)
                    or time.time() >= expires
                ):
                    raise ValueError("Release evidence is missing or expired")
                try:
                    from .distribution import release_asset
                except ImportError:
                    from distribution import release_asset
                from_asset = release_asset(
                    evidence["release"], self.repository, self.current()["version"]
                )
                if from_asset is None:
                    result = dict(status, status="no_update")
                else:
                    target, asset = from_asset
                    if automatic and self.blocked_version(target):
                        return dict(status, status="skipped_failed", failed_version=target)
                    with tempfile.TemporaryDirectory(
                        prefix=".download-", dir=self.state
                    ) as temporary:
                        archive = Path(temporary) / "release.tar.gz"
                        download_asset(asset, archive)
                        candidate = self.app(target)
                        if candidate.exists():
                            shutil.rmtree(candidate)
                        extract_bundle(archive, candidate, self.repository, target)
                    if self.stop.is_set() or automatic and not self.consent():
                        shutil.rmtree(candidate)
                        return dict(status, status="cancelled")
                    if time.time() >= expires:
                        raise ValueError("Release evidence expired before installation")
                    self.apply(target)
                    result = dict(status, status="updated", version=target)
        except Exception as exc:
            if (self.state / "transaction.json").exists():
                # Never swallow an uncertain rollback; let the container restart recovery.
                raise
            result = {
                "status": "error",
                "checked_at": time.time(),
                "reason": str(exc),
                "failed_version": target,
            }
            if target is not None and not isinstance(exc, OSError):
                save(self.state / "failed-version.json", {"version": target})
            self.log.warning("Update left current version in place: %s", exc)
        save(self.state / "update-status.json", result)
        self.prune()
        return result

    def process_commands(self):
        for command in sorted((self.state / "commands").glob("*.json")):
            result_path = self.state / "results" / command.name
            if result_path.is_file():
                # A crash between publishing a result and deleting its request
                # must not retry an already handled update.
                command.unlink()
                continue
            try:
                request = read(command)
            except (ValueError, UnicodeError):
                request = None
            result = (
                self.check()
                if request == {"action": "update"}
                else {"status": "error", "reason": "Invalid local command"}
            )
            save(result_path, result)
            command.unlink()
            self.prune()

    def run(self):
        lock = (self.state / "supervisor.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RuntimeError("Another supervisor already owns this folder") from None
        try:
            try:
                from .storage_control import recover
            except ImportError:
                from storage_control import recover
            recover(self.state)
            self.initialize()
            if self.child is None:
                self.start_app(self.current()["version"])
            if not self.healthy(self.current()["version"]):
                raise RuntimeError("Local app did not pass startup checks")
            next_check = time.monotonic() + 30
            while not self.stop.wait(1):
                if self.child.poll() is not None:
                    raise RuntimeError("Application exited; Docker will restart the container")
                self.process_commands()
                if time.monotonic() >= next_check:
                    self.check(automatic=True)
                    next_check = time.monotonic() + 3600 + random.uniform(0, 300)
        finally:
            self.stop_app()
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state", type=Path, default=Path(os.environ.get("TERNELA_STATE", "/state"))
    )
    parser.add_argument("--bootstrap", type=Path, default=Path("/opt/bootstrap.tar.gz"))
    args = parser.parse_args()
    service = LocalService(args.state, args.bootstrap)
    log_dir = args.state / "logs"
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(log_dir / "updates.log", maxBytes=1_000_000, backupCount=2),
        ],
    )
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: service.stop.set())
    service.run()


if __name__ == "__main__":
    main()
