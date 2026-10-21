# SPDX-License-Identifier: Apache-2.0
"""Compatibility alias for ternela.acquisition; no second implementation."""

import sys

from ternela import acquisition as _implementation

sys.modules[__name__] = _implementation
