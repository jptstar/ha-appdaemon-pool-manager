# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

import datetime
from datetime import timedelta

from pool_common import TAB_MODE
from pool_predictive_runtime import PredictiveHeatingSupport
from pool_season import decide_season


CHAUFFAGE_DESACTIVE = "Désactivé"
CHAUFFAGE_AUTO = "Automatique"
CHAUFFAGE_DEBUT_SAISON = "Début de saison • Smart"
# Compatibility alias accepted when an existing HA selector still uses v0.5 wording.
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
    CHAUFFAGE_DEBUT_SAISON,
    CHAUFFAGE_PREMIERE_CHAUFFE,
    CHAUFFAGE_FIN_SAISON,
    *TURBO_DURATIONS_H.keys(),
}


def chauffage_mode_kind(value):
    """Return a stable policy kind for one Home Assistant selector value."""
    value = str(value or "").strip()
    if value in TURBO_DURATIONS_H:
        return "turbo"
    if value in {CHAUFFAGE_DEBUT_SAISON, CHAUFFAGE_PREMIERE_CHAUFFE}:
        return "season_start"
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


def seasonal_cutoff_reached(now, month_day=(10, 10)):
    """Local calendar cutoff; disabled selector persists through New Year."""
    return (now.month, now.day) >= month_day


class HeatingModeMixin(PredictiveHeatingSupport):
    """Single-selector heating policy layered above the normal PAC automation.

    `entity_chauffage` is optional. Without it, Pool Manager keeps the exact
    pre-v0.4.4 PAC behavior. When configured, it can own normal automatic PAC,
    season start, predictive automatic/end-of-season policy and Turbo modes.
    """

    def _initialize_heating(self):
        self.entity_chauffage = self.args.get("entity_chauffage")
        self.entity_chauffage_timer = self.args.get("entity_chauffage_timer")
        self.entity_chauffage_precedent = self.args.get("entity_chauffage_precedent")
        self.chauffage_arret_saisonnier = str(
            self.args.get("chauffage_arret_saisonnier", True)
        ).lower() in {"true", "1", "yes", "on"}
        cutoff = str(self.args.get("chauffage_date_arret", "10-10"))
        parsed = datetime.datetime.strptime("2001-" + cutoff, "%Y-%m-%d")
        self.chauffage_date_arret = (parsed.month, parsed.day)
        self._seasonal_turbo_override_until = None

        self.chauffage_preset_auto = self.args.get("chauffage_preset_auto", "Smart")
        self.chauffage_preset_smart = self.args.get("chauffage_preset_smart", "Smart")
        self.chauffage_preset_turbo = self.args.get("chauffage_preset_turbo", "Turbo")
        self.chauffage_premiere_chauffe_marge_c = float(
            self.args.get(
                "chauffage_debut_saison_marge_c",
                self.args.get("chauffage_premiere_chauffe_marge_c", 0.3),
            )
        )

        # v0.6 common predictive engine; v0.5 fin_saison_* keys remain aliases.
        self._initialize_predictive_heating()
        self._initialize_season_heating()

        self.chauffage_mode_precedent = CHAUFFAGE_AUTO
        self.handle_chauffage_start = None
        self.chauffage_start_deadline = None
        self.chauffage_start_preset = None
        self.chauffage_start_label = None
        self.handle_turbo_fallback = None
        self.chauffage_turbo_ends_at = None

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


    # ------------------- heating-only hybrid end-of-season -------------------

    def _initialize_season_heating(self):
        # Explicit opt-in keeps all existing users' heating behavior unchanged.
        self.chauffage_fin_saison_hybride = self._bool_value(
            self.args.get("chauffage_fin_saison_hybride", False)
        )
        self.entity_chauffage_saison_terminee = self.args.get(
            "entity_chauffage_saison_terminee"
        )
        self.entity_chauffage_froid_depuis = self.args.get(
            "entity_chauffage_froid_depuis"
        )
        self.entity_chauffage_derogation_jusqua = self.args.get(
            "entity_chauffage_derogation_jusqua"
        )
        self.entity_chauffage_derogation_bouton = self.args.get(
            "entity_chauffage_derogation_bouton"
        )
        cutoff = str(self.args.get("chauffage_fin_saison_date", "10-10")).strip()
        try:
            month, day = (int(part) for part in cutoff.split("-"))
            datetime.date(2026, month, day)
        except (TypeError, ValueError):
            month, day = 10, 10
        self.chauffage_fin_saison_cutoff = (month, day)
        self.chauffage_fin_saison_seuil_7j_c = float(
            self.args.get("chauffage_fin_saison_seuil_7j_c", 16.0)
        )
        self.chauffage_fin_saison_confirmation_h = max(
            1.0, float(self.args.get("chauffage_fin_saison_confirmation_h", 72))
        )
        self.chauffage_fin_saison_derogation_h = max(
            1.0, float(self.args.get("chauffage_fin_saison_derogation_h", 24))
        )
        self._season_latched = False
        self._season_reset_pending = False
        self._season_cold_since_cache = None
        self._season_last_decision = None
        self._season_last_recommendation = False
        if self.chauffage_fin_saison_hybride:
            if not self.entity_chauffage_saison_terminee:
                self._fault(
                    "season_persistence",
                    "Fin de saison hybride: helper de verrouillage manquant"
                )
            if self.entity_chauffage_derogation_bouton:
                self.listen_state(
                    self._season_override_button_pressed,
                    self.entity_chauffage_derogation_bouton,
                )

    def _season_now(self):
        return datetime.datetime.now()

    def _season_closed_helper(self):
        entity = getattr(self, "entity_chauffage_saison_terminee", None)
        if not entity:
            return False
        try:
            return str(self.get_state(entity)).strip().lower() == "on"
        except Exception:
            return False

    def _season_set_closed(self, closed):
        self._season_latched = bool(closed)
        if not closed:
            # HA state writes can be asynchronous. Do not re-latch from an
            # old on-state in the same safety tick as explicit reopening.
            self._season_reset_pending = True
        else:
            self._season_reset_pending = False
        entity = getattr(self, "entity_chauffage_saison_terminee", None)
        if not entity:
            return
        try:
            actual = self._season_closed_helper()
            if actual != closed:
                self.call_service(
                    "input_boolean/turn_on" if closed else "input_boolean/turn_off",
                    entity_id=entity,
                )
        except Exception as exc:
            self._fault("season_persistence", f"Verrou saisonnier non sauvegardé: {exc}")

    def _season_locked(self, kind=None):
        if not getattr(self, "chauffage_fin_saison_hybride", False):
            return False
        kind = kind or chauffage_mode_kind(self.chauffage_mode())
        saved_closed = self._season_closed_helper()
        if getattr(self, "_season_reset_pending", False):
            if not saved_closed:
                self._season_reset_pending = False
            saved_closed = False
        if getattr(self, "_season_latched", False) or saved_closed:
            self._season_latched = True
            return True
        month, day = self.chauffage_fin_saison_cutoff
        now = self._season_now()
        if kind == "end_season" and now.date() >= datetime.date(now.year, month, day):
            self._season_set_closed(True)
            return True
        return False

    def _season_heat_allowed(self, kind=None):
        if not getattr(self, "chauffage_fin_saison_hybride", False):
            return True
        kind = kind or chauffage_mode_kind(self.chauffage_mode())
        if self._season_locked(kind):
            return False
        if kind == "end_season":
            decision = getattr(self, "_season_last_decision", None)
            return bool(decision and decision.heat_allowed)
        return True

    def _season_cold_since(self, now):
        entity = getattr(self, "entity_chauffage_froid_depuis", None)
        cold_since = getattr(self, "_season_cold_since_cache", None)
        if entity:
            try:
                saved = self._parse_datetime(self.get_state(entity))
                if saved and saved.year >= 2020:
                    cold_since = saved.replace(tzinfo=None)
            except Exception:
                pass

        sensor = self.args.get("entity_temperature_exterieure_moyenne_7j")
        average = self._raw_float(sensor) if sensor else None
        if average is None:
            # Interrupt continuity on a sensor outage. We cannot assert
            # 72 consecutive valid hours across missing measurements.
            if cold_since is not None:
                self._season_cold_since_cache = None
                if entity:
                    try:
                        self.call_service(
                            "input_datetime/set_datetime",
                            entity_id=entity,
                            datetime="2000-01-01 00:00:00",
                        )
                    except Exception:
                        pass
            return None, None

        is_cold = average < self.chauffage_fin_saison_seuil_7j_c
        new_since = (cold_since or now) if is_cold else None
        if new_since != cold_since:
            self._season_cold_since_cache = new_since
            if entity:
                try:
                    self.call_service(
                        "input_datetime/set_datetime",
                        entity_id=entity,
                        datetime=(
                            new_since.strftime("%Y-%m-%d %H:%M:%S")
                            if new_since else "2000-01-01 00:00:00"
                        ),
                    )
                except Exception as exc:
                    self._fault(
                        "season_cold_helper", f"Historique de froid non sauvegardé: {exc}"
                    )
        return average, new_since

    def _season_override_until(self):
        entity = getattr(self, "entity_chauffage_derogation_jusqua", None)
        if not entity:
            return None
        try:
            parsed = self._parse_datetime(self.get_state(entity))
            return parsed.replace(tzinfo=None) if parsed else None
        except Exception:
            return None

    def _season_override_button_pressed(self, entity, attribute, old, new, kwargs):
        if not getattr(self, "chauffage_fin_saison_hybride", False):
            return
        now = self._season_now()
        kind = chauffage_mode_kind(self.chauffage_mode())
        if kind != "end_season":
            return
        if self._season_locked(kind):
            self.log(
                "Dérogation refusée : fermeture saisonnière définitive",
                log="piscine_log",
            )
            return
        target = getattr(self, "entity_chauffage_derogation_jusqua", None)
        if not target:
            return
        until = now + timedelta(hours=self.chauffage_fin_saison_derogation_h)
        try:
            self.call_service(
                "input_datetime/set_datetime",
                entity_id=target,
                datetime=until.strftime("%Y-%m-%d %H:%M:%S"),
            )
            self._season_last_decision = None
            self.safety_tick({})
        except Exception as exc:
            self._fault("season_override", f"Dérogation indisponible: {exc}")

    def _season_policy_for_plan(self, plan, forecast):
        now = self._season_now()
        mean, cold_since = self._season_cold_since(now)
        forecast_age = self._predictive_forecast_age_s()
        valid = bool(forecast) and (
            forecast_age is not None
            and forecast_age <= self.chauffage_predictif_prevision_max_age_s
        )
        scores = []
        for item in forecast or []:
            try:
                scores.append(float(item.get("usage_score", item.get("score", 0))))
            except (ValueError, TypeError, AttributeError):
                scores.append(0.0)
        viable = bool(
            (plan or {}).get("swim_dates") or (plan or {}).get("candidate")
        )
        decision = decide_season(
            now, mode="end_season",
            mean_7d_c=mean,
            cold_since=cold_since,
            forecast_scores=scores,
            min_score=self.chauffage_predictif_score_baignade_min,
            viable_window=viable,
            forecast_ok=valid,
            already_closed=self._season_locked("end_season"),
            override_until=self._season_override_until(),
            cutoff_month=self.chauffage_fin_saison_cutoff[0],
            cutoff_day=self.chauffage_fin_saison_cutoff[1],
            cold_threshold_c=self.chauffage_fin_saison_seuil_7j_c,
            cold_confirmation_h=self.chauffage_fin_saison_confirmation_h,
        )
        self._season_last_decision = decision
        if decision.closure_recommended and not self._season_last_recommendation:
            message = (
                "Fin de saison recommandée : aucune fenêtre de baignade "
                "réalisable et refroidissement durable. Pour verrouiller "
                "le chauffage dès maintenant, activer "
                "input_boolean.piscine_chauffage_saison_terminee. "
                "Sans action, la pause météo reste réversible."
            )
            self.log(message, log="piscine_log")
            try:
                self.call_service(
                    "persistent_notification/create",
                    title="Piscine • Fin de saison recommandée",
                    message=message,
                    notification_id="pool_manager_fin_saison",
                )
            except Exception:
                pass
        self._season_last_recommendation = decision.closure_recommended
        return decision

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
            "season_start",
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
        self.chauffage_turbo_ends_at = None
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
        self.chauffage_turbo_ends_at = datetime.datetime.now() + timedelta(
            seconds=max(0, int(seconds))
        )
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
        self.chauffage_turbo_ends_at = None
        previous = self._get_chauffage_precedent()
        if self._seasonal_cutoff_active():
            previous = CHAUFFAGE_DESACTIVE
        self._set_chauffage_selector(previous)
        self.log(f"PAC Turbo terminé -> chauffage {previous}", log="piscine_log")

    def change_chauffage_mode(self, entity, attribute, old, new, kwargs):
        old_mode = str(old or "").strip()
        new_mode = str(new or "").strip()
        old_kind = chauffage_mode_kind(old_mode)
        new_kind = chauffage_mode_kind(new_mode)

        # Only a new, explicit selector change after the cutoff can grant a
        # bounded exception. A stale Turbo found at startup never reopens it.
        self._seasonal_turbo_override_until = None
        if (
            new_kind == "turbo"
            and old_kind != "unknown"
            and old_mode != new_mode
            and self._seasonal_cutoff_active()
        ):
            self._seasonal_turbo_override_until = datetime.datetime.now() + timedelta(
                seconds=turbo_duration_seconds(new_mode)
            )
        if getattr(self, "chauffage_fin_saison_hybride", False):
            if old_kind == "end_season":
                self._season_locked("end_season")
            if new_kind == "season_start" and old_kind != "season_start":
                # A deliberate new-season choice is the ONLY reset of a
                # persisted closure; weather and automatic modes never reset it.
                self._season_set_closed(False)
            self._season_last_decision = None

        if new_kind == "turbo":
            if old_kind != "turbo":
                self._set_chauffage_precedent(old_mode)
            seconds = turbo_duration_seconds(new_mode)
            if seconds:
                self._start_turbo_timer(seconds)
        elif old_kind == "turbo":
            self._cancel_turbo_timer()

            # Leaving a forced Turbo mode must also release the physical PAC
            # preset immediately. Start from Smart; predictive control may
            # explicitly select Turbo again a moment later if the new MPC plan
            # genuinely requires it.
            if new_kind in {"auto", "end_season", "season_start"}:
                self._set_pac_preset(self.chauffage_preset_smart)

        if self.chauffage_predictif and new_kind in {"auto", "end_season", "season_start"}:
            # Re-evaluate weather immediately after a strategy change.
            self.chauffage_predictif_forecast_at = None
            self.chauffage_predictif_last_log_signature = None
        if old_kind in {"auto", "end_season"} and old_kind != new_kind:
            self.chauffage_predictif_heat_requested = False
            self.chauffage_predictif_heat_target_c = None
            self.chauffage_predictif_last_plan = None

        if (
            new_kind not in {"auto", "end_season"}
            and getattr(self, "chauffage_predictif_measurement_active", False)
            and hasattr(self, "_reset_measurement_tracker")
        ):
            self._reset_measurement_tracker()

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
        if self._seasonal_heating_blocked() or bool(
            getattr(self, "chauffage_predictif_measurement_active", False)
        ):
            return False
        if not self._season_heat_allowed():
            self._cancel_chauffage_start()
            return False
        if not self._available(self.entity_pac_climate):
            self._fault("pac_climate", f"PAC indisponible ({label})")
            return False
        # A renewed heat decision cancels any older predictive WAIT request.
        self.pac_deferred_stop_reason = None
        try:
            before_state = self._pac_state()
            if before_state != "heat":
                if self._pac_start_deferred(label):
                    return False
                self.call_service(
                    "climate/set_hvac_mode",
                    entity_id=self.entity_pac_climate,
                    hvac_mode="heat",
                )
                self._pac_mark_started()
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

    def _apply_pac_heat_and_log(self, preset, label):
        before_state = self._pac_state()
        try:
            before_preset = self.get_state(
                self.entity_pac_climate,
                attribute="preset_mode",
            )
        except Exception:
            before_preset = None

        if not self._set_pac_heat(preset, label):
            return False

        before_name = str(before_preset or "").strip()
        after_name = str(preset or "").strip()
        same_preset = before_name.casefold() == after_name.casefold()
        if before_state == "heat" and same_preset:
            return True

        try:
            if before_state == "heat" and before_name and not same_preset:
                self.log(
                    f"PAC : passage {before_name} → {after_name} ({label}).",
                    log="piscine_log",
                )
            else:
                self.log(
                    f"PAC démarrée : chauffage {after_name} ({label}).",
                    log="piscine_log",
                )
        except Exception:
            pass
        return True

    def _request_chauffage_start(self, preset, label):
        if self._seasonal_heating_blocked() or bool(
            getattr(self, "chauffage_predictif_measurement_active", False)
        ):
            self._cancel_chauffage_start()
            return
        if not self._season_heat_allowed():
            self._cancel_chauffage_start()
            return
        if not self._chauffage_pool_mode_autorise():
            return
        if self._pac_start_deferred(label):
            self._cancel_chauffage_start()
            return
        if self._pump_flow_ok():
            self._cancel_chauffage_start()
            self._apply_pac_heat_and_log(preset, label)
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
        kind = chauffage_mode_kind(self.chauffage_mode())
        if (
            not self._season_heat_allowed(kind)
            or not self._predictive_start_still_allowed(kind)
            or not self._chauffage_pool_mode_autorise()
        ):
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
            self._apply_pac_heat_and_log(preset, label)
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

    def _manage_debut_saison(self):
        self._update_predictive_diagnostics(
            "season_start", override="🌡️ Début de saison"
        )
        water = self._premiere_chauffe_temperature()
        target = self._pac_target_temperature()
        if premiere_chauffe_terminee(water, target, self.chauffage_premiere_chauffe_marge_c):
            self._cancel_chauffage_start()
            self._set_chauffage_selector(CHAUFFAGE_AUTO)
            self.log(
                f"Début de saison terminé ({water:.1f}/{target:.1f} °C) -> Automatique",
                log="piscine_log",
            )
            return
        if water is None or target is None:
            self._fault("chauffage_temperature", "température/consigne PAC indisponible pour fin de début de saison")
        else:
            self._recover("chauffage_temperature")
        self._request_chauffage_start(self.chauffage_preset_smart, "début de saison")

    def _manage_pac_auto(self):
        # No selector configured: strict pre-v0.4.4 compatibility.
        if not self.entity_chauffage:
            return self._manage_pac_saisonnier()

        if self._seasonal_heating_blocked():
            self.chauffage_predictif_heat_requested = False
            self.chauffage_predictif_heat_target_c = None
            self.chauffage_predictif_last_plan = None
            self._cancel_chauffage_start()
            self._cancel_pac_start()
            self._cancel_turbo_timer()
            if self.chauffage_mode() != CHAUFFAGE_DESACTIVE:
                self._set_chauffage_selector(CHAUFFAGE_DESACTIVE)
            self._update_predictive_diagnostics("disabled", override="⏸ Arrêt saisonnier")
            self._pac_off("arrêt saisonnier", respect_min_on=True)
            return

        # Expire a bounded manual policy independently from the active heating
        # selector. Previously this happened only inside a Smart/MPC replan, so
        # an overnight choice could remain displayed throughout a long Turbo.
        pool_choice = getattr(self, "_pool_choice", None)
        if callable(pool_choice):
            pool_choice(datetime.datetime.now())

        mode_chauffage = self.chauffage_mode()
        kind = chauffage_mode_kind(mode_chauffage)
        if self._season_locked(kind):
            self.chauffage_predictif_heat_requested = False
            self.chauffage_predictif_heat_target_c = None
            self._cancel_chauffage_start()
            self._cancel_pac_start()
            self._season_last_decision = decide_season(
                self._season_now(), mode=kind, already_closed=True,
                cutoff_month=self.chauffage_fin_saison_cutoff[0],
                cutoff_day=self.chauffage_fin_saison_cutoff[1],
            )
            self._update_predictive_diagnostics(
                kind, override="🔒 Chauffage fermé pour la saison"
            )
            self._pac_off("fermeture saisonnière", post=True)
            return

        # No thermal learning or calibration may begin after the seasonal lock.
        self._update_predictive_learning()

        if kind == "unknown":
            self._cancel_chauffage_start()
            self._fault("chauffage_mode", "mode chauffage indisponible/inconnu")
            self._update_predictive_diagnostics(kind, override="⚠️ Mode inconnu")
            return
        self._recover("chauffage_mode")

        pool_mode = self._mode()
        if pool_mode in [TAB_MODE[2], TAB_MODE[4]] or self.arret_force_actif():
            self.chauffage_predictif_heat_requested = False
            self._cancel_chauffage_start()
            self._update_predictive_diagnostics(kind, override="⛔ Sécurité piscine")
            self._pac_off("hors gel / arrêt forcé", post=(pool_mode != TAB_MODE[4]))
            return

        if kind == "disabled":
            self.chauffage_predictif_heat_requested = False
            self._cancel_chauffage_start()
            self._cancel_pac_start()
            self._update_predictive_diagnostics(kind, override="⏸ Chauffage désactivé")
            self._pac_off("chauffage désactivé")
            return

        if kind == "season_start":
            self._cancel_pac_start()
            return self._manage_debut_saison()

        if kind == "turbo":
            self._cancel_pac_start()
            self._update_predictive_diagnostics(kind, override="🔥 Turbo")
            return self._request_chauffage_start(self.chauffage_preset_turbo, "Turbo")

        if self.chauffage_predictif and kind in {"auto", "end_season"}:
            if kind == "auto":
                if not self.gestion_pac_auto:
                    self.chauffage_predictif_heat_requested = False
                    self._cancel_chauffage_start()
                    self._update_predictive_diagnostics(
                        kind, override="⏸ Auto PAC non piloté"
                    )
                    return
                if hasattr(self, "mode_auto_autorise") and not self.mode_auto_autorise():
                    self.chauffage_predictif_heat_requested = False
                    self._cancel_chauffage_start()
                    self._update_predictive_diagnostics(
                        kind, override="⏸ Automatique suspendu"
                    )
                    return
            self._cancel_pac_start()
            return self._manage_predictive_heating(kind)

        if kind == "auto":
            self._cancel_chauffage_start()
            result = self._manage_pac_saisonnier()
            if self._pac_state() == "heat":
                self._set_pac_preset(self.chauffage_preset_auto)
            return result

        # Legacy end-of-season behavior when the predictive engine is disabled.
        if kind == "end_season":
            self._cancel_pac_start()
            return self._request_chauffage_start(
                self.chauffage_preset_smart,
                "fin de saison",
            )

    def _seasonal_cutoff_active(self):
        # The hybrid controller owns its persistent lock and explicit reopening.
        # Never stack an independent calendar gate on top of that policy.
        if getattr(self, "chauffage_fin_saison_hybride", False):
            return False
        return bool(getattr(self, "entity_chauffage", None)) and bool(getattr(self, "chauffage_arret_saisonnier", False)) and seasonal_cutoff_reached(
            datetime.datetime.now(), getattr(self, "chauffage_date_arret", (10, 10))
        )

    def _seasonal_heating_blocked(self):
        if not self._seasonal_cutoff_active():
            return False
        until = getattr(self, "_seasonal_turbo_override_until", None)
        return not (
            chauffage_mode_kind(self.chauffage_mode()) == "turbo"
            and isinstance(until, datetime.datetime)
            and datetime.datetime.now() < until
        )

    def _pac_circulation_chauffage_requise(self):
        # Certified water measurement requires circulation even though the PAC
        # itself may deliberately remain off during the 70% / 15-minute mixing
        # cycle. Reuse the existing pump-demand path instead of creating a
        # second hydraulic control loop.
        if getattr(self, "chauffage_fin_saison_hybride", False):
            if not self._season_heat_allowed():
                return False
        if (
            getattr(self, "chauffage_predictif_measurement_active", False)
            and not self.arret_force_actif()
        ):
            return True

        if self.entity_chauffage:
            kind = chauffage_mode_kind(self.chauffage_mode())
            if kind in {"season_start", "turbo"}:
                return True
            if kind == "end_season" and not self.chauffage_predictif:
                return True
            if kind in {"auto", "end_season"} and self.chauffage_predictif:
                return bool(self.chauffage_predictif_heat_requested)
        return None
