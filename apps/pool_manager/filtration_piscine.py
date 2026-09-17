# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import datetime

import hassapi as hass

from pool_common import *
from pool_status import StatusMixin
from pool_heating import HeatingModeMixin
from pool_auto_gate import AutoModeGateMixin
from pool_safety import SafetyMixin
from pool_daylight import DaylightMixin
from pool_lifecycle import LifecycleMixin
from pool_devices import DevicesMixin
from pool_strategy import StrategyMixin
from pool_control import ControlMixin


class FiltrationPiscine(
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

    @staticmethod
    def _bool_arg(value, default=False):
        if value is None:
            return bool(default)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _clock(value, default):
        try:
            return datetime.time.fromisoformat(str(value or default))
        except (TypeError, ValueError):
            return datetime.time.fromisoformat(default)

    @staticmethod
    def _time_in_window(value, start, end):
        if start <= end:
            return start <= value < end
        return value >= start or value < end

    def initialize(self):
        """Initialize the production controller and optional HA policy inputs."""
        self.entity_derogation_chauffage = self.args.get("entity_derogation_chauffage")

        # Intelligent mode can run without periodic night mixing because the
        # persisted water reference is used while circulation is stopped.
        self.brassage_nuit_intelligent = self._bool_arg(
            self.args.get("brassage_nuit_intelligent", "false")
        )

        # Aquagem/iSaver local control regains authority roughly one minute after
        # the last remote write. When Pool Manager intentionally disables local
        # handoff, refresh the last desired speed before that watchdog expires.
        self.pompe_remote_keepalive_s = max(
            15,
            int(float(self.args.get("pompe_remote_keepalive_s", 30))),
        )

        # A predictive heating decision must not flip every 30 seconds around a
        # moving schedule boundary. Once a valid slot starts, keep it active at
        # least for this duration (or until the planned slot ends / target is met).
        self.chauffage_predictif_tempo_min_on_s = max(
            60,
            int(float(self.args.get("chauffage_predictif_tempo_min_on_s", 900))),
        )
        self._predictive_hold_until = None
        self._predictive_hold_target_c = None
        self._predictive_hold_kind = None

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

    # -------------------------- runtime stability guards -------------------------

    def is_night_brassage_slot(self, heure_actuelle):
        """Honor the Intelligent-mode night-mixing opt-out.

        Older code documented ``brassage_nuit_intelligent: false`` but the slot
        detector ignored it, so Intelligent mode still started 20 minutes of
        circulation at the beginning of every hour.
        """
        try:
            mode = (self.get_state(self.args["mode_de_fonctionnement"]) or "").strip()
        except Exception:
            mode = None

        if mode == TAB_MODE[1] and not getattr(
            self, "brassage_nuit_intelligent", False
        ):
            return False
        return super().is_night_brassage_slot(heure_actuelle)

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
        # Intelligent and Hors Gel are the modes where Pool Manager must remain
        # the single speed authority.
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
            if elapsed < getattr(self, "pompe_remote_keepalive_s", 30):
                return False

        self.set_pump_percentage(target, force=True)
        return True

    def check_etats_speciaux(self, kwargs):
        """Run normal safety housekeeping, then keep remote pump ownership alive."""
        super().check_etats_speciaux(kwargs)
        self._maintain_remote_pump_authority()

    def _clear_predictive_hold(self):
        self._predictive_hold_until = None
        self._predictive_hold_target_c = None
        self._predictive_hold_kind = None

    def _predictive_emergency_allowed(self, plan, now):
        """Allow emergency heating only inside intentional heating periods.

        The pure scheduler can otherwise fill missing capacity by inserting an
        emergency segment starting *now*. At 05:xx this bypassed the configured
        07:00 morning start and made the pump/PAC wake before the intended window.
        """
        active = (plan or {}).get("active_segment") or {}
        if active.get("kind") != "emergency":
            return True

        candidate = (plan or {}).get("candidate") or {}
        target_day = candidate.get("date")
        if target_day is None:
            return False

        current_time = now.time()
        if now.date() < target_day:
            start = self._clock(
                getattr(self, "chauffage_predictif_veille_debut", None),
                "12:00:00",
            )
            end = self._clock(
                getattr(self, "chauffage_predictif_veille_fin", None),
                "20:00:00",
            )
            return self._time_in_window(current_time, start, end)

        if now.date() == target_day:
            morning_start = self._clock(
                getattr(self, "chauffage_predictif_matin_debut", None),
                "07:00:00",
            )
            swim_datetime = candidate.get("swim_datetime")
            if isinstance(swim_datetime, datetime.datetime):
                return morning_start <= current_time < swim_datetime.time()
            return current_time >= morning_start

        return False

    def _suppress_out_of_window_predictive_emergency(self, plan, now):
        if not plan or self._predictive_emergency_allowed(plan, now):
            return plan

        active = plan.get("active_segment")
        schedule = [
            item
            for item in (plan.get("schedule") or [])
            if item is not active and item != active
        ]

        next_segment = None
        for item in schedule:
            start = item.get("start")
            if isinstance(start, datetime.datetime) and start > now:
                next_segment = item
                break

        plan["schedule"] = schedule
        plan["scheduled_hours"] = round(
            sum(float(item.get("hours") or 0.0) for item in schedule),
            2,
        )
        plan["active_segment"] = None
        plan["next_segment"] = next_segment
        plan["should_heat"] = False
        plan["heat_target_c"] = None

        if next_segment is not None:
            plan["reason"] = (
                "attente plage de chauffe autorisée; prochain créneau "
                f"{next_segment['start'].strftime('%d/%m %H:%M')}"
            )
        else:
            plan["reason"] = "attente plage de chauffe autorisée"
        return plan

    def _stabilize_predictive_plan(self, plan, kind, water, target, now=None):
        """Latch an active predictive slot so tiny recalculations cannot chatter."""
        now = now or datetime.datetime.now()
        plan = self._suppress_out_of_window_predictive_emergency(plan, now)
        margin = max(0.0, float(getattr(self, "chauffage_predictif_marge_arret_c", 0.2)))

        if getattr(self, "_predictive_hold_kind", None) not in [None, kind]:
            self._clear_predictive_hold()

        hold_until = getattr(self, "_predictive_hold_until", None)
        hold_target = getattr(self, "_predictive_hold_target_c", None)

        if (
            hold_until is not None
            and hold_target is not None
            and water is not None
            and float(water) >= float(hold_target) - margin
        ):
            self._clear_predictive_hold()
            hold_until = None
            hold_target = None

        if plan.get("should_heat"):
            desired_target = plan.get("heat_target_c")
            if desired_target is None:
                desired_target = target

            active = plan.get("active_segment") or {}
            segment_end = active.get("end")
            minimum_until = now + datetime.timedelta(
                seconds=getattr(self, "chauffage_predictif_tempo_min_on_s", 900)
            )

            if isinstance(segment_end, datetime.datetime) and segment_end > now:
                # Never extend a scheduled slot past its configured end.
                desired_until = segment_end
            else:
                desired_until = minimum_until

            if hold_until is None or desired_until > hold_until:
                hold_until = desired_until

            self._predictive_hold_until = hold_until
            self._predictive_hold_target_c = desired_target
            self._predictive_hold_kind = kind
            return plan

        if hold_until is not None and now < hold_until:
            plan["should_heat"] = True
            plan["heat_target_c"] = hold_target if hold_target is not None else target
            plan["reason"] = (
                "créneau prédictif stabilisé jusqu’à "
                f"{hold_until.strftime('%H:%M')}"
            )
            return plan

        self._clear_predictive_hold()
        return plan

    def _build_runtime_predictive_plan(self, kind, water, target, forecast):
        plan, rate = super()._build_runtime_predictive_plan(
            kind,
            water,
            target,
            forecast,
        )
        plan = self._stabilize_predictive_plan(
            plan,
            kind,
            water,
            target,
        )
        return plan, rate

    def change_chauffage_mode(self, entity, attribute, old, new, kwargs):
        self._clear_predictive_hold()
        return super().change_chauffage_mode(entity, attribute, old, new, kwargs)

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
