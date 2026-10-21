# SPDX-License-Identifier: Apache-2.0
"""Adversarial release inputs, written before the distribution implementation."""

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from ternela.distribution import extract_bundle, release_asset, version

REPO = "Alphabetsoup16/ternela"


def metadata(tag="v0.2.0", **changes):
    name = f"ternela-{tag}.tar.gz"
    result = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-10-19T10:00:00Z",
        "assets": [
            {
                "name": name,
                "state": "uploaded",
                "size": 500,
                "digest": "sha256:" + "a" * 64,
                "browser_download_url": f"https://github.com/{REPO}/releases/download/{tag}/{name}",
            }
        ],
    }
    return dict(result, **changes)


def archive(root, *, change=None, extra=None):
    files = {"ternela/__main__.py": b"pass\n", "seed/sources/manifest.json": b"[]\n"}
    manifest = {
        "format": 1,
        "runtime": 1,
        "python": [3, 15],
        "version": "0.2.0",
        "repository": REPO,
        "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
    }
    if change:
        change(manifest)
    files["release.json"] = json.dumps(manifest).encode()
    path = root / "candidate.tar.gz"
    with tarfile.open(path, "w:gz") as output:
        for name, raw in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(raw)
            output.addfile(entry, io.BytesIO(raw))
        if extra:
            entry = tarfile.TarInfo(extra[0])
            entry.type = extra[1]
            entry.linkname = "../../outside"
            output.addfile(entry, io.BytesIO(b""))
    return path


class DistributionTests(unittest.TestCase):
    def test_stable_versions_only(self):
        self.assertEqual(version("1.12.3"), (1, 12, 3))
        for invalid in ("v1.2.3", "01.2.3", "1.2", "1.2.3-beta", "../1.2.3", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                version(invalid)

    def test_only_newer_published_stable_release(self):
        self.assertEqual(release_asset(metadata(), REPO, "0.1.0")[0], "0.2.0")
        self.assertIsNone(release_asset(metadata(), REPO, "0.2.0"))
        self.assertIsNone(release_asset(metadata(), REPO, "0.3.0"))
        for change in (
            {"draft": True},
            {"prerelease": True},
            {"published_at": None},
            {"published_at": "yesterday"},
            {"tag_name": "v0.3.0-rc1"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                release_asset(metadata(**change), REPO, "0.1.0")

    def test_asset_identity_and_integrity_are_required(self):
        for change in (
            {"digest": None},
            {"digest": "sha256:bad"},
            {"state": "new"},
            {"size": 0},
            {"size": 20_000_000},
            {"browser_download_url": "https://evil.example/app"},
        ):
            record = metadata()
            record["assets"][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                release_asset(record, REPO, "0.1.0")
        for assets in ([], metadata()["assets"] * 2):
            with self.assertRaises(ValueError):
                release_asset(metadata(assets=assets), REPO, "0.1.0")

    def test_bundle_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = archive(root)
            manifest = extract_bundle(path, root / "app", REPO, "0.2.0")
            self.assertEqual(manifest["version"], "0.2.0")
            self.assertEqual((root / "app/ternela/__main__.py").read_bytes(), b"pass\n")

    def test_manifest_mismatch_and_tampering(self):
        changes = [
            lambda m: m.update(repository="someone/else"),
            lambda m: m.update(version="0.3.0"),
            lambda m: m.update(runtime=2),
            lambda m: m.update(python=[3, 16]),
            lambda m: m["files"].update({"ternela/__main__.py": "0" * 64}),
        ]
        for change in changes:
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
                root = Path(directory)
                extract_bundle(archive(root, change=change), root / "app", REPO, "0.2.0")

    def test_archive_paths_links_and_duplicate_members(self):
        extras = [
            ("../escape", tarfile.REGTYPE),
            ("/absolute", tarfile.REGTYPE),
            ("data/something", tarfile.REGTYPE),
            ("link", tarfile.SYMTYPE),
            ("hardlink", tarfile.LNKTYPE),
            ("ternela/__main__.py", tarfile.REGTYPE),
        ]
        for extra in extras:
            with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
                root = Path(directory)
                extract_bundle(archive(root, extra=extra), root / "app", REPO, "0.2.0")


if __name__ == "__main__":
    unittest.main()
