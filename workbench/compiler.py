# SPDX-License-Identifier: Apache-2.0
"""Compatibility import; implementation lives in ternela.compiler.jobs."""

import sys

from ternela.compiler import jobs as _implementation

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
