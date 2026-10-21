# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.plan_audit."""

import sys

from ternela import plan_audit as _implementation

sys.modules[__name__] = _implementation
