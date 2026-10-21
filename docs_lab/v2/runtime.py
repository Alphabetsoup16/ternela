# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.evaluation."""

import sys

from ternela import evaluation as _implementation

sys.modules[__name__] = _implementation
