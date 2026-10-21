# SPDX-License-Identifier: Apache-2.0
"""Explicit reviewed built-ins; importing a provider never performs acquisition."""

from .github import GITHUB
from .github_status import GithubStatusConnector
from .npm import NpmConnector
from .pypi import PyPIConnector

BUILTINS = (
    GITHUB,
    GithubStatusConnector(
        "github_status",
        "www.githubstatus.com",
        "GET /api/v2/summary.json",
        "https://www.githubstatus.com/api",
    ),
    PyPIConnector(
        "pypi", "pypi.org", "GET /pypi/{package}/{version}/json", "https://docs.pypi.org/api/json/"
    ),
    NpmConnector(
        "npm",
        "registry.npmjs.org",
        "GET /{package}/{version}",
        "https://github.com/npm/registry/blob/main/docs/REGISTRY-API.md",
    ),
)
