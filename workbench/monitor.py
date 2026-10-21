# SPDX-License-Identifier: Apache-2.0
"""Compatibility alias for ternela.monitor."""

import sys

from ternela import monitor as _implementation

sys.modules[__name__] = _implementation
