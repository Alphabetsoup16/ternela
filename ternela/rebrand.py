# SPDX-License-Identifier: Apache-2.0
"""Update the working brand without changing Python imports or stored records."""

import argparse
import json
import re

from ternela.storage import PROJECT, write_json

from .branding import BRAND, BRAND_PATH

# These documents contain marketing names or repository URLs. Runtime identity
# comes from brand.json; package names and protocol fields deliberately stay stable.
DOCUMENTS = (
    "README.md",
    "NOTICE",
    "docs/architecture.md",
    "THIRD_PARTY_NOTICES.md",
    "docs/rebranding.md",
    "docs/roadmap.md",
    "docs/monitoring.md",
    "docs/public-connectors.md",
    "docs/status-readiness.md",
    "docs/local-install.md",
    "docs/releasing.md",
    "docs/adding-connectors.md",
    "docs/semantic-review.md",
    "docs/api.md",
    "docs/evaluation.md",
    "pyproject.toml",
)


def update(name, repository, tagline):
    current = json.loads(BRAND_PATH.read_text())
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9 -]{0,47}", name):
        raise ValueError("Use a product name of 1–48 letters, digits, spaces or hyphens")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Expected a GitHub owner/repository")
    if not tagline or len(tagline) > 160 or any(c in tagline for c in "<>\r\n"):
        raise ValueError("Use a single plain-text tagline of at most 160 characters")
    replacements = (
        ("https://github.com/" + current["repository"], "https://github.com/" + repository),
        (current["tagline"], tagline),
        (current["name"], name),
    )
    mapping = dict(replacements)
    pattern = re.compile(
        "|".join(re.escape(value) for value in sorted(mapping, key=len, reverse=True))
    )
    # Prepare every edit before writing. Lowercase ternela is a stable technical
    # namespace and is not changed by a marketing rebrand.
    edits = {}
    for relative in DOCUMENTS:
        path = PROJECT / relative
        value = path.read_text()
        value = pattern.sub(lambda match: mapping[match[0]], value)
        edits[path] = value
    for path, value in edits.items():
        path.write_text(value)
    write_json(BRAND_PATH, {"name": name, "tagline": tagline, "repository": repository})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True)
    parser.add_argument("--repository", default=BRAND["repository"])
    parser.add_argument("--tagline", default=BRAND["tagline"])
    args = parser.parse_args()
    update(args.name, args.repository, args.tagline)
    print("Updated branding. Review the diff; Python imports and local data are unchanged.")


if __name__ == "__main__":
    main()
