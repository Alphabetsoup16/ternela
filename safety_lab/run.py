# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.compiler.feedback."""

import sys

from ternela.compiler import feedback as _implementation

sys.modules[__name__] = _implementation
