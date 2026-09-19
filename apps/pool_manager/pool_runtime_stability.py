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

        # v0.8.1: do not require the optional local-control switch to be wired in
        # apps.yaml before protecting automatic pump ownership. Aquagem/iSaver
        # fan entity ids are auto-detected; other integrations can opt in
        # explicitly with pompe_remote_keepalive: true.
        configured = self.args.get("pompe_remote_keepalive")
        if configured is None:
            fan_entity = str(self.args.get("fan_variateur_pompe", "")).lower()
            self.pompe_remote_keepalive_enabled = (
                "aquagem" in fan_entity or "isaver" in fan_entity
            )
        else:
            self.pompe_remote_keepalive_enabled = (
                str(configured).strip().lower() in {"1", "true", "yes", "on"}
            )

        super().initialize()

    def _maintain_remote_pump_authority(self, now=None):
        """Re-assert automatic pump authority before an iSaver local handover.

        Intelligent and Hors Gel are Pool-Manager-owned modes. Aquagem's
        "Retour au contrôle local" is useful in manual-capable modes, but if it
        is left enabled here the physical panel can restore its remembered
        speed (often Max/2900 rpm) after the ~70 s quiet window. Rewriting the
        last requested speed before that handover expires keeps the automatic
        setpoint authoritative, even when the optional switch entity was not
        added to apps.yaml.
        """
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

        entity = getattr(self, "entity_pompe_local_panel_assist", None)
        if entity:
            # Self-heal the integration policy if the switch was restored or
            # toggled ON while an automatic mode is active.
            try:
                if self.get_state(entity) != "off":
                    self.sync_local_panel_policy(mode)
            except Exception:
                pass

        # If the integration switch is explicitly wired, automatic ownership is
        # always protected. Without it, protect known Aquagem/iSaver fans (or an
        # explicitly opted-in generic fan) by periodic remote keepalive writes.
        if not entity and not getattr(
            self,
            "pompe_remote_keepalive_enabled",
            False,
        ):
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
