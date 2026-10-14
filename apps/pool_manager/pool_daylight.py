# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

from pool_daylight_core import (
    DaylightMixin as _DaylightMixinBase,
    brassage_nuit_intelligent_autorise,
    moyenne_reference_temperature,
    progression_solaire,
)

# Historical public import path: these helpers are re-exported on purpose.
__all__ = [
    "DaylightMixin",
    "brassage_nuit_intelligent_autorise",
    "moyenne_reference_temperature",
    "progression_solaire",
]


class DaylightMixin(_DaylightMixinBase):
    """Public daylight role kept at its historical import path."""
