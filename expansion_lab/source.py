# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.pinned_sources."""

import sys

from ternela import pinned_sources as _implementation

sys.modules[__name__] = _implementation
