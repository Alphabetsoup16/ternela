# SPDX-License-Identifier: Apache-2.0
"""PyPI exact-release metadata; reported digests are not downloaded-byte verification."""

import re
from dataclasses import dataclass

from .release import EvidenceField, ReleaseConnector


@dataclass(frozen=True)
class PyPIConnector(ReleaseConnector):
    display_name = "PyPI"
    package_example = "httpx"
    version_example = "0.28.1"
    purpose = "at least one non-yanked distribution with a reported SHA-256"
    conditions = (
        EvidenceField(
            "usable_files", "integer", "gt", 0, "At least one non-yanked file reports a SHA-256."
        ),
    )

    def package_name(self, value):
        if (
            not isinstance(value, str)
            or len(value) > 214
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", value)
        ):
            raise ValueError("Invalid public PyPI package name")
        return re.sub(r"[-_.]+", "-", value).lower()

    def normalize(self, body):
        info, files = body["info"], body["urls"]
        if not isinstance(files, list):
            raise ValueError("Release files must be an array")
        usable = 0
        for file in files:
            if type(file["yanked"]) is not bool or not isinstance(file["filename"], str):
                raise ValueError("Malformed distribution metadata")
            digest = file["digests"]["sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
                raise ValueError("Missing or malformed distribution SHA-256")
            usable += not file["yanked"] and bool(file["filename"])
        return {
            "name": self.package_name(info["name"]),
            "version": self.version_name(info["version"]),
            "usable_files": usable,
        }
