# SPDX-License-Identifier: Apache-2.0
"""Exact downloaded bytes, redirect restrictions and archive resource bounds."""

import gzip
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ternela.distribution import download_asset, extract_bundle, unique_object, validate_url


class DistributionTransportTests(unittest.TestCase):
    def asset(self, raw):
        return {
            "browser_download_url": "https://github.com/Alphabetsoup16/ternela/releases/download/v0.2.0/ternela-v0.2.0.tar.gz",
            "size": len(raw),
            "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }

    def test_download_publishes_only_exact_verified_bytes(self):
        raw = b"a release bundle"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle"
            opener = Mock()
            opener.open.return_value = io.BytesIO(raw)
            with patch("ternela.distribution.build_opener", return_value=opener):
                download_asset(self.asset(raw), path)
            self.assertEqual(path.read_bytes(), raw)

    def test_truncated_or_wrong_digest_does_not_publish(self):
        for actual in (b"short", b"different content"):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "bundle"
                opener = Mock()
                opener.open.return_value = io.BytesIO(actual)
                with (
                    patch("ternela.distribution.build_opener", return_value=opener),
                    self.assertRaises(ValueError),
                ):
                    download_asset(self.asset(b"expected content!"), path)
                self.assertFalse(path.exists())

    def test_download_stream_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            stream = io.BytesIO(b"x" * 101)
            opener = Mock()
            opener.open.return_value = stream
            with (
                patch("ternela.distribution.MAX_DOWNLOAD", 100),
                patch("ternela.distribution.build_opener", return_value=opener),
                self.assertRaises(ValueError),
            ):
                download_asset(self.asset(b"x" * 101), Path(directory) / "bundle")

    def test_redirect_hosts_and_scheme_are_restricted(self):
        for url in (
            "http://github.com/a",
            "https://evil.example/a",
            "https://github.com.evil.example/a",
            "https://user:pass@github.com/a",
            "https://github.com:444/a",
            "file:///tmp/a",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        validate_url("https://release-assets.githubusercontent.com/a?signature=public-download")

    def test_entire_expanded_tar_stream_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "bomb.tar.gz"
            path.write_bytes(gzip.compress(b"x" * 4_200_000))
            with (
                patch("ternela.distribution.MAX_EXPANDED", 100),
                self.assertRaisesRegex(ValueError, "expanded stream"),
            ):
                extract_bundle(path, root / "app", "Alphabetsoup16/ternela", "0.2.0")

    def test_duplicate_json_keys_are_not_ambiguous(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            unique_object([("version", "0.2.0"), ("version", "0.3.0")])


if __name__ == "__main__":
    unittest.main()
