# SPDX-License-Identifier: Apache-2.0
"""Synthetic HTTP envelopes shared by the regression probes."""

import copy


def envelope(body, *, status=200, next_page=None):
    return {
        "http_status": status,
        "body": copy.deepcopy(body),
        "headers_complete": True,
        "headers": (
            {"link": f'<https://api.example.test/resource?page={next_page}>; rel="next"'}
            if next_page
            else {}
        ),
        "url": "https://api.example.test/resource",
    }
