# SPDX-License-Identifier: Apache-2.0
"""npm exact-version metadata; integrity syntax is not an artifact safety claim."""

import base64
import binascii
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .release import EvidenceField, ReleaseConnector, exact_version


def valid_integrity(value):
    sizes = {"sha256": 32, "sha384": 48, "sha512": 64}
    for token in value.split():
        algorithm, separator, digest = token.partition("-")
        if separator and algorithm in sizes:
            try:
                if len(base64.b64decode(digest, validate=True)) == sizes[algorithm]:
                    return True
            except (ValueError, binascii.Error):
                pass
    return False


@dataclass(frozen=True)
class NpmConnector(ReleaseConnector):
    display_name = "npm"
    package_example = "lodash or @scope/package"
    version_example = "4.17.21"
    purpose = "an HTTPS tarball URL and syntactically valid registry-reported SRI metadata"
    conditions = (
        EvidenceField(
            "has_integrity",
            "boolean",
            "eq",
            True,
            "Registry reports a syntactically valid integrity token.",
        ),
        EvidenceField(
            "has_tarball",
            "boolean",
            "eq",
            True,
            "Registry reports an HTTPS tarball URL without credentials.",
        ),
    )

    def package_name(self, value):
        if (
            not isinstance(value, str)
            or len(value) > 214
            or not re.fullmatch(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*", value)
        ):
            raise ValueError("Invalid public npm package name")
        return value

    def version_name(self, value):
        value = exact_version(value)
        if not re.fullmatch(
            r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
            r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?",
            value,
        ):
            raise ValueError("npm requires an exact semantic version, not a tag or range")
        if "-" in value.split("+", 1)[0]:
            prerelease = value.split("+", 1)[0].split("-", 1)[1]
            if any(
                part.isdigit() and len(part) > 1 and part.startswith("0")
                for part in prerelease.split(".")
            ):
                raise ValueError("Invalid npm prerelease version")
        return value

    def normalize(self, body):
        distribution = body["dist"]
        integrity, tarball = distribution["integrity"], distribution["tarball"]
        if not isinstance(integrity, str) or not isinstance(tarball, str):
            raise ValueError("Missing distribution metadata")
        address = urlsplit(tarball)
        return {
            "name": self.package_name(body["name"]),
            "version": self.version_name(body["version"]),
            "has_integrity": valid_integrity(integrity),
            "has_tarball": bool(address.hostname)
            and address.scheme == "https"
            and not (address.username or address.password or address.fragment),
        }
