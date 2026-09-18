# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Small runtime guards that do not belong to the filtration strategy."""

import datetime

from pool_common import TAB_MODE


class RuntimeStabilityMixin:
    """Keep remote pump ownership stable when local iSaver control is disabled."""

    def initialize(self):
        # Aquagem/iSaver local control can regain authority roughly one minute
        # after the last remote write. Refresh the last requested speed before
        # that watchdog expires whenever Pool Manager owns the pump.
        self.pompe_remote_keepalive_s = max(
            15,
            int(float(self.args.get("pompe_remote_keepalive_s", 30))),
        )
        super().initialize()

    def _maintain_remote_pump_authority(self, now=None):
        """Re-assert the last desired speed while local-panel handoff is disabled."""
        if not getattr(self, "entity_pompe_local_panel_assist", None):
            return False
        if self.arret_force_actif() or not self.pompe_est_on():
            return False
        if self.start_sequence_is_running() or self.stop_sequence_is_running():
            return False

        try:
            mode = (self.get_state(self.args["mode_de_fonctionnement"]) or "").strip()
        except Exception:
            return False

        # Température and Marche Forcée deliberately allow local-panel takeover.
        # Intelligent and Hors Gel keep Pool Manager as the single speed authority.
        if mode not in [TAB_MODE[1], TAB_MODE[2]]:
            return False

        try:
            if self.get_state(self.entity_pompe_local_panel_assist) == "on":
                return False
        except Exception:
            return False

        target = getattr(self, "derniere_vitesse_commande", None)
        if target is None:
            return False

        now = now or datetime.datetime.now()
        last_change = getattr(self, "last_changement_vitesse", None)
        if last_change is not None:
            elapsed = (now - last_change).total_seconds()
            if elapsed < self.pompe_remote_keepalive_s:
                return False

        self.set_pump_percentage(target, force=True)
        return True

    def check_etats_speciaux(self, kwargs):
        """Run normal housekeeping, then keep remote pump ownership alive."""
        super().check_etats_speciaux(kwargs)
        self._maintain_remote_pump_authority()
