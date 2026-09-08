# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import hassapi as hass

from pool_common import *
from pool_status import StatusMixin
from pool_lifecycle import LifecycleMixin
from pool_devices import DevicesMixin
from pool_strategy import StrategyMixin
from pool_control import ControlMixin


class FiltrationPiscine(
    StatusMixin,
    LifecycleMixin,
    DevicesMixin,
    StrategyMixin,
    ControlMixin,
    hass.Hass,
):
    def initialize(self):
        """Initialize the production controller and optional HA policy inputs."""
        self.entity_derogation_chauffage = self.args.get("entity_derogation_chauffage")
        super().initialize()

        if self.entity_derogation_chauffage:
            self.listen_state(
                self.change_derogation_chauffage,
                self.entity_derogation_chauffage,
            )

    def change_derogation_chauffage(self, entity, attribute, old, new, kwargs):
        """Re-evaluate pump demand immediately when heating override changes."""
        self.traitement(kwargs)

    def derogation_chauffage_active(self):
        """Return True when Home Assistant requests temporary pool heating."""
        if not self.entity_derogation_chauffage:
            return False
        try:
            return self.get_state(self.entity_derogation_chauffage) == "on"
        except Exception:
            return False

    def pac_besoin_chauffe(self):
        """Treat an explicit HA heating override as a heat-pump flow request.

        Home Assistant remains responsible for the override timer and PAC
        operating mode. Pool Manager only converts that policy signal into a
        circulation requirement. With no configured override entity, behavior
        remains identical to the previous release.
        """
        if self.derogation_chauffage_active():
            return True
        return super().pac_besoin_chauffe()

    def set_consigne_electrolyseur(self, valeur, force=False):
        """Set chlorinator production without blocking critical pump control.

        Avoids writing the same value again. During a forced stop, the
        chlorinator stop request is still sent first, but the wait for Home
        Assistant is bounded so a non-responsive chlorinator cannot delay the
        pump shutdown indefinitely.
        """
        if not self.entity_consigne_electrolyseur:
            return

        try:
            valeur = max(0.0, min(100.0, float(valeur)))
        except Exception:
            valeur = 0.0

        # Prefer the real HA state over the in-memory cache. This is especially
        # useful after an AppDaemon/HACS reload, where last_consigne is empty.
        try:
            valeur_actuelle = self.get_state(self.entity_consigne_electrolyseur)
            if valeur_actuelle not in [None, "unknown", "unavailable", ""]:
                if abs(float(valeur_actuelle) - valeur) < 0.01:
                    self.last_consigne_electrolyseur = valeur
                    return
        except Exception:
            pass

        if not force and self.last_consigne_electrolyseur is not None:
            if abs(float(self.last_consigne_electrolyseur) - valeur) < 0.01:
                return

        try:
            if force:
                timeout_s = max(0.5, float(self.args.get("timeout_electrolyseur_force_s", 3.0)))
                self.call_service(
                    "number/set_value",
                    entity_id=self.entity_consigne_electrolyseur,
                    value=valeur,
                    timeout=timeout_s,
                )
            else:
                self.call_service(
                    "number/set_value",
                    entity_id=self.entity_consigne_electrolyseur,
                    value=valeur,
                )
            self.last_consigne_electrolyseur = valeur
        except Exception as e:
            self.log(f"⚠️ Erreur consigne électrolyseur : {e}", log="piscine_log")
