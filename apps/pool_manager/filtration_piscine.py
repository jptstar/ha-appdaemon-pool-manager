# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import hassapi as hass

from pool_common import *
from pool_status import StatusMixin
from pool_safety import SafetyMixin
from pool_daylight import DaylightMixin
from pool_lifecycle import LifecycleMixin
from pool_devices import DevicesMixin
from pool_strategy import StrategyMixin
from pool_control import ControlMixin


class FiltrationPiscine(
    StatusMixin,
    SafetyMixin,
    DaylightMixin,
    LifecycleMixin,
    DevicesMixin,
    StrategyMixin,
    ControlMixin,
    hass.Hass,
):
    def initialize(self):
        """Initialize the production controller and optional HA policy inputs."""
        self.entity_derogation_chauffage = self.args.get("entity_derogation_chauffage")

        # Cold-water chlorination protection. The lock starts conservative so an
        # AppDaemon restart around the threshold cannot briefly enable the cell.
        self.protection_electrolyse_froid = str(
            self.args.get("protection_electrolyse_froid", "true")
        ).lower() == "true"
        self.electrolyse_temperature_arret_c = float(
            self.args.get("electrolyse_temperature_arret_c", 15.0)
        )
        self.electrolyse_temperature_reprise_c = max(
            self.electrolyse_temperature_arret_c,
            float(self.args.get("electrolyse_temperature_reprise_c", 16.0)),
        )
        self.electrolyse_basse_temp_bloquee = True

        super().initialize()

        if self.entity_derogation_chauffage:
            self.listen_state(
                self.change_derogation_chauffage,
                self.entity_derogation_chauffage,
            )

        # The normal water-temperature listener intentionally ignores
        # `unavailable`. This dedicated safety listener must not: an unavailable
        # water probe immediately inhibits electrolysis.
        temperature_eau = self.args.get("temperature_eau")
        if temperature_eau:
            self.listen_state(
                self.change_temperature_electrolyse,
                temperature_eau,
            )

        self.maj_electrolyseur()

    def change_derogation_chauffage(self, entity, attribute, old, new, kwargs):
        """Re-evaluate pump demand immediately when heating override changes."""
        self.traitement(kwargs)

    def change_temperature_electrolyse(self, entity, attribute, old, new, kwargs):
        """Apply the cold-water cell lock immediately on any probe change."""
        self.maj_electrolyseur()

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

    def temperature_eau_brute_electrolyse(self):
        """Return the physical water-probe value without fail-safe substitution."""
        entity = self.args.get("temperature_eau")
        if not entity:
            return None
        try:
            value = self.get_state(entity)
            if value is None or str(value).strip().lower() in {
                "unknown",
                "unavailable",
                "none",
                "",
            }:
                return None
            return float(value)
        except Exception:
            return None

    def electrolyse_temperature_autorisee(self):
        """Protect the chlorinator cell with low-temperature hysteresis.

        <= stop threshold: lock electrolysis.
        >= resume threshold: release the lock.
        Between thresholds: retain the previous state.
        Missing probe: lock electrolysis immediately (fail-safe).
        """
        if not self.protection_electrolyse_froid:
            return True

        temperature = self.temperature_eau_brute_electrolyse()
        if temperature is None:
            self.electrolyse_basse_temp_bloquee = True
            if hasattr(self, "_fault"):
                self._fault(
                    "electrolyse_temperature",
                    "température eau indisponible, électrolyse interdite",
                )
            return False

        if temperature <= self.electrolyse_temperature_arret_c:
            self.electrolyse_basse_temp_bloquee = True
        elif temperature >= self.electrolyse_temperature_reprise_c:
            self.electrolyse_basse_temp_bloquee = False

        if self.electrolyse_basse_temp_bloquee:
            if hasattr(self, "_fault"):
                self._fault(
                    "electrolyse_temperature",
                    f"eau {temperature:.1f} °C, électrolyse interdite sous "
                    f"{self.electrolyse_temperature_reprise_c:.1f} °C",
                )
            return False

        if hasattr(self, "_recover"):
            self._recover(
                "electrolyse_temperature",
                f"température eau {temperature:.1f} °C compatible",
            )
        return True

    def electrolyseur_autorise(self):
        """Require both normal hydraulic safety and valid water temperature."""
        if not self.electrolyse_temperature_autorisee():
            return False
        return super().electrolyseur_autorise()

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