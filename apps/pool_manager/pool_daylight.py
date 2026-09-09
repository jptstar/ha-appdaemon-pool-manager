# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

from pool_daylight_core import (
    DaylightMixin as _DaylightMixinBase,
    brassage_nuit_intelligent_autorise,
    moyenne_reference_temperature,
    progression_solaire,
)


class DaylightMixin(_DaylightMixinBase):
    """Daylight policy refinements layered on the v0.4.0 core."""

    def stabilite_surplus_ok(self, surplus_disponible):
        """Do not let lack of PV block sanitary daytime catch-up.

        The 500 W / stability gate still applies to an opportunistic solar-only
        start because ControlMixin separately checks the solar-start threshold.
        During daylight, however, a calculated filtration delay may start the
        pump even with little or no PV available.
        """
        if self.suivre_soleil_reel:
            daytime = self._daylight_active()
        else:
            daytime = self._fixed_range_active(
                self.heure_debut_solaire,
                self.heure_fin_solaire,
            )

        if not daytime:
            self.debut_stabilite_surplus = None
            return False, self.tempo_stabilite_surplus

        try:
            surplus = float(surplus_disponible)
        except (TypeError, ValueError):
            surplus = 0.0

        if surplus < self.seuil_surplus_demarrage_w:
            # No PV stability wait for a sanitary catch-up start. The normal
            # solar-only branch still cannot start because its threshold test is
            # false; only the separate "rattrapage retard" branch can proceed.
            self.debut_stabilite_surplus = None
            return True, 0

        return super().stabilite_surplus_ok(surplus)
