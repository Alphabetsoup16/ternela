# SPDX-License-Identifier: Apache-2.0
"""Public repository reads keep GitHub's JSON representation and pinned source schema."""

from .base import PublicConnector


class GithubConnector(PublicConnector):
    display_name = "GitHub"


GITHUB = GithubConnector(
    provider="github",
    host="api.github.com",
    operation="GET /repos/",
    documentation="https://docs.github.com/en/rest",
    operation_prefix=True,
    allow_query=True,
    allow_continuation=True,
)
