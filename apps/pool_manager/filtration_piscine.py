# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import hassapi as hass

from pool_common import *
from pool_runtime_stability import RuntimeStabilityMixin
from pool_status import StatusMixin
from pool_heating import HeatingModeMixin
from pool_auto_gate import AutoModeGateMixin
from pool_safety import SafetyMixin
from pool_daylight import DaylightMixin
from pool_lifecycle import LifecycleMixin
from pool_devices import DevicesMixin
from pool_strategy import StrategyMixin
from pool_control import ControlMixin
from pool_journal import journal_category, journal_french, publish_journal_event


INITIALIZATION_STAGES = (
    "_initialize_runtime_stability",
    "_initialize_heating",
    "_initialize_auto_gate",
    "_initialize_safety",
    "_initialize_daylight",
    "_initialize_lifecycle",
)


class FiltrationPiscine(
    RuntimeStabilityMixin,
    StatusMixin,
    HeatingModeMixin,
    AutoModeGateMixin,
    SafetyMixin,
    DaylightMixin,
    LifecycleMixin,
    DevicesMixin,
    StrategyMixin,
    ControlMixin,
    hass.Hass,
):
    def log(self, msg, *args, **kwargs):
        """Mirror the dedicated piscine_log stream into a Home Assistant sensor."""
        result = super().log(msg, *args, **kwargs)

        if kwargs.get("log") == "piscine_log":
            try:
                self._publish_pool_manager_log(msg)
            except Exception:
                # Logging must never interfere with pool control.
                pass
        return result

    @staticmethod
    def _pool_log_category(message):
        return journal_category(message)

    @staticmethod
    def _pool_log_french(message):
        """Translate internal controller tokens before they reach the HA journal."""
        return journal_french(message)

    def _publish_pool_manager_log(self, message):
        return publish_journal_event(self, message)

    def call_service(self, service, **kwargs):
        """Keep legacy response calls compatible with AppDaemon 4.5+.

        Pool Manager v0.5/v0.6 used ``return_result=True`` while AppDaemon 4.5
        exposes Home Assistant service responses through ``return_response``.
        Translate the old internal flag here so response-returning services such
        as ``weather/get_forecasts`` are requested correctly without leaking an
        unsupported ``return_result`` field into Home Assistant service data.
        """
        if kwargs.pop("return_result", False):
            kwargs["return_response"] = True
        return super().call_service(service, **kwargs)

    def initialize(self):
        """Initialize the production controller and optional HA policy inputs."""
        self.entity_derogation_chauffage = self.args.get("entity_derogation_chauffage")
        self.entity_pool_manager_log = self.args.get(
            "entity_pool_manager_log",
            "sensor.pool_manager_log",
        )
        self.pool_manager_log_history_size = int(
            float(self.args.get("pool_manager_log_history_size", 50))
        )
        self._pool_manager_log_history = []
        try:
            previous_history = self.get_state(
                self.entity_pool_manager_log,
                attribute="history",
            )
            if isinstance(previous_history, list):
                self._pool_manager_log_history = previous_history[
                    -self.pool_manager_log_history_size :
                ]
        except Exception:
            pass

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

        # Composition root: domain initialization order is explicit.  No
        # safety or control priority depends on Python's cooperative MRO.
        for stage in INITIALIZATION_STAGES:
            getattr(self, stage)()

        self.log("Pool Manager initialisé", log="piscine_log")

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

        Home Assistant remains responsible for the legacy override timer and PAC
        operating mode when `entity_derogation_chauffage` is configured. The new
        single-selector heating policy is layered below this compatibility hook.
        """
        if self.derogation_chauffage_active():
            return True
        heating_request = self._pac_circulation_chauffage_requise()
        if heating_request is not None:
            return bool(heating_request)
        if self._pac_circulation_securite_requise():
            return True
        return self._pac_besoin_chauffe_physique()

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
        if not self._electrolyse_securite_autorisee():
            return False
        return self._electrolyse_hydrauliquement_autorisee()

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
