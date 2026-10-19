# SPDX-License-Identifier: Apache-2.0
"""Replay unchanged regression probes against the supplemental evidence version."""
from expansion_lab import grade as original
from safety_lab import grade as safety
from .evidence import SourceIndex

if __name__ == "__main__":
    original.SourceIndex = SourceIndex
    safety.main()
