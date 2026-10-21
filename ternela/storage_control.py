# SPDX-License-Identifier: Apache-2.0
"""Offline, lock-protected state transfer. Archives are local recovery material."""

import argparse
import contextlib
import fcntl
import gzip
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath

try:
    from .local_service import save
except ImportError:
    from local_service import save

WORK = ".state-transfer"
MANIFEST = ".ternela-backup.json"
MAX_BYTES = 8 * 1024**3
MAX_ENTRIES = 100_000


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def safe(name):
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or path.parts[0] in {WORK, "supervisor.lock", MANIFEST}
    ):
        raise ValueError("Unsafe backup path")
    return path


def link_allowed(name, target):
    return bool(
        re.fullmatch(r"releases/[0-9]+\.[0-9]+\.[0-9]+/data", name) and target == "/state/data"
    )


def recover(state):
    """Roll back an interrupted import; a committed import only needs cleanup."""
    work = state / WORK
    if not work.exists():
        return
    journal = work / "journal.json"
    if journal.exists():
        record = json.loads(journal.read_text())
        if record["phase"] != "committed":
            for name in set(record["new"]) - set(record["old"]):
                safe(name)
                remove(state / name)
            for name in record["old"]:
                safe(name)
                old = work / "old" / name
                if old.exists() or old.is_symlink():
                    remove(state / name)
                    old.rename(state / name)
    shutil.rmtree(work)


@contextlib.contextmanager
def locked(state):
    state.mkdir(parents=True, exist_ok=True)
    with (state / "supervisor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Stop Ternela before transferring its state") from None
        recover(state)
        yield


def export_state(state, stream):
    records, total = {}, 0
    with (
        gzip.GzipFile(filename="", fileobj=stream, mode="wb", compresslevel=6) as compressed,
        tarfile.open(fileobj=compressed, mode="w|") as archive,
    ):
        for path in sorted(state.rglob("*")):
            name = path.relative_to(state).as_posix()
            if name.split("/")[0] in {WORK, "supervisor.lock"}:
                continue
            safe(name)
            info = archive.gettarinfo(str(path), arcname=name)
            total += info.size
            if total > MAX_BYTES or len(records) >= MAX_ENTRIES - 1:
                raise ValueError("Backup exceeds transfer limits")
            # Ownership is provided by the receiving container, never by the archive.
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o700 if info.isdir() or info.mode & 0o100 else 0o600
            if info.issym() and link_allowed(name, info.linkname):
                records[name] = {"link": info.linkname}
                archive.addfile(info)
            elif info.isdir():
                records[name] = {"directory": True}
                archive.addfile(info)
            elif info.isfile() or info.islnk():
                info.type, info.linkname, info.size = tarfile.REGTYPE, "", path.stat().st_size
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(block)
                    source.seek(0)
                    archive.addfile(info, source)
                records[name] = {"bytes": info.size, "sha256": digest.hexdigest()}
            else:
                raise ValueError("Backup refuses special files or unexpected links")
        raw = json.dumps({"format": 1, "entries": records}, sort_keys=True).encode()
        if total + len(raw) > MAX_BYTES or len(raw) > 32_000_000:
            raise ValueError("Backup exceeds transfer limits")
        info = tarfile.TarInfo(MANIFEST)
        info.size, info.mode = len(raw), 0o600
        archive.addfile(info, io.BytesIO(raw))


def import_state(state, stream):
    """Validate the entire archive before changing any existing state."""
    work = state / WORK
    work.mkdir(mode=0o700)
    new = work / "new"
    new.mkdir()
    records, manifest, total = {}, None, 0
    try:
        with tarfile.open(fileobj=stream, mode="r|gz") as archive:
            for entry in archive:
                total += entry.size
                if total > MAX_BYTES or len(records) >= MAX_ENTRIES:
                    raise ValueError("Backup exceeds transfer limits")
                if entry.name == MANIFEST:
                    if manifest is not None or not entry.isfile() or entry.size > 32_000_000:
                        raise ValueError("Invalid backup manifest")
                    manifest = json.load(archive.extractfile(entry))
                    continue
                name = entry.name
                safe(name)
                if name in records or manifest is not None:
                    raise ValueError("Duplicate entry or data after manifest")
                output = new / name
                # Never traverse an archive-created link.
                if any(
                    (new / parent).is_symlink()
                    for parent in PurePosixPath(name).parents
                    if str(parent) != "."
                ):
                    raise ValueError("Backup traverses a link")
                output.parent.mkdir(parents=True, exist_ok=True)
                if entry.isdir():
                    output.mkdir(mode=0o700, exist_ok=True)
                    records[name] = {"directory": True}
                elif entry.issym() and link_allowed(name, entry.linkname):
                    output.symlink_to(entry.linkname)
                    records[name] = {"link": entry.linkname}
                elif entry.isfile():
                    digest = hashlib.sha256()
                    with archive.extractfile(entry) as source, output.open("xb") as destination:
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(block)
                            destination.write(block)
                        destination.flush()
                        os.fsync(destination.fileno())
                    output.chmod(0o700 if entry.mode & 0o100 else 0o600)
                    records[name] = {"bytes": entry.size, "sha256": digest.hexdigest()}
                else:
                    raise ValueError("Backup refuses special files or unexpected links")
        if manifest != {"format": 1, "entries": records}:
            raise ValueError("Backup integrity check failed")
        if not (new / "data").is_dir() or not (new / "settings.json").is_file():
            raise ValueError("Backup lacks application data or preferences")
        old = work / "old"
        old.mkdir()
        record = {
            "phase": "replacing",
            "old": sorted(
                p.name for p in state.iterdir() if p.name not in {WORK, "supervisor.lock"}
            ),
            "new": sorted(p.name for p in new.iterdir()),
        }
        save(work / "journal.json", record)
        for name in record["old"]:
            (state / name).rename(old / name)
        for name in record["new"]:
            (new / name).rename(state / name)
        save(work / "journal.json", dict(record, phase="committed"))
    finally:
        recover(state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    args = parser.parse_args()
    state = Path(os.environ.get("TERNELA_STATE", "/state"))
    with locked(state):
        if args.action == "backup":
            export_state(state, sys.stdout.buffer)
        else:
            import_state(state, sys.stdin.buffer)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, tarfile.TarError, EOFError) as exc:
        raise SystemExit(str(exc)) from None
