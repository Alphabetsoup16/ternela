# SPDX-License-Identifier: Apache-2.0
"""Reliable waits and inspectable evidence for agents."""

__version__ = "0.1.3"
__all__ = ["Client", "__version__"]


def __getattr__(name):
    if name == "Client":
        from .client import ObservationClient

        globals()[name] = ObservationClient
        return ObservationClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
