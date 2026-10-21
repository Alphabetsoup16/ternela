# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.specification."""

import sys

from ternela import specification as _implementation

sys.modules[__name__] = _implementation
