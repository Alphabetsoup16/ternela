# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.sources."""

import sys

from ternela import sources as _implementation

sys.modules[__name__] = _implementation
