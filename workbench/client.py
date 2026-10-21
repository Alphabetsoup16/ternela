# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.client."""

import sys

from ternela import client as _implementation

sys.modules[__name__] = _implementation
