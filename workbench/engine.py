# SPDX-License-Identifier: Apache-2.0
"""Compatibility alias for ternela.watches; no second implementation."""

import sys

from ternela import watches as _implementation

sys.modules[__name__] = _implementation
