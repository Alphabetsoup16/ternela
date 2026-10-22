# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.compiler.inference."""

import sys

from ternela.compiler import inference as _implementation

sys.modules[__name__] = _implementation
