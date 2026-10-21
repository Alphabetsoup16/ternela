# SPDX-License-Identifier: Apache-2.0
"""Bounded, hash-verified portable bundles. Also copied into the immutable launcher."""

import argparse
import ast
import gzip
import hashlib
import io
import json
import re
import tarfile
import tempfile
import tomllib
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

FORMAT = RUNTIME = 1
PYTHON = [3, 15]
MAX_DOWNLOAD = 16_000_000
MAX_EXPANDED = 64_000_000
ROOTS = (
    "ternela",
    "workbench",
    "expansion_lab",
    "source_lab",
    "docs_lab",
    "safety_lab",
    "observation_lab",
    "examples",
    "evals",
    "docs",
    "tests",
    "scripts",
)
FILES = (
    "README.md",
    "LICENSE",
    "NOTICE",
    "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "Dockerfile",
    "compose.yaml",
    ".dockerignore",
    ".gitignore",
)
SUFFIXES = {".py", ".json", ".gz", ".txt", ".md", ".html", ".css", ".js", ".svg", ".sh", ".ps1"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def version(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value
    ):
        raise ValueError("Expected a stable MAJOR.MINOR.PATCH version")
    return tuple(map(int, value.split(".")))


def release_asset(record, repository, current):
    """Select a newer stable asset, never a branch archive or arbitrary URL."""
    if (
        not isinstance(record, dict)
        or record.get("draft") is not False
        or record.get("prerelease") is not False
    ):
        raise ValueError("Release must be published and stable")
    published = record.get("published_at")
    try:
        if (
            not isinstance(published, str)
            or datetime.fromisoformat(published.replace("Z", "+00:00")).utcoffset() is None
        ):
            raise ValueError("Release publication time is missing")
    except (TypeError, ValueError) as exc:
        raise ValueError("Release publication time is invalid") from exc
    tag = record.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("Release must use a stable vMAJOR.MINOR.PATCH tag")
    target = tag[1:]
    if version(target) <= version(current):
        return None
    name = f"ternela-{tag}.tar.gz"
    assets = record.get("assets")
    if not isinstance(assets, list) or any(not isinstance(a, dict) for a in assets):
        raise ValueError("Release assets are unavailable")
    selected = [a for a in assets if a.get("name") == name]
    if len(selected) != 1:
        raise ValueError("Expected exactly one matching application bundle")
    asset = selected[0]
    if (
        asset.get("state") != "uploaded"
        or type(asset.get("size")) is not int
        or not 0 < asset["size"] <= MAX_DOWNLOAD
        or not isinstance(asset.get("digest"), str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset["digest"])
        or asset.get("browser_download_url")
        != f"https://github.com/{repository}/releases/download/{tag}/{name}"
    ):
        raise ValueError("Release asset identity, size or SHA-256 digest is invalid")
    return target, asset


def safe_name(name):
    if not isinstance(name, str):
        raise ValueError("Unsafe bundle path")
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or any(p in (".", "..", "") for p in name.split("/"))
        or str(path) != name
    ):
        raise ValueError("Unsafe bundle path")
    return path


def extract_bundle(archive, destination, repository, target):
    """Validate every member before publishing a new directory; never use extractall."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError("Bundle destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Bound the entire expanded stream, including tar metadata and padding.
    archive_limit = MAX_EXPANDED + 4_194_304
    with gzip.open(archive, "rb") as stream:
        expanded = stream.read(archive_limit + 1)
    if len(expanded) > archive_limit:
        raise ValueError("Bundle expanded stream exceeds its limit")
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as source:
        entries, total = {}, 0
        for item in source:
            safe_name(item.name)
            if not item.isfile() or item.name in entries or item.size < 0:
                raise ValueError("Bundle contains a link, special file or duplicate")
            total += item.size
            if total > MAX_EXPANDED or len(entries) >= 4096:
                raise ValueError("Bundle expanded-size or file-count limit exceeded")
            entries[item.name] = item
        entry = entries.get("release.json")
        if entry is None or entry.size > 1_000_000:
            raise ValueError("Missing or oversized release manifest")
        manifest = json.loads(source.extractfile(entry).read(), object_pairs_hook=unique_object)
        if (
            not isinstance(manifest, dict)
            or type(manifest.get("format")) is not int
            or manifest.get("format") != FORMAT
            or type(manifest.get("runtime")) is not int
            or manifest.get("runtime") != RUNTIME
            or not isinstance(manifest.get("python"), list)
            or any(type(value) is not int for value in manifest["python"])
            or manifest.get("python") != PYTHON
            or manifest.get("version") != target
            or manifest.get("repository") != repository
        ):
            raise ValueError("Bundle identity or runtime is incompatible; rebuild the image")
        version(target)
        files = manifest.get("files")
        if not isinstance(files, dict) or set(files) != set(entries) - {"release.json"}:
            raise ValueError("Manifest and bundle members differ")
        if not {"ternela/__main__.py", "seed/sources/manifest.json"} <= set(files):
            raise ValueError("Bundle lacks its application entrypoint or sources")
        with tempfile.TemporaryDirectory(prefix=".extract-", dir=destination.parent) as temporary:
            root = Path(temporary)
            for name, digest in files.items():
                path = safe_name(name)
                if (
                    path.parts[0] not in (*ROOTS, "seed")
                    and name not in FILES
                    or path.parts[0] == "data"
                    or not isinstance(digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", digest)
                ):
                    raise ValueError("Unexpected bundle file or digest")
                if path.parts[0] == "seed" and not (
                    len(path.parts) == 3
                    and path.parts[1] == "sources"
                    and re.fullmatch(r"[a-z][a-z0-9_-]*\.json(?:\.gz)?", path.parts[2])
                    or len(path.parts) == 4
                    and path.parts[1] == "blobs"
                    and re.fullmatch(r"[0-9a-f]{2}", path.parts[2])
                    and re.fullmatch(r"[0-9a-f]{64}\.gz", path.parts[3])
                    and path.parts[3].startswith(path.parts[2])
                ):
                    raise ValueError(
                        "Bundle seed may contain only source documents and source blobs"
                    )
                raw = source.extractfile(entries[name]).read()
                if sha(raw) != digest:
                    raise ValueError("Bundle file hash mismatch: " + name)
                output = root / path
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(raw)
                output.chmod(0o644)
            (root / "release.json").write_text(json.dumps(manifest, sort_keys=True) + "\n")
            root.rename(destination)
    return manifest


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def validate_url(url):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname
        not in {
            "github.com",
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
        }
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
    ):
        raise ValueError("Release download must stay on GitHub HTTPS asset hosts")


def download_asset(asset, destination):
    validate_url(asset["browser_download_url"])
    request = Request(
        asset["browser_download_url"], headers={"User-Agent": "Ternela-local-updater/1"}
    )
    with build_opener(ReleaseRedirect()).open(request, timeout=30) as response:
        raw = response.read(MAX_DOWNLOAD + 1)
    if len(raw) != asset["size"] or len(raw) > MAX_DOWNLOAD or sha(raw) != asset["digest"][7:]:
        raise ValueError("Downloaded bundle size or SHA-256 mismatch")
    Path(destination).write_bytes(raw)


def build_bundle(root, output):
    root, output = Path(root), Path(output)
    config = tomllib.loads((root / "pyproject.toml").read_text())
    target = config["project"]["version"]
    version(target)
    module = ast.parse((root / "ternela/__init__.py").read_text())
    declared = [
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets)
    ]
    if declared != [target]:
        raise ValueError("Package and project versions must match before building a release")
    repository = json.loads((root / "ternela/brand.json").read_text())["repository"]
    files = {}
    for directory in ROOTS:
        for path in sorted((root / directory).rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix in SUFFIXES
                and not any(
                    p.startswith((".", "__pycache__")) for p in path.relative_to(root).parts
                )
            ):
                files[path.relative_to(root).as_posix()] = path.read_bytes()
    for name in FILES:
        files[name] = (root / name).read_bytes()
    # Only pinned source documents and the three attributed supplemental blobs.
    pins = json.loads((root / "data/sources/manifest.json").read_text())
    for pin in pins:
        path = root / "data/sources" / (pin["provider"] + ".json.gz")
        if sha(gzip.decompress(path.read_bytes())) != pin["sha256"]:
            raise ValueError("Unverified pinned source: " + pin["provider"])
    seeds = ["sources/manifest.json", "sources/guides.json.gz", "sources/supplements.json"]
    seeds += ["sources/" + pin["provider"] + ".json.gz" for pin in pins]
    for record in json.loads((root / "data/sources/supplements.json").read_text()):
        for field in ("raw_document", "example", "schema"):
            digest = record[field]["sha256"]
            name = "blobs/" + digest[:2] + "/" + digest + ".gz"
            if sha(gzip.decompress((root / "data" / name).read_bytes())) != digest:
                raise ValueError("Unverified supplemental source")
            seeds.append(name)
    for name in seeds:
        files["seed/" + name] = (root / "data" / name).read_bytes()
    manifest = {
        "format": FORMAT,
        "runtime": RUNTIME,
        "python": PYTHON,
        "version": target,
        "repository": repository,
        "files": {n: sha(raw) for n, raw in sorted(files.items())},
    }
    files["release.json"] = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"ternela-v{target}.tar.gz"
    with (
        path.open("wb") as stream,
        gzip.GzipFile(filename="", mode="wb", fileobj=stream, mtime=0, compresslevel=9) as packed,
        tarfile.open(fileobj=packed, mode="w") as archive,
    ):
        for name, raw in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.mtime = len(raw), 0o644, 0
            archive.addfile(entry, io.BytesIO(raw))
    if path.stat().st_size > MAX_DOWNLOAD:
        raise ValueError("Application bundle exceeds the updater limit")
    # Validate the artifact we actually wrote, not just the source inputs.
    with tempfile.TemporaryDirectory() as temporary:
        extract_bundle(path, Path(temporary) / "verified", repository, target)
    (output / "SHA256SUMS").write_text(sha(path.read_bytes()) + "  " + path.name + "\n")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build"])
    parser.add_argument("--output", type=Path, default=Path("dist"))
    args = parser.parse_args()
    print(build_bundle(Path(__file__).resolve().parent.parent, args.output))


if __name__ == "__main__":
    main()
