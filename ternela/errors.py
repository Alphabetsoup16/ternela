# SPDX-License-Identifier: Apache-2.0
"""Stable domain errors; ValueError/KeyError ancestry preserves caller compatibility."""


class UnsupportedVersion(ValueError):
    code = "unsupported_version"


class AdmissionError(ValueError):
    code = "admission_failed"


class ObservationConflict(ValueError):
    code = "observation_conflict"


class EventConflict(ObservationConflict):
    code = "event_conflict"


class LeaseExpired(ObservationConflict):
    code = "lease_expired"


class WatchNotFound(KeyError):
    code = "watch_not_found"


class DeliveryNotFound(KeyError):
    code = "delivery_not_found"


class RecipeArgumentsError(ValueError):
    code = "recipe_arguments"
