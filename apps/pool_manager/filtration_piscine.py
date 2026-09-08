# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import hassapi as hass

from pool_common import *
from pool_lifecycle import LifecycleMixin
from pool_devices import DevicesMixin
from pool_strategy import StrategyMixin
from pool_control import ControlMixin


class FiltrationPiscine(
    LifecycleMixin,
    DevicesMixin,
    StrategyMixin,
    ControlMixin,
    hass.Hass,
):
    pass
