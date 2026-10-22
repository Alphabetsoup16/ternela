# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.compiler.evidence."""

import sys

from ternela.compiler import evidence as _implementation

sys.modules[__name__] = _implementation
