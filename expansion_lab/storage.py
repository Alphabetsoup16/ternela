# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.storage."""

import sys

from ternela import storage as _implementation

sys.modules[__name__] = _implementation
