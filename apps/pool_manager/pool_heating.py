# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import datetime
from datetime import timedelta

from pool_common import TAB_MODE


CHAUFFAGE_DESACTIVE = "Désactivé"
CHAUFFAGE_AUTO = "Automatique"
CHAUFFAGE_PREMIERE_CHAUFFE = "Première chauffe • Smart"
CHAUFFAGE_FIN_SAISON = "Fin de saison • Smart"

TURBO_DURATIONS_H = {
    "Turbo • 1 h": 1,
    "Turbo • 2 h": 2,
    "Turbo • 3 h": 3,
    "Turbo • 6 h": 6,
    "Turbo • 12 h": 12,
    "Turbo • 1 jour": 24,
    "Turbo • 2 jours": 48,
    "Turbo • 3 jours": 72,
}

CHAUFFAGE_MODES = {
    CHAUFFAGE_DESACTIVE,
    CHAUFFAGE_AUTO,
    CHAUFFAGE_PREMIERE_CHAUFFE,
    CHAUFFAGE_FIN_SAISON,
    *TURBO_DURATIONS_H.keys(),
}


def chauffage_mode_kind(value):
    """Return a stable policy kind for one Home Assistant selector value."""
    value = str(value or "").strip()
    if value in TURBO_DURATIONS_H:
        return "turbo"
    if value == CHAUFFAGE_PREMIERE_CHAUFFE:
        return "first_heat"
    if value == CHAUFFAGE_FIN_SAISON:
        return "end_season"
    if value == CHAUFFAGE_DESACTIVE:
        return "disabled"
    if value == CHAUFFAGE_AUTO:
        return "auto"
    return "unknown"


def turbo_duration_seconds(value):
    hours = TURBO_DURATIONS_H.get(str(value or "").strip())
    return None if hours is None else int(hours * 3600)


def premiere_chauffe_terminee(temperature_eau, consigne, marge_c=0.3):
    """Return True once the measured water has reached the PAC setpoint."""
    if temperature_eau is None or consigne is None:
        return False
    return float(temperature_eau) >= float(consigne) - max(0.0, float(marge_c))


class HeatingModeMixin:
    """Single-selector heating policy layered above the normal PAC automation.

    `entity_chauffage` is optional. Without it, Pool Manager keeps the exact
    pre-v0.4.4 PAC behavior. When configured, it can own normal automatic PAC,
    first heat, end-of-season Smart maintenance and temporary Turbo modes.
    """

    def initialize(self):
        self.entity_chauffage = self.args.get("entity_chauffage")
        self.entity_chauffage_timer = self.args.get("entity_chauffage_timer")
        self.entity_chauffage_precedent = self.args.get("entity_chauffage_precedent")

        self.chauffage_preset_auto = self.args.get("chauffage_preset_auto", "Smart")
        self.chauffage_preset_smart = self.args.get("chauffage_preset_smart", "Smart")
        self.chauffage_preset_turbo = self.args.get("chauffage_preset_turbo", "Turbo")
        self.chauffage_premiere_chauffe_marge_c = float(
            self.args.get("chauffage_premiere_chauffe_marge_c", 0.3)
        )

        self.chauffage_mode_precedent = CHAUFFAGE_AUTO
        self.handle_chauffage_start = None
        self.chauffage_start_deadline = None
        self.chauffage_start_preset = None
        self.chauffage_start_label = None
        self.handle_turbo_fallback = None

        super().initialize()

        if self.entity_chauffage:
            self.listen_state(self.change_chauffage_mode, self.entity_chauffage)

        if self.entity_chauffage_timer:
            try:
                self.listen_event(
                    self.chauffage_timer_finished,
                    "timer.finished",
                    entity_id=self.entity_chauffage_timer,
                )
            except Exception:
                pass

    def chauffage_mode(self):
        if not self.entity_chauffage:
            return None
        try:
            value = self.get_state(self.entity_chauffage)
            return None if self._invalid(value) else str(value).strip()
        except Exception:
            return None

    def chauffage_mode_explicite(self):
        return chauffage_mode_kind(self.chauffage_mode()) in {
            "first_heat",
            "end_season",
            "turbo",
        }

    def _chauffage_pool_mode_autorise(self):
        mode = self._mode()
        return (
            mode in [TAB_MODE[0], TAB_MODE[1], TAB_MODE[3]]
            and not self.arret_force_actif()
            and not getattr(self, "hors_gel_continu_active", False)
        )

    def _set_chauffage_selector(self, option):
        if not self.entity_chauffage or option not in CHAUFFAGE_MODES:
            return False
        try:
            self.call_service(
                "input_select/select_option",
                entity_id=self.entity_chauffage,
                option=option,
            )
            return True
        except Exception as exc:
            self._fault("chauffage_select", f"échec changement mode chauffage: {exc}")
            return False

    def _set_chauffage_precedent(self, mode):
        mode = str(mode or "").strip()
        if chauffage_mode_kind(mode) == "unknown" or chauffage_mode_kind(mode) == "turbo":
            return
        self.chauffage_mode_precedent = mode
        if not self.entity_chauffage_precedent:
            return
        try:
            self.call_service(
                "input_text/set_value",
                entity_id=self.entity_chauffage_precedent,
                value=mode,
            )
        except Exception:
            pass

    def _get_chauffage_precedent(self):
        if self.entity_chauffage_precedent:
            try:
                value = self.get_state(self.entity_chauffage_precedent)
                value = None if self._invalid(value) else str(value).strip()
                if value in CHAUFFAGE_MODES and chauffage_mode_kind(value) != "turbo":
                    return value
            except Exception:
                pass
        if self.chauffage_mode_precedent in CHAUFFAGE_MODES:
            if chauffage_mode_kind(self.chauffage_mode_precedent) != "turbo":
                return self.chauffage_mode_precedent
        return CHAUFFAGE_AUTO

    def _timer_duration_text(self, seconds):
        hours = max(0, int(seconds)) // 3600
        minutes = (max(0, int(seconds)) % 3600) // 60
        secs = max(0, int(seconds)) % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def _cancel_turbo_timer(self):
        if self.handle_turbo_fallback is not None:
            try:
                self.cancel_timer(self.handle_turbo_fallback)
            except Exception:
                pass
            self.handle_turbo_fallback = None
        if self.entity_chauffage_timer:
            try:
                self.call_service("timer/cancel", entity_id=self.entity_chauffage_timer)
            except Exception:
                pass

    def _start_turbo_timer(self, seconds):
        self._cancel_turbo_timer()
        if self.entity_chauffage_timer:
            try:
                self.call_service(
                    "timer/start",
                    entity_id=self.entity_chauffage_timer,
                    duration=self._timer_duration_text(seconds),
                )
                return
            except Exception as exc:
                self._fault("chauffage_timer", f"timer chauffage indisponible: {exc}")
        self.handle_turbo_fallback = self.run_in(self._turbo_fallback_finished, seconds)

    def _turbo_fallback_finished(self, kwargs):
        self.handle_turbo_fallback = None
        self._restore_after_turbo()

    def chauffage_timer_finished(self, event_name, data, kwargs):
        if data and data.get("entity_id") not in [None, self.entity_chauffage_timer]:
            return
        if chauffage_mode_kind(self.chauffage_mode()) == "turbo":
            self._restore_after_turbo()

    def _restore_after_turbo(self):
        previous = self._get_chauffage_precedent()
        self._set_chauffage_selector(previous)
        self.log(f"PAC Turbo terminé -> chauffage {previous}", log="piscine_log")

    def change_chauffage_mode(self, entity, attribute, old, new, kwargs):
        old_mode = str(old or "").strip()
        new_mode = str(new or "").strip()
        old_kind = chauffage_mode_kind(old_mode)
        new_kind = chauffage_mode_kind(new_mode)

        if new_kind == "turbo":
            if old_kind != "turbo":
                self._set_chauffage_precedent(old_mode)
            seconds = turbo_duration_seconds(new_mode)
            if seconds:
                self._start_turbo_timer(seconds)
        elif old_kind == "turbo":
            self._cancel_turbo_timer()

        self._cancel_chauffage_start()
        self.safety_tick({})

    def _set_pac_preset(self, preset):
        if not preset or not self._available(self.entity_pac_climate):
            return False
        try:
            current = self.get_state(self.entity_pac_climate, attribute="preset_mode")
            if str(current or "").strip().casefold() == str(preset).strip().casefold():
                return True
        except Exception:
            pass
        try:
            self.call_service(
                "climate/set_preset_mode",
                entity_id=self.entity_pac_climate,
                preset_mode=preset,
            )
            return True
        except Exception as exc:
            self._fault("pac_preset", f"échec preset PAC {preset}: {exc}")
            return False

    def _set_pac_heat(self, preset, label):
        if not self._available(self.entity_pac_climate):
            self._fault("pac_climate", f"PAC indisponible ({label})")
            return False
        try:
            if self._pac_state() != "heat":
                self.call_service(
                    "climate/set_hvac_mode",
                    entity_id=self.entity_pac_climate,
                    hvac_mode="heat",
                )
            self._set_pac_preset(preset)
            self._recover("pac_climate")
            return True
        except Exception as exc:
            self._fault("pac_start", f"échec démarrage PAC {label}: {exc}")
            return False

    def _cancel_chauffage_start(self):
        if self.handle_chauffage_start is not None:
            try:
                self.cancel_timer(self.handle_chauffage_start)
            except Exception:
                pass
        self.handle_chauffage_start = None
        self.chauffage_start_deadline = None
        self.chauffage_start_preset = None
        self.chauffage_start_label = None

    def _request_chauffage_start(self, preset, label):
        if not self._chauffage_pool_mode_autorise():
            return
        if self._pump_flow_ok():
            self._cancel_chauffage_start()
            if self._set_pac_heat(preset, label):
                self.log(f"PAC {label}: circulation confirmée -> Heat/{preset}", log="piscine_log")
            return

        if self.chauffage_start_deadline is None:
            self.chauffage_start_deadline = datetime.datetime.now() + timedelta(
                seconds=self.pac_auto_timeout_demarrage_s
            )
            self.chauffage_start_preset = preset
            self.chauffage_start_label = label

        self._ensure_pac_flow()
        if self.handle_chauffage_start is None:
            self.handle_chauffage_start = self.run_in(self._check_chauffage_start, 2)

    def _check_chauffage_start(self, kwargs):
        self.handle_chauffage_start = None
        if not self.chauffage_mode_explicite() or not self._chauffage_pool_mode_autorise():
            self._cancel_chauffage_start()
            return
        if self.chauffage_start_deadline is None:
            return
        if datetime.datetime.now() >= self.chauffage_start_deadline:
            self._fault("pac_start", "PAC non démarrée: circulation pompe non confirmée")
            self._cancel_chauffage_start()
            return
        if self._pump_flow_ok():
            preset = self.chauffage_start_preset or self.chauffage_preset_smart
            label = self.chauffage_start_label or "chauffage"
            self._cancel_chauffage_start()
            if self._set_pac_heat(preset, label):
                self.log(f"PAC {label}: circulation confirmée -> Heat/{preset}", log="piscine_log")
            self.traitement({})
            return
        self._ensure_pac_flow()
        self.handle_chauffage_start = self.run_in(self._check_chauffage_start, 2)

    def _pac_target_temperature(self):
        try:
            value = self.get_state(self.entity_pac_climate, attribute="temperature")
            return None if self._invalid(value) else float(value)
        except Exception:
            return None

    def _premiere_chauffe_temperature(self):
        water = self._raw_float(self.args.get("temperature_eau"))
        if water is not None:
            return water
        try:
            value = self.get_state(self.entity_pac_climate, attribute="current_temperature")
            return None if self._invalid(value) else float(value)
        except Exception:
            return None

    def _manage_premiere_chauffe(self):
        water = self._premiere_chauffe_temperature()
        target = self._pac_target_temperature()
        if premiere_chauffe_terminee(water, target, self.chauffage_premiere_chauffe_marge_c):
            self._cancel_chauffage_start()
            self._set_chauffage_selector(CHAUFFAGE_AUTO)
            self.log(
                f"Première chauffe terminée ({water:.1f}/{target:.1f} °C) -> Automatique",
                log="piscine_log",
            )
            return
        if water is None or target is None:
            self._fault("chauffage_temperature", "température/consigne PAC indisponible pour fin de première chauffe")
        else:
            self._recover("chauffage_temperature")
        self._request_chauffage_start(self.chauffage_preset_smart, "première chauffe")

    def _manage_pac_auto(self):
        # No new selector configured: strict backwards compatibility.
        if not self.entity_chauffage:
            return super()._manage_pac_auto()

        mode_chauffage = self.chauffage_mode()
        kind = chauffage_mode_kind(mode_chauffage)

        # Unknown/unavailable selector is fail-safe: do not create a new heat demand.
        if kind == "unknown":
            self._cancel_chauffage_start()
            self._fault("chauffage_mode", "mode chauffage indisponible/inconnu")
            return
        self._recover("chauffage_mode")

        pool_mode = self._mode()
        if pool_mode in [TAB_MODE[2], TAB_MODE[4]] or self.arret_force_actif():
            self._cancel_chauffage_start()
            self._pac_off("hors gel / arrêt forcé", post=(pool_mode != TAB_MODE[4]))
            return

        if kind == "disabled":
            self._cancel_chauffage_start()
            self._cancel_pac_start()
            self._pac_off("chauffage désactivé")
            return

        if kind == "auto":
            self._cancel_chauffage_start()
            result = super()._manage_pac_auto()
            if self._pac_state() == "heat":
                self._set_pac_preset(self.chauffage_preset_auto)
            return result

        # Explicit modes intentionally bypass the seasonal weather/window gate.
        self._cancel_pac_start()
        if kind == "first_heat":
            return self._manage_premiere_chauffe()
        if kind == "end_season":
            return self._request_chauffage_start(self.chauffage_preset_smart, "fin de saison")
        if kind == "turbo":
            return self._request_chauffage_start(self.chauffage_preset_turbo, "Turbo")

    def pac_besoin_chauffe(self):
        if self.entity_chauffage and self.chauffage_mode_explicite():
            return True
        return super().pac_besoin_chauffe()
