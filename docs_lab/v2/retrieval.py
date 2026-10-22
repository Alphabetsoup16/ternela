# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.compiler.retrieval."""

import sys

from ternela.compiler import retrieval as _implementation

sys.modules[__name__] = _implementation
