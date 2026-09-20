# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Runtime integration for self-learning adaptive pool heating."""

import datetime
import json
import os

from pool_mpc import build_mpc_plan
from pool_predictive import (
    build_predictive_plan,
    dashboard_forecast,
    enrich_daily_with_hourly,
    estimate_heating_rate,
    estimate_loss_rate,
    extract_weather_forecast,
    normalize_cover_state,
    normalize_daily_forecast,
    normalize_hourly_forecast,
    update_heating_rate_model,
    update_loss_model,
)


class PredictiveHeatingSupport:
    """Weather forecast + certified measurements + persistent thermal learning."""

    @staticmethod
    def _bool_value(value, default=False):
        if value is None:
            return bool(default)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _optional_float(value):
        if value is None or str(value).strip() == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_datetime(value):
        if isinstance(value, datetime.datetime):
            return value
        if not value:
            return None
        try:
            return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    def _predictive_arg(self, new_key, legacy_key=None, default=None):
        if new_key in self.args:
            return self.args.get(new_key)
        if legacy_key and legacy_key in self.args:
            return self.args.get(legacy_key)
        return default

    def _initialize_predictive_heating(self):
        self.chauffage_predictif = self._bool_value(
            self._predictive_arg("chauffage_predictif", "fin_saison_predictif", "false")
        )
        self.entity_meteo_chauffage_predictif = self._predictive_arg(
            "entity_meteo_chauffage_predictif",
            "entity_meteo_fin_saison",
        )
        self.entity_chauffage_predictif_status = self.args.get(
            "entity_chauffage_predictif_status"
        )
        self.entity_chauffage_predictif_score = self.args.get(
            "entity_chauffage_predictif_score",
            "sensor.piscine_score_baignade",
        )

        self.chauffage_predictif_horizon_jours = max(
            3,
            min(
                15,
                int(
                    float(
                        self._predictive_arg(
                            "chauffage_predictif_horizon_jours",
                            "fin_saison_horizon_jours",
                            15,
                        )
                    )
                ),
            ),
        )
        self.chauffage_predictif_prevision_refresh_s = max(
            300,
            int(
                float(
                    self._predictive_arg(
                        "chauffage_predictif_prevision_refresh_s",
                        "fin_saison_prevision_refresh_s",
                        1800,
                    )
                )
            ),
        )
        self.chauffage_predictif_prevision_max_age_s = max(
            self.chauffage_predictif_prevision_refresh_s,
            int(
                float(
                    self._predictive_arg(
                        "chauffage_predictif_prevision_max_age_s",
                        "fin_saison_prevision_max_age_s",
                        21600,
                    )
                )
            ),
        )

        self.chauffage_predictif_temperature_baignade_min_c = float(
            self._predictive_arg(
                "chauffage_predictif_temperature_baignade_min_c",
                "fin_saison_temperature_baignade_min_c",
                21.0,
            )
        )
        self.chauffage_predictif_temperature_baignade_ideale_c = float(
            self._predictive_arg(
                "chauffage_predictif_temperature_baignade_ideale_c",
                "fin_saison_temperature_baignade_ideale_c",
                26.0,
            )
        )
        self.chauffage_predictif_score_baignade_min = float(
            self._predictive_arg(
                "chauffage_predictif_score_baignade_min",
                "fin_saison_score_baignade_min",
                55.0,
            )
        )
        self.chauffage_predictif_bonus_weekend = max(
            0.0,
            float(self.args.get("chauffage_predictif_bonus_weekend", 10.0)),
        )

        # Initial fallback values; learned installation data progressively takes
        # precedence over them.
        self.chauffage_predictif_gain_chauffe_c_par_h = max(
            0.05,
            float(
                self._predictive_arg(
                    "chauffage_predictif_gain_chauffe_c_par_h",
                    "fin_saison_gain_chauffe_c_par_h",
                    0.30,
                )
            ),
        )
        self.chauffage_predictif_perte_nuit_delta10_c_par_h = max(
            0.0,
            float(
                self.args.get(
                    "chauffage_predictif_perte_nuit_delta10_c_par_h",
                    0.05,
                )
            ),
        )

        self.chauffage_predictif_temperature_min_eau_c = self._optional_float(
            self.args.get("chauffage_predictif_temperature_min_eau_c")
        )
        self.chauffage_predictif_plancher_auto_delta_c = max(
            0.0,
            float(self.args.get("chauffage_predictif_plancher_auto_delta_c", 2.0)),
        )
        self.chauffage_predictif_plancher_fin_saison_delta_c = max(
            0.0,
            float(
                self._predictive_arg(
                    "chauffage_predictif_plancher_fin_saison_delta_c",
                    "fin_saison_temperature_plancher_delta_c",
                    4.0,
                )
            ),
        )
        self.chauffage_predictif_recharge_plancher_c = max(
            0.1,
            float(
                self._predictive_arg(
                    "chauffage_predictif_recharge_plancher_c",
                    "fin_saison_recharge_plancher_c",
                    0.5,
                )
            ),
        )
        self.chauffage_predictif_marge_arret_c = max(
            0.0,
            float(
                self._predictive_arg(
                    "chauffage_predictif_marge_arret_c",
                    "fin_saison_marge_arret_c",
                    0.2,
                )
            ),
        )

        self.chauffage_predictif_apprentissage = self._bool_value(
            self.args.get("chauffage_predictif_apprentissage", "true"),
            default=True,
        )

        # v0.8 adaptive thermal model + receding-horizon MPC.  The existing
        # deterministic planner remains available as an explicit fallback.
        self.chauffage_predictif_mpc = self._bool_value(
            self.args.get("chauffage_predictif_mpc", "true"),
            default=True,
        )
        self.chauffage_predictif_mpc_pas_h = max(
            0.5,
            min(3.0, float(self.args.get("chauffage_predictif_mpc_pas_h", 1.0))),
        )
        self.chauffage_predictif_mpc_pas_temperature_c = max(
            0.1,
            min(
                0.5,
                float(
                    self.args.get(
                        "chauffage_predictif_mpc_pas_temperature_c",
                        0.2,
                    )
                ),
            ),
        )
        self.chauffage_predictif_mpc_puissance_smart_w = max(
            100.0,
            float(
                self.args.get(
                    "chauffage_predictif_mpc_puissance_smart_w",
                    self.args.get("seuil_pac_smart_w", 1200),
                )
            ),
        )
        self.chauffage_predictif_mpc_puissance_turbo_w = max(
            self.chauffage_predictif_mpc_puissance_smart_w,
            float(
                self.args.get(
                    "chauffage_predictif_mpc_puissance_turbo_w",
                    self.args.get("seuil_pac_turbo_w", 1900),
                )
            ),
        )
        self.chauffage_predictif_mpc_penalite_turbo_kwh_h = max(
            0.0,
            float(
                self.args.get(
                    "chauffage_predictif_mpc_penalite_turbo_kwh_h",
                    0.08,
                )
            ),
        )
        self.chauffage_predictif_mpc_penalite_nuit_kwh_h = max(
            0.0,
            float(
                self.args.get(
                    "chauffage_predictif_mpc_penalite_nuit_kwh_h",
                    0.35,
                )
            ),
        )
        self.chauffage_predictif_apprentissage_min_s = max(
            900,
            int(float(self.args.get("chauffage_predictif_apprentissage_min_s", 1800))),
        )
        self.chauffage_predictif_apprentissage_alpha = max(
            0.05,
            min(
                1.0,
                float(self.args.get("chauffage_predictif_apprentissage_alpha", 0.25)),
            ),
        )

        # Legacy measurement settings are still parsed for configuration
        # compatibility. Since the passive-sampling change, certification uses
        # the shared pump-start stabilization (tempo_eau) and never owns speed.
        self.chauffage_predictif_mesure_vitesse_pct = max(
            47,
            min(
                100,
                int(
                    float(
                        self.args.get(
                            "chauffage_predictif_mesure_vitesse_pct",
                            70,
                        )
                    )
                ),
            ),
        )
        self.chauffage_predictif_mesure_tempo_s = max(
            300,
            int(
                float(
                    self.args.get(
                        "chauffage_predictif_mesure_tempo_s",
                        900,
                    )
                )
            ),
        )
        self.chauffage_predictif_mesure_stabilite_s = max(
            60,
            int(
                float(
                    self.args.get(
                        "chauffage_predictif_mesure_stabilite_s",
                        120,
                    )
                )
            ),
        )
        self.chauffage_predictif_mesure_variation_max_c = max(
            0.02,
            float(
                self.args.get(
                    "chauffage_predictif_mesure_variation_max_c",
                    0.15,
                )
            ),
        )
        self.chauffage_predictif_mesure_fraiche_s = max(
            1800,
            int(
                float(
                    self.args.get(
                        "chauffage_predictif_mesure_fraiche_s",
                        21600,
                    )
                )
            ),
        )
        self.chauffage_predictif_mesure_intervalle_chauffe_s = max(
            1800,
            int(
                float(
                    self.args.get(
                        "chauffage_predictif_mesure_intervalle_chauffe_s",
                        7200,
                    )
                )
            ),
        )
        # A certified calibration must never hold the controller forever.
        # The reference speed gets a short grace period to settle; after a
        # failed calibration, predictive heating is allowed to continue from
        # the best available estimate and retries later.
        self.chauffage_predictif_mesure_timeout_grace_s = max(
            60,
            int(
                float(
                    self.args.get(
                        "chauffage_predictif_mesure_timeout_grace_s",
                        300,
                    )
                )
            ),
        )
        self.chauffage_predictif_mesure_retry_s = max(
            60,
            int(
                float(
                    self.args.get(
                        "chauffage_predictif_mesure_retry_s",
                        900,
                    )
                )
            ),
        )
        self.chauffage_predictif_mesure_anticipation_jours = max(
            1,
            min(
                5,
                int(
                    float(
                        self.args.get(
                            "chauffage_predictif_mesure_anticipation_jours",
                            3,
                        )
                    )
                ),
            ),
        )
        self.chauffage_predictif_heures_chauffe_jour = max(
            4.0,
            min(
                16.0,
                float(self.args.get("chauffage_predictif_heures_chauffe_jour", 12.0)),
            ),
        )
        self.chauffage_predictif_heures_jour_baignade = max(
            1.0,
            min(
                10.0,
                float(self.args.get("chauffage_predictif_heures_jour_baignade", 6.0)),
            ),
        )
        # Future night-loss forecasts must not reuse the *current* daytime cover
        # state. With a configured cover entity we assume the normal night policy
        # is closed unless the installation explicitly overrides it.
        self.chauffage_predictif_volet_nuit_prevu = normalize_cover_state(
            self.args.get(
                "chauffage_predictif_volet_nuit_prevu",
                "closed" if self.args.get("entity_volet_piscine") else "unknown",
            )
        )

        default_learning_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "pool_manager_thermal_learning.json",
        )
        self.chauffage_predictif_learning_file = str(
            self.args.get("chauffage_predictif_learning_file", default_learning_file)
        )

        self.chauffage_predictif_forecast = []
        self.chauffage_predictif_forecast_at = None
        self.chauffage_predictif_heat_requested = False
        self.chauffage_predictif_heat_target_c = None
        self.chauffage_predictif_last_plan = None
        self.chauffage_predictif_last_log_signature = None
        self.chauffage_predictif_last_status_signature = None

        self.chauffage_predictif_rate_model = {}
        self.chauffage_predictif_loss_model = {}

        self.chauffage_predictif_certified_water_c = None
        self.chauffage_predictif_certified_at = None
        self.chauffage_predictif_measurement_active = False
        self.chauffage_predictif_measurement_purpose = None
        self.chauffage_predictif_measurement_previous_speed = None
        self.chauffage_predictif_measurement_requested_at = None
        self.chauffage_predictif_measurement_started_at = None
        self.chauffage_predictif_measurement_failed_at = None
        self.chauffage_predictif_measurement_stable_at = None
        self.chauffage_predictif_measurement_stable_temp = None

        self._heating_learning_session = None
        self._passive_learning_session = None
        self._load_predictive_learning()

    # ----------------------------- forecast cache -----------------------------

    def _predictive_forecast_age_s(self):
        if self.chauffage_predictif_forecast_at is None:
            return None
        return max(
            0.0,
            (
                datetime.datetime.now() - self.chauffage_predictif_forecast_at
            ).total_seconds(),
        )

    def _refresh_predictive_forecast(self):
        if not self.entity_meteo_chauffage_predictif:
            self._fault(
                "chauffage_predictif_meteo",
                "chauffage prédictif sans entité météo",
            )
            return self.chauffage_predictif_forecast

        age = self._predictive_forecast_age_s()
        if (
            age is not None
            and age < self.chauffage_predictif_prevision_refresh_s
            and self.chauffage_predictif_forecast
        ):
            return self.chauffage_predictif_forecast

        try:
            result = self.call_service(
                "weather/get_forecasts",
                entity_id=self.entity_meteo_chauffage_predictif,
                type="daily",
                return_result=True,
                hass_timeout=10,
            )
            raw = extract_weather_forecast(result, self.entity_meteo_chauffage_predictif)
            daily = normalize_daily_forecast(
                raw,
                horizon_days=self.chauffage_predictif_horizon_jours,
                min_air_c=self.chauffage_predictif_temperature_baignade_min_c,
                ideal_air_c=self.chauffage_predictif_temperature_baignade_ideale_c,
            )
            if not daily:
                raise ValueError("réponse météo sans prévisions daily")

            # Hourly data is an optimization, not a hard dependency. It refines
            # PAC ambient temperature and the real after-work/weekend usage window.
            try:
                hourly_result = self.call_service(
                    "weather/get_forecasts",
                    entity_id=self.entity_meteo_chauffage_predictif,
                    type="hourly",
                    return_result=True,
                    hass_timeout=10,
                )
                hourly_raw = extract_weather_forecast(
                    hourly_result,
                    self.entity_meteo_chauffage_predictif,
                )
                hourly = normalize_hourly_forecast(
                    hourly_raw,
                    min_air_c=self.chauffage_predictif_temperature_baignade_min_c,
                    ideal_air_c=self.chauffage_predictif_temperature_baignade_ideale_c,
                )
                if hourly:
                    daily = enrich_daily_with_hourly(daily, hourly)
            except Exception:
                pass

            self.chauffage_predictif_forecast = daily
            self.chauffage_predictif_forecast_at = datetime.datetime.now()
            self._recover(
                "chauffage_predictif_meteo",
                f"{len(daily)} jours de prévision disponibles",
            )
            return daily
        except Exception as exc:
            age = self._predictive_forecast_age_s()
            if (
                self.chauffage_predictif_forecast
                and age is not None
                and age <= self.chauffage_predictif_prevision_max_age_s
            ):
                self._fault(
                    "chauffage_predictif_meteo",
                    f"prévision non actualisée ({exc}); cache utilisé",
                )
                return self.chauffage_predictif_forecast

            self.chauffage_predictif_forecast = []
            self._fault(
                "chauffage_predictif_meteo",
                f"prévisions météo indisponibles ({exc})",
            )
            return []

    # -------------------------- persistent learning --------------------------

    def _serialize_passive_session(self):
        session = self._passive_learning_session
        if not session:
            return None
        return {
            "started_at": (
                session["started_at"].isoformat()
                if isinstance(session.get("started_at"), datetime.datetime)
                else session.get("started_at")
            ),
            "water": session.get("water"),
            "cover": session.get("cover"),
            "ambient_sum": session.get("ambient_sum", 0.0),
            "ambient_count": session.get("ambient_count", 0),
            "contaminated": bool(session.get("contaminated", False)),
        }

    def _load_predictive_learning(self):
        try:
            with open(self.chauffage_predictif_learning_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                return

            self.chauffage_predictif_rate_model = dict(payload.get("heating") or {})
            self.chauffage_predictif_loss_model = dict(payload.get("night_loss") or {})

            certified = payload.get("last_certified") or {}
            self.chauffage_predictif_certified_water_c = self._optional_float(
                certified.get("water_c")
            )
            self.chauffage_predictif_certified_at = self._parse_datetime(
                certified.get("at")
            )

            passive = payload.get("pending_passive") or {}
            started_at = self._parse_datetime(passive.get("started_at"))
            if started_at is not None and passive.get("water") is not None:
                self._passive_learning_session = {
                    "started_at": started_at,
                    "water": float(passive.get("water")),
                    "cover": passive.get("cover", "unknown"),
                    "ambient_sum": float(passive.get("ambient_sum") or 0.0),
                    "ambient_count": int(passive.get("ambient_count") or 0),
                    "contaminated": bool(passive.get("contaminated", False)),
                }
        except FileNotFoundError:
            pass
        except Exception as exc:
            try:
                self.log(
                    f"Apprentissage thermique: lecture impossible ({exc})",
                    log="piscine_log",
                )
            except Exception:
                pass

    def _save_predictive_learning(self):
        if not self.chauffage_predictif_apprentissage:
            return

        certified = None
        if (
            self.chauffage_predictif_certified_water_c is not None
            and self.chauffage_predictif_certified_at is not None
        ):
            certified = {
                "water_c": round(float(self.chauffage_predictif_certified_water_c), 3),
                "at": self.chauffage_predictif_certified_at.isoformat(),
            }

        payload = {
            "version": 2,
            "updated_at": datetime.datetime.now().isoformat(),
            "heating": self.chauffage_predictif_rate_model,
            "night_loss": self.chauffage_predictif_loss_model,
            "last_certified": certified,
            "pending_passive": self._serialize_passive_session(),
        }

        path = self.chauffage_predictif_learning_file
        tmp = f"{path}.tmp"
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, path)
        except Exception as exc:
            try:
                self.log(
                    f"Apprentissage thermique: sauvegarde impossible ({exc})",
                    log="piscine_log",
                )
            except Exception:
                pass
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    # ----------------------- certified water measurement -----------------------

    def _predictive_physical_water_raw(self):
        try:
            return self._raw_float(self.args.get("temperature_eau"))
        except Exception:
            return None

    def _predictive_cover_state(self):
        entity = getattr(self, "entity_volet_piscine", None)
        if not entity:
            return "unknown"
        try:
            return normalize_cover_state(self.get_state(entity))
        except Exception:
            return "unknown"

    def _predictive_expected_night_cover_state(self):
        return getattr(
            self,
            "chauffage_predictif_volet_nuit_prevu",
            "unknown",
        )

    def _certified_age_s(self, now=None):
        if self.chauffage_predictif_certified_at is None:
            return None
        now = now or datetime.datetime.now()
        try:
            return max(
                0.0,
                (now - self.chauffage_predictif_certified_at).total_seconds(),
            )
        except TypeError:
            return None

    def _certified_fresh(self, now=None, same_day=False):
        now = now or datetime.datetime.now()
        age = self._certified_age_s(now)
        if age is None or age > self.chauffage_predictif_mesure_fraiche_s:
            return False
        if same_day and self.chauffage_predictif_certified_at.date() != now.date():
            return False
        return True

    def _measurement_tempo_eau_s(self):
        try:
            return max(
                0,
                int(float(self.get_state(self.args["tempo_eau"]))),
            )
        except Exception:
            return 0

    def _measurement_retry_blocked(self, now=None):
        failed_at = getattr(
            self,
            "chauffage_predictif_measurement_failed_at",
            None,
        )
        if failed_at is None:
            return False
        now = now or datetime.datetime.now()
        try:
            age = max(0.0, (now - failed_at).total_seconds())
        except TypeError:
            return False
        return age < self.chauffage_predictif_mesure_retry_s

    def _measurement_progress(self, now=None):
        now = now or datetime.datetime.now()
        tempo_eau = self._measurement_tempo_eau_s()
        stable_s = max(
            0,
            int(getattr(self, "chauffage_predictif_mesure_stabilite_s", 0)),
        )
        if not self.chauffage_predictif_measurement_active:
            return None, None, None

        started_at = self.chauffage_predictif_measurement_started_at
        total_reference_s = tempo_eau + stable_s
        if not isinstance(started_at, datetime.datetime):
            return 0, total_reference_s, "raising_flow"

        try:
            elapsed = max(0, int((now - started_at).total_seconds()))
        except TypeError:
            elapsed = 0

        if elapsed < tempo_eau:
            return (
                elapsed,
                max(0, tempo_eau - elapsed + stable_s),
                "circulating",
            )

        stable_at = self.chauffage_predictif_measurement_stable_at
        if not isinstance(stable_at, datetime.datetime):
            return elapsed, stable_s, "stability"

        try:
            stable_elapsed = max(0, int((now - stable_at).total_seconds()))
        except TypeError:
            stable_elapsed = 0
        return (
            elapsed,
            max(0, stable_s - stable_elapsed),
            "stability",
        )

    def _abort_measurement_timeout(self, now, reason):
        self.chauffage_predictif_measurement_failed_at = now
        try:
            self._fault("chauffage_predictif_measurement_timeout", reason)
        except Exception:
            pass
        try:
            self.log(
                f"Mesure température abandonnée: {reason}; "
                f"nouvelle tentative dans "
                f"{self.chauffage_predictif_mesure_retry_s // 60} min",
                log="piscine_log",
            )
        except Exception:
            pass
        self._reset_measurement_tracker()
        return False

    def _reset_measurement_tracker(self, keep_request=False, recalculate=True):
        self.chauffage_predictif_measurement_started_at = None
        self.chauffage_predictif_measurement_stable_at = None
        self.chauffage_predictif_measurement_stable_temp = None
        if not keep_request:
            was_active = bool(self.chauffage_predictif_measurement_active)
            self.chauffage_predictif_measurement_active = False
            self.chauffage_predictif_measurement_purpose = None
            self.chauffage_predictif_measurement_previous_speed = None
            self.chauffage_predictif_measurement_requested_at = None

            # Once certification ends, hand speed authority back to the normal
            # automatic strategy. Pump-stop callbacks deliberately disable the
            # immediate recalculation to avoid recursive stop/start sequences.
            if was_active and recalculate:
                try:
                    self.traitement({})
                except Exception:
                    pass

    @staticmethod
    def _measurement_purpose_label(purpose):
        labels = {
            "startup_calibration": "mesure au démarrage",
            "decision": "décision chauffage",
            "morning_decision": "décision du matin",
            "heating_learning": "apprentissage chauffage",
            "target_check": "contrôle de consigne",
            "temperature_stabilization": "stabilisation température",
        }
        return labels.get(str(purpose or ""), str(purpose or "mesure température"))

    def _interrupt_predictive_measurement(self, reason="pompe arrêtée"):
        """Invalidate an in-flight calibration after any real pump stop."""
        if not bool(getattr(self, "chauffage_predictif_measurement_active", False)):
            return False

        purpose = self._measurement_purpose_label(
            getattr(self, "chauffage_predictif_measurement_purpose", None)
        )
        self._reset_measurement_tracker(recalculate=False)
        try:
            self.log(
                f"Mesure température interrompue ({purpose}) : {reason}. "
                "La prochaine mesure repartira depuis zéro.",
                log="piscine_log",
            )
        except Exception:
            pass
        return True

    def _request_predictive_measurement(self, purpose, start_pump=False):
        """Start a protected certified calibration on an already justified run.

        Once started, normal solar/grid optimization must not stop the pump.
        Safety and forced-stop rules keep higher priority. The configured
        reference speed is a temporary minimum; higher automatic speeds remain
        allowed.
        """
        if not self.chauffage_predictif:
            return False
        if self.arret_force_actif():
            return False
        if self._measurement_retry_blocked():
            return False
        if not self.pompe_est_on():
            return False

        speed = self.get_fan_percentage()
        if speed is None:
            return False

        newly_started = False
        if not self.chauffage_predictif_measurement_active:
            newly_started = True
            self.chauffage_predictif_measurement_active = True
            self.chauffage_predictif_measurement_purpose = str(purpose)
            self.chauffage_predictif_measurement_previous_speed = int(speed)
            self._reset_measurement_tracker(keep_request=True)
            self.chauffage_predictif_measurement_requested_at = (
                datetime.datetime.now()
            )

        minimum = self.chauffage_predictif_mesure_vitesse_pct
        if speed < minimum:
            try:
                self.set_pump_percentage(minimum, force=True)
            except Exception:
                return False

        if newly_started:
            try:
                self.log(
                    "Mesure température démarrée : "
                    f"{self._measurement_purpose_label(purpose)} • "
                    f"circulation {self._measurement_tempo_eau_s() // 60} min "
                    f"+ stabilité {self.chauffage_predictif_mesure_stabilite_s // 60} min "
                    f"(pompe ≥ {minimum}%).",
                    log="piscine_log",
                )
            except Exception:
                pass

        return True

    def _register_certified_measurement(self, now, water):
        self.chauffage_predictif_certified_water_c = float(water)
        self.chauffage_predictif_certified_at = now
        self.chauffage_predictif_measurement_failed_at = None
        try:
            self._recover(
                "chauffage_predictif_measurement_timeout",
                "mesure température certifiée",
            )
        except Exception:
            pass

        self._finalize_passive_learning(now, float(water))

        # Start the next passive reference only when the PAC is not heating.
        if not self._pac_power_active():
            ambient = self._raw_float(
                getattr(self, "entity_temperature_exterieure", None)
            )
            self._passive_learning_session = {
                "started_at": now,
                "water": float(water),
                "cover": self._predictive_cover_state(),
                "ambient_sum": float(ambient or 0.0),
                "ambient_count": 1 if ambient is not None else 0,
                "contaminated": False,
            }

        purpose = self._measurement_purpose_label(
            self.chauffage_predictif_measurement_purpose
        )
        try:
            speed = self.get_fan_percentage()
            speed_txt = f"{speed}%" if speed is not None else "vitesse inconnue"
            self.log(
                f"Température bassin certifiée : {water:.2f} °C "
                f"({speed_txt}, {purpose}, circulation et stabilité validées).",
                log="piscine_log",
            )
        except Exception:
            pass

        # Log the certified value before recalculating the MPC so the central
        # journal reads in chronological order: measure -> decision -> action.
        self._reset_measurement_tracker()
        self._save_predictive_learning()
        return True

    def _update_certified_measurement(self, now=None):
        now = now or datetime.datetime.now()

        if not self.pompe_est_on():
            if self.chauffage_predictif_measurement_active:
                self._interrupt_predictive_measurement("pompe arrêtée")
            return False

        speed = self.get_fan_percentage()
        if speed is None:
            self._reset_measurement_tracker()
            return False

        minimum = self.chauffage_predictif_mesure_vitesse_pct

        # A new pump run needs one certified calibration. Do not immediately
        # re-arm after a bounded calibration failure: the controller may keep
        # operating from its best estimate and retry after the cooldown.
        last_start = getattr(self, "last_pompe_on", None)
        certified_at = self.chauffage_predictif_certified_at
        new_run_needs_sample = certified_at is None
        if last_start is not None and certified_at is not None:
            try:
                new_run_needs_sample = certified_at < last_start
            except TypeError:
                new_run_needs_sample = True

        if (
            not self.chauffage_predictif_measurement_active
            and new_run_needs_sample
            and not self._measurement_retry_blocked(now)
        ):
            self.chauffage_predictif_measurement_active = True
            self.chauffage_predictif_measurement_purpose = "startup_calibration"
            self.chauffage_predictif_measurement_previous_speed = int(speed)
            self.chauffage_predictif_measurement_requested_at = now
            self.chauffage_predictif_measurement_started_at = (
                now if speed >= minimum else None
            )
            try:
                self.log(
                    "Mesure température démarrée : mesure au démarrage • "
                    f"circulation {self._measurement_tempo_eau_s() // 60} min "
                    f"+ stabilité {self.chauffage_predictif_mesure_stabilite_s // 60} min "
                    f"(pompe ≥ {minimum}%).",
                    log="piscine_log",
                )
            except Exception:
                pass

        if not self.chauffage_predictif_measurement_active:
            return False

        if getattr(
            self,
            "chauffage_predictif_measurement_requested_at",
            None,
        ) is None:
            self.chauffage_predictif_measurement_requested_at = now

        # Defensive continuity check: if the pump restarted after this
        # calibration clock was established, the full hydraulic delay must start
        # again even if the normal filtration shortcut would consider a short
        # interruption already mixed.
        started_at = self.chauffage_predictif_measurement_started_at
        if (
            isinstance(started_at, datetime.datetime)
            and isinstance(last_start, datetime.datetime)
        ):
            try:
                restarted = last_start > started_at
            except TypeError:
                restarted = False
            if restarted:
                self.chauffage_predictif_measurement_started_at = (
                    last_start if speed >= minimum else None
                )
                self.chauffage_predictif_measurement_requested_at = last_start
                self.chauffage_predictif_measurement_stable_at = None
                self.chauffage_predictif_measurement_stable_temp = None

        # Before the reference speed has ever been observed, keep requesting the
        # temporary minimum. Give the device a bounded grace period to report the
        # new speed instead of leaving measurement_active stuck forever.
        if speed < minimum:
            try:
                self.set_pump_percentage(minimum, force=True)
            except Exception:
                return self._abort_measurement_timeout(
                    now,
                    "vitesse de calibration impossible à commander",
                )

            try:
                confirmed_speed = self.get_fan_percentage()
            except Exception:
                confirmed_speed = None
            if confirmed_speed is not None and confirmed_speed >= minimum:
                speed = confirmed_speed
                if self.chauffage_predictif_measurement_started_at is None:
                    self.chauffage_predictif_measurement_started_at = now

            if self.chauffage_predictif_measurement_started_at is None:
                try:
                    waiting_s = (
                        now - self.chauffage_predictif_measurement_requested_at
                    ).total_seconds()
                except TypeError:
                    waiting_s = 0.0
                if waiting_s >= self.chauffage_predictif_mesure_timeout_grace_s:
                    return self._abort_measurement_timeout(
                        now,
                        f"vitesse {minimum}% non confirmée dans le délai",
                    )
                return False
        elif self.chauffage_predictif_measurement_started_at is None:
            self.chauffage_predictif_measurement_started_at = now

        tempo_eau = self._measurement_tempo_eau_s()
        stable_s = max(0, int(self.chauffage_predictif_mesure_stabilite_s))
        started_at = self.chauffage_predictif_measurement_started_at
        if started_at is None:
            return False

        try:
            elapsed = max(0.0, (now - started_at).total_seconds())
        except TypeError:
            elapsed = 0.0

        # The lifecycle gate and the dedicated calibration clock must agree.
        if not bool(getattr(self, "fin_tempo", 0)):
            if elapsed >= tempo_eau + self.chauffage_predictif_mesure_timeout_grace_s:
                return self._abort_measurement_timeout(
                    now,
                    "temporisation de circulation non validée",
                )
            return False

        if elapsed < tempo_eau:
            return False

        water = self._predictive_physical_water_raw()
        if water is None:
            if elapsed >= (
                tempo_eau
                + stable_s
                + self.chauffage_predictif_mesure_timeout_grace_s
            ):
                return self._abort_measurement_timeout(
                    now,
                    "sonde température eau indisponible",
                )
            return False

        # After hydraulic mixing, require a genuinely stable pipe reading before
        # calling it a pool temperature. This is especially important after a
        # night stop, where stagnant local water can move by several degrees in
        # the first minutes after circulation resumes.
        stable_at = self.chauffage_predictif_measurement_stable_at
        stable_temp = self.chauffage_predictif_measurement_stable_temp
        max_variation = float(
            self.chauffage_predictif_mesure_variation_max_c
        )

        if not isinstance(stable_at, datetime.datetime) or stable_temp is None:
            self.chauffage_predictif_measurement_stable_at = now
            self.chauffage_predictif_measurement_stable_temp = float(water)
            return False

        if abs(float(water) - float(stable_temp)) > max_variation:
            if elapsed >= (
                tempo_eau
                + stable_s
                + self.chauffage_predictif_mesure_timeout_grace_s
            ):
                return self._abort_measurement_timeout(
                    now,
                    "température eau non stabilisée dans le délai",
                )
            self.chauffage_predictif_measurement_stable_at = now
            self.chauffage_predictif_measurement_stable_temp = float(water)
            return False

        try:
            stable_elapsed = max(0.0, (now - stable_at).total_seconds())
        except TypeError:
            stable_elapsed = 0.0

        if stable_elapsed < stable_s:
            return False

        self._register_certified_measurement(now, float(water))
        return True

    @staticmethod
    def _crosses_midnight(start, end):
        return start.date() != end.date()

    def _update_passive_session_context(self, ambient):
        session = self._passive_learning_session
        if not session:
            return

        if self._pac_power_active():
            session["contaminated"] = True
        if self._predictive_cover_state() != session.get("cover"):
            session["contaminated"] = True
        if ambient is not None:
            session["ambient_sum"] += float(ambient)
            session["ambient_count"] += 1

    def _finalize_passive_learning(self, now, water):
        session = self._passive_learning_session
        if not session:
            return

        started = session.get("started_at")
        if not isinstance(started, datetime.datetime):
            self._passive_learning_session = None
            return

        elapsed_s = (now - started).total_seconds()
        valid_night = (
            4 * 3600 <= elapsed_s <= 20 * 3600
            and self._crosses_midnight(started, now)
            and not session.get("contaminated", False)
        )

        if valid_night:
            loss = float(session["water"]) - float(water)
            rate = loss / (elapsed_s / 3600.0)
            avg_ambient = (
                session["ambient_sum"] / session["ambient_count"]
                if session.get("ambient_count")
                else None
            )
            before = self.chauffage_predictif_loss_model
            learned = update_loss_model(
                before,
                session.get("cover"),
                session.get("water"),
                avg_ambient,
                rate,
                alpha=self.chauffage_predictif_apprentissage_alpha,
            )
            if learned != before:
                self.chauffage_predictif_loss_model = learned
                try:
                    self.log(
                        f"Apprentissage pertes nuit: {loss:.2f} °C sur "
                        f"{elapsed_s / 3600.0:.1f} h "
                        f"(air {avg_ambient if avg_ambient is not None else '?'} °C, "
                        f"volet {session.get('cover')})",
                        log="piscine_log",
                    )
                except Exception:
                    pass

        self._passive_learning_session = None

    def _predictive_pac_preset(self):
        try:
            value = self.get_state(
                self.entity_pac_climate,
                attribute="preset_mode",
            )
            return str(value or self.chauffage_preset_smart)
        except Exception:
            return self.chauffage_preset_smart

    def _update_heating_learning(self, now, ambient):
        active = self._pac_power_active()
        preset = self._predictive_pac_preset()
        session = self._heating_learning_session

        if not active:
            self._heating_learning_session = None
            return

        certified_at = self.chauffage_predictif_certified_at
        certified_water = self.chauffage_predictif_certified_water_c
        if certified_at is None or certified_water is None:
            return

        if (
            session is None
            or session.get("preset") != preset
            or session.get("start_certified_at") is None
        ):
            age = self._certified_age_s(now)
            if age is None or age > 1800:
                return
            self._heating_learning_session = {
                "started_at": now,
                "start_certified_at": certified_at,
                "water": float(certified_water),
                "preset": preset,
                "ambient_sum": float(ambient or 0.0),
                "ambient_count": 1 if ambient is not None else 0,
                "power_sum": 0.0,
                "power_count": 0,
            }
            return

        if ambient is not None:
            session["ambient_sum"] += float(ambient)
            session["ambient_count"] += 1

        try:
            power = self._raw_float(self.entity_pac_conso)
        except Exception:
            power = None
        if power is not None and power > 0:
            session["power_sum"] += float(power)
            session["power_count"] += 1

        # Learn only from a NEW certified water measurement. Raw pipe readings
        # never enter the model.
        if certified_at <= session["start_certified_at"]:
            return

        elapsed_s = (certified_at - session["started_at"]).total_seconds()
        if elapsed_s < self.chauffage_predictif_apprentissage_min_s:
            return

        rate = (
            float(certified_water) - float(session["water"])
        ) / (elapsed_s / 3600.0)
        avg_ambient = (
            session["ambient_sum"] / session["ambient_count"]
            if session["ambient_count"]
            else ambient
        )
        avg_power = (
            session["power_sum"] / session["power_count"]
            if session["power_count"]
            else None
        )

        before = self.chauffage_predictif_rate_model
        learned = update_heating_rate_model(
            before,
            preset,
            avg_ambient,
            rate,
            alpha=self.chauffage_predictif_apprentissage_alpha,
            sample_power_w=avg_power,
        )
        if learned != before:
            self.chauffage_predictif_rate_model = learned
            self._save_predictive_learning()
            try:
                self.log(
                    f"Apprentissage PAC {preset}: {rate:.3f} °C/h "
                    f"à {avg_ambient if avg_ambient is not None else '?'} °C"
                    + (
                        f", {avg_power:.0f} W"
                        if avg_power is not None
                        else ""
                    ),
                    log="piscine_log",
                )
            except Exception:
                pass

        self._heating_learning_session = {
            "started_at": now,
            "start_certified_at": certified_at,
            "water": float(certified_water),
            "preset": preset,
            "ambient_sum": float(ambient or 0.0),
            "ambient_count": 1 if ambient is not None else 0,
            "power_sum": 0.0,
            "power_count": 0,
        }

    def _maybe_request_learning_measurement(self, now):
        if not self._pac_power_active():
            return
        if self.chauffage_predictif_measurement_active:
            return

        age = self._certified_age_s(now)
        if age is None or age >= self.chauffage_predictif_mesure_intervalle_chauffe_s:
            self._request_predictive_measurement(
                "heating_learning",
                start_pump=False,
            )

    def _update_predictive_learning(self):
        if not self.chauffage_predictif_apprentissage:
            return

        now = datetime.datetime.now()
        ambient = self._raw_float(
            getattr(self, "entity_temperature_exterieure", None)
        )

        self._update_passive_session_context(ambient)
        self._maybe_request_learning_measurement(now)
        self._update_certified_measurement(now)
        self._update_heating_learning(now, ambient)

    # ---------------------- estimated / operational water ----------------------

    def _predictive_water_temperature(self):
        """Return best current estimate without pretending it is a measurement."""
        now = datetime.datetime.now()

        # A freshly certified measurement is authoritative.
        if self._certified_fresh(now):
            water = float(self.chauffage_predictif_certified_water_c)
            elapsed_h = (self._certified_age_s(now) or 0.0) / 3600.0
            ambient = self._raw_float(
                getattr(self, "entity_temperature_exterieure", None)
            )

            if self._pac_power_active():
                # Heating gain starts when the PAC session starts, not when the
                # last certified measurement was taken. A morning certification
                # can precede a noon PAC start by hours.
                session = self._heating_learning_session
                if (
                    session
                    and session.get("start_certified_at")
                    == self.chauffage_predictif_certified_at
                    and isinstance(session.get("started_at"), datetime.datetime)
                ):
                    elapsed_h = max(
                        0.0,
                        (now - session["started_at"]).total_seconds() / 3600.0,
                    )
                    water = float(session.get("water", water))
                else:
                    elapsed_h = 0.0

                rate = estimate_heating_rate(
                    self.chauffage_predictif_gain_chauffe_c_par_h,
                    self._predictive_pac_preset(),
                    ambient,
                    learned_model=self.chauffage_predictif_rate_model,
                )
                return water + rate * elapsed_h

            # When circulation is stopped, project passive cooling from the last
            # certified pool temperature. This is an estimate only; the first
            # new sample after tempo_eau at/above the reference speed corrects it.
            if not self.pompe_est_on():
                loss_rate = estimate_loss_rate(
                    water,
                    ambient,
                    cover_state=self._predictive_cover_state(),
                    learned_model=self.chauffage_predictif_loss_model,
                    fallback_delta10_c_per_h=(
                        self.chauffage_predictif_perte_nuit_delta10_c_par_h
                    ),
                )
                return water - loss_rate * elapsed_h

            # During normal circulation below certification speed, keep the last
            # certified reference rather than training on a potentially local pipe
            # temperature.
            return water

        try:
            memory = self._raw_float(self.args.get("mem_temp"))
        except Exception:
            memory = None
        if memory is not None:
            return memory

        # Last-resort operational fallback only. This raw reading is never used
        # for thermal learning.
        return self._predictive_physical_water_raw()

    def _predictive_daylight_active(self):
        try:
            return bool(self._daylight_active())
        except Exception:
            hour = datetime.datetime.now().hour
            return 8 <= hour < 20

    def _predictive_daylight_hours(self):
        try:
            bounds = self._daylight_bounds()
            if bounds is not None:
                start, end = bounds
                return max(
                    4.0,
                    min(18.0, (end - start).total_seconds() / 3600.0),
                )
        except Exception:
            pass
        return self.chauffage_predictif_heures_chauffe_jour

    def _predictive_daylight_hours_remaining(self):
        try:
            bounds = self._daylight_bounds()
            if bounds is not None:
                start, end = bounds
                now = (
                    datetime.datetime.now(end.tzinfo)
                    if end.tzinfo
                    else datetime.datetime.now()
                )
                if now >= end:
                    return 0.0
                if now <= start:
                    return max(0.0, (end - start).total_seconds() / 3600.0)
                return max(0.0, (end - now).total_seconds() / 3600.0)
        except Exception:
            pass

        now = datetime.datetime.now()
        if now.hour < 8:
            return self.chauffage_predictif_heures_chauffe_jour
        if now.hour >= 20:
            return 0.0
        return max(0.0, 20.0 - (now.hour + now.minute / 60.0))

    # --------------------------- planning + status ---------------------------

    def _predictive_profile_floor_delta(self, kind):
        if kind == "end_season":
            return self.chauffage_predictif_plancher_fin_saison_delta_c
        return self.chauffage_predictif_plancher_auto_delta_c

    def _build_runtime_predictive_plan(self, kind, water, target, forecast):
        day_hours = self._predictive_daylight_hours()
        remaining = self._predictive_daylight_hours_remaining()
        common = dict(
            now=datetime.datetime.now(),
            water_c=water,
            target_c=target,
            forecast=forecast,
            base_heating_rate_c_per_h=self.chauffage_predictif_gain_chauffe_c_par_h,
            heating_rate_model=self.chauffage_predictif_rate_model,
            loss_model=self.chauffage_predictif_loss_model,
            cover_state=self._predictive_expected_night_cover_state(),
            floor_delta_c=self._predictive_profile_floor_delta(kind),
            minimum_water_c=self.chauffage_predictif_temperature_min_eau_c,
            floor_recharge_c=self.chauffage_predictif_recharge_plancher_c,
            stop_margin_c=self.chauffage_predictif_marge_arret_c,
            score_min=self.chauffage_predictif_score_baignade_min,
            min_air_c=self.chauffage_predictif_temperature_baignade_min_c,
            smart_preset=self.chauffage_preset_smart,
            turbo_preset=self.chauffage_preset_turbo,
            day_hours=day_hours,
            today_day_hours_remaining=remaining,
            candidate_day_hours=self.chauffage_predictif_heures_jour_baignade,
            night_hours=max(6.0, 24.0 - day_hours),
            loss_fallback_delta10_c_per_h=(
                self.chauffage_predictif_perte_nuit_delta10_c_par_h
            ),
            daylight_active=self._predictive_daylight_active(),
        )
        if self.chauffage_predictif_mpc:
            return build_mpc_plan(
                **common,
                smart_power_fallback_w=(
                    self.chauffage_predictif_mpc_puissance_smart_w
                ),
                turbo_power_fallback_w=(
                    self.chauffage_predictif_mpc_puissance_turbo_w
                ),
                step_h=self.chauffage_predictif_mpc_pas_h,
                state_step_c=self.chauffage_predictif_mpc_pas_temperature_c,
                turbo_penalty_kwh_per_h=(
                    self.chauffage_predictif_mpc_penalite_turbo_kwh_h
                ),
                night_penalty_kwh_per_h=(
                    self.chauffage_predictif_mpc_penalite_nuit_kwh_h
                ),
                weekend_bonus=self.chauffage_predictif_bonus_weekend,
            )
        return build_predictive_plan(**common)

    @staticmethod
    def _iso_date(value):
        return value.isoformat() if isinstance(value, datetime.date) else None

    @staticmethod
    def _iso_datetime(value):
        return value.isoformat() if isinstance(value, datetime.datetime) else None


    def _forced_heating_timer_progress(self, now=None):
        """Return forced-heating deadline + live remaining seconds.

        Prefer the internal Turbo deadline because Home Assistant's timer
        `remaining` attribute is not a live countdown. Fall back to the timer's
        `finishes_at` timestamp after an AppDaemon restart.
        """
        now = now or datetime.datetime.now()
        deadline = getattr(self, "chauffage_turbo_ends_at", None)

        if not isinstance(deadline, datetime.datetime):
            entity = getattr(self, "entity_chauffage_timer", None)
            if entity:
                try:
                    if self.get_state(entity) == "active":
                        deadline = self._parse_datetime(
                            self.get_state(entity, attribute="finishes_at")
                        )
                except Exception:
                    deadline = None

        if not isinstance(deadline, datetime.datetime):
            return None, None

        try:
            compare_now = now
            if deadline.tzinfo is not None and compare_now.tzinfo is None:
                compare_now = datetime.datetime.now(deadline.tzinfo)
            elif deadline.tzinfo is None and compare_now.tzinfo is not None:
                compare_now = compare_now.replace(tzinfo=None)
            remaining = max(
                0,
                int((deadline - compare_now).total_seconds()),
            )
        except (TypeError, ValueError):
            return self._iso_datetime(deadline), None

        return self._iso_datetime(deadline), remaining

    def _predictive_status_state(self, plan, kind, override=None):
        if override:
            return override
        if self.chauffage_predictif_measurement_active:
            return "🌀 Stabilisation mesure température"
        action = (plan or {}).get("action")
        if action == "PREHEAT":
            return f"🔥 Préparation {(plan or {}).get('preset') or 'Smart'}"
        if action == "PRESERVE":
            return "🌡️ Préservation"
        if action == "MAINTAIN":
            return "🏊 Maintien baignade"
        candidate = (plan or {}).get("candidate") or {}
        if candidate.get("date") is not None:
            return f"⏸ Attente {candidate['date'].strftime('%a %d/%m')}"
        if kind == "end_season":
            return "⏸ Fin de saison"
        return "⏸ Veille météo"

    def _publish_predictive_score(self, plan):
        """Publish the selected bathing opportunity score as a HA sensor."""
        entity = getattr(self, "entity_chauffage_predictif_score", None)
        if not entity:
            return

        candidate = (plan or {}).get("candidate") or {}
        usage_score = candidate.get("usage_score")
        try:
            state = (
                round(float(usage_score), 1)
                if usage_score is not None
                else "unknown"
            )
        except (TypeError, ValueError):
            state = "unknown"

        attributes = {
            "friendly_name": "Piscine • Score baignade",
            "icon": "mdi:star-outline",
            "score_type": "usage_score",
            "minimum_score": round(
                float(self.chauffage_predictif_score_baignade_min),
                1,
            ),
            "date": self._iso_date(candidate.get("date")),
            "usage_score": candidate.get("usage_score"),
            "weather_score": candidate.get("score"),
            "strategic_score": candidate.get("strategic_score"),
            "weekend": candidate.get("weekend"),
            "usage_window": candidate.get("usage_window"),
            "confidence": candidate.get("confidence"),
        }

        try:
            self.set_state(
                entity,
                state=state,
                attributes=attributes,
                replace=True,
            )
            self._recover("chauffage_predictif_score")
        except Exception as exc:
            self._fault(
                "chauffage_predictif_score",
                f"publication score baignade impossible: {exc}",
            )

    def _publish_predictive_status(
        self,
        *,
        plan,
        forecast,
        kind,
        water,
        target,
        override=None,
    ):
        self._publish_predictive_score(plan)

        if not self.entity_chauffage_predictif_status:
            return

        candidate = (plan or {}).get("candidate") or {}
        status_now = datetime.datetime.now()
        measurement_elapsed_s, measurement_remaining_s, measurement_phase = (
            self._measurement_progress(status_now)
        )
        forced_timer_ends_at, forced_timer_remaining_s = (
            self._forced_heating_timer_progress(status_now)
        )
        rows = dashboard_forecast(forecast, plan or {}, status_now)
        attributes = {
            "friendly_name": "Piscine chauffage prédictif",
            "icon": "mdi:pool-thermometer",
            "mode": kind,
            "enabled": bool(self.chauffage_predictif),
            "action": (plan or {}).get("action"),
            "water_temperature_estimated": (
                round(float(water), 2) if water is not None else None
            ),
            "certified_water_temperature": (
                round(float(self.chauffage_predictif_certified_water_c), 2)
                if self.chauffage_predictif_certified_water_c is not None
                else None
            ),
            "certified_water_at": self._iso_datetime(
                self.chauffage_predictif_certified_at
            ),
            "measurement_active": bool(
                self.chauffage_predictif_measurement_active
            ),
            "measurement_purpose": self.chauffage_predictif_measurement_purpose,
            "measurement_requested_at": self._iso_datetime(
                self.chauffage_predictif_measurement_requested_at
            ),
            "measurement_reference_speed_pct": (
                self.chauffage_predictif_mesure_vitesse_pct
            ),
            "measurement_reference_seconds": self._measurement_tempo_eau_s(),
            "measurement_stability_seconds": (
                self.chauffage_predictif_mesure_stabilite_s
            ),
            "measurement_max_variation_c": (
                self.chauffage_predictif_mesure_variation_max_c
            ),
            "measurement_elapsed_seconds": measurement_elapsed_s,
            "measurement_remaining_seconds": measurement_remaining_s,
            "measurement_phase": measurement_phase,
            "forced_heating_ends_at": forced_timer_ends_at,
            "forced_heating_remaining_seconds": forced_timer_remaining_s,
            "current_cover": self._predictive_cover_state(),
            "target_temperature": (
                round(float(target), 1) if target is not None else None
            ),
            "planner": (plan or {}).get(
                "planner",
                "MPC" if self.chauffage_predictif_mpc else "trajectory",
            ),
            "adaptive_model": bool((plan or {}).get("adaptive_model")),
            "model_confidence": (plan or {}).get("model_confidence"),
            "floor_temperature": (plan or {}).get("floor_c"),
            "adaptive_floor_temperature": (plan or {}).get("adaptive_floor_c"),
            "trajectory_target_temperature": (plan or {}).get(
                "trajectory_target_c"
            ),
            "heating_now": bool((plan or {}).get("should_heat")),
            "heat_target_temperature": (plan or {}).get("heat_target_c"),
            "recommended_preset": (plan or {}).get("preset"),
            "night_heating": bool((plan or {}).get("night_heating")),
            "night_required_c": (plan or {}).get("night_required_c"),
            "reason": (plan or {}).get("reason"),
            "next_swim_date": self._iso_date(candidate.get("date")),
            "next_swim_score": candidate.get("score"),
            "next_swim_usage_score": candidate.get("usage_score"),
            "next_swim_weekend": candidate.get("weekend"),
            "next_swim_usage_window": candidate.get("usage_window"),
            "next_swim_confidence": candidate.get("confidence"),
            "recovery_start_date": self._iso_date(
                (plan or {}).get("recovery_start_date")
            ),
            "required_gain_c": (plan or {}).get("required_gain_c"),
            "predicted_night_loss_c": (plan or {}).get("predicted_loss_c"),
            "projected_without_heat_c": (plan or {}).get(
                "projected_without_heat_c"
            ),
            "future_smart_capacity_c": (plan or {}).get(
                "future_smart_capacity_c"
            ),
            "thermal_margin_c": (plan or {}).get("thermal_margin_c"),
            "mpc_energy_kwh": (plan or {}).get("mpc_energy_kwh"),
            "mpc_horizon_energy_kwh": (plan or {}).get(
                "mpc_horizon_energy_kwh",
                (plan or {}).get("mpc_energy_kwh"),
            ),
            "mpc_next_swim_energy_kwh": (plan or {}).get(
                "mpc_next_swim_energy_kwh"
            ),
            "swim_dates": [
                self._iso_date(item)
                for item in ((plan or {}).get("swim_dates") or [])
                if isinstance(item, datetime.date)
            ],
            "swim_opportunities": [
                {
                    **item,
                    "date": self._iso_date(item.get("date")),
                }
                for item in ((plan or {}).get("opportunities") or [])
                if isinstance(item, dict)
            ],
            "mpc_night_energy_required": bool(
                (plan or {}).get("mpc_night_energy_required")
            ),
            "mpc_plan": [
                {
                    **item,
                    "date": self._iso_date(item.get("date"))
                    if isinstance(item, dict)
                    else None,
                }
                for item in ((plan or {}).get("mpc_plan") or [])
                if isinstance(item, dict)
            ],
            "forecast_horizon_days": self.chauffage_predictif_horizon_jours,
            "forecast": rows,
            "learned_heating_rates": self.chauffage_predictif_rate_model,
            "learned_night_losses": self.chauffage_predictif_loss_model,
            "expected_night_cover": self._predictive_expected_night_cover_state(),
            "forecast_updated_at": self._iso_datetime(
                self.chauffage_predictif_forecast_at
            ),
        }

        state = self._predictive_status_state(plan, kind, override=override)
        signature = (
            state,
            attributes.get("action"),
            attributes.get("water_temperature_estimated"),
            attributes.get("certified_water_temperature"),
            attributes.get("measurement_active"),
            attributes.get("measurement_remaining_seconds"),
            attributes.get("measurement_phase"),
            attributes.get("forced_heating_remaining_seconds"),
            attributes.get("current_cover"),
            attributes.get("target_temperature"),
            attributes.get("heating_now"),
            attributes.get("recommended_preset"),
            attributes.get("next_swim_date"),
            attributes.get("planner"),
            attributes.get("adaptive_floor_temperature"),
            attributes.get("trajectory_target_temperature"),
            attributes.get("mpc_energy_kwh"),
            attributes.get("swim_dates"),
            attributes.get("mpc_plan"),
            attributes.get("reason"),
            attributes.get("forecast_updated_at"),
        )
        if signature == self.chauffage_predictif_last_status_signature:
            return

        try:
            # Replace the attribute set atomically. AppDaemon/Home Assistant
            # otherwise merges attributes and can leave obsolete v0.6.x fields
            # such as schedule/heating_slots attached to the virtual sensor.
            self.set_state(
                self.entity_chauffage_predictif_status,
                state=state,
                attributes=attributes,
                replace=True,
            )
            self.chauffage_predictif_last_status_signature = signature
            self._recover("chauffage_predictif_status")
        except Exception as exc:
            self._fault(
                "chauffage_predictif_status",
                f"publication diagnostic prédictif impossible: {exc}",
            )

    def _log_predictive_plan(self, plan, water, target, kind):
        candidate = (plan or {}).get("candidate") or {}
        signature = (
            kind,
            (plan or {}).get("action"),
            (plan or {}).get("preset"),
            bool((plan or {}).get("night_heating")),
            candidate.get("date"),
            (plan or {}).get("recovery_start_date"),
            round(float((plan or {}).get("trajectory_target_c") or 0.0), 1),
            str((plan or {}).get("reason") or ""),
        )
        if signature == self.chauffage_predictif_last_log_signature:
            return

        self.chauffage_predictif_last_log_signature = signature
        try:
            self.log(
                f"Chauffage prédictif [{kind}]: "
                f"eau ~{water:.1f}/{target:.1f} °C | "
                f"{(plan or {}).get('action', 'WAIT')} | "
                f"{(plan or {}).get('reason', '')}",
                log="piscine_log",
            )
        except Exception:
            pass

    def _candidate_within_measurement_horizon(self, plan):
        candidate = (plan or {}).get("candidate") or {}
        day = candidate.get("date")
        if not isinstance(day, datetime.date):
            return False
        delta = (day - datetime.datetime.now().date()).days
        return 0 <= delta <= self.chauffage_predictif_mesure_anticipation_jours

    def _measurement_required_before_action(self, plan):
        if not (plan or {}).get("should_heat"):
            return False
        now = datetime.datetime.now()
        if self._certified_fresh(now, same_day=True):
            return False
        if self._measurement_retry_blocked(now):
            return False
        return True

    def _maybe_measure_during_normal_filtration(self, plan):
        if self.chauffage_predictif_measurement_active:
            return False
        now = datetime.datetime.now()
        if self._certified_fresh(now, same_day=True):
            return False
        if self._measurement_retry_blocked(now):
            return False
        if not self.pompe_est_on():
            return False
        if not self._predictive_daylight_active():
            return False
        if not self._candidate_within_measurement_horizon(plan):
            return False
        return self._request_predictive_measurement(
            "morning_decision",
            start_pump=False,
        )

    def _update_predictive_diagnostics(self, kind, override=None):
        if not self.chauffage_predictif:
            return None

        forecast = self._refresh_predictive_forecast()
        water = self._predictive_water_temperature()
        target = self._pac_target_temperature()
        if water is None or target is None:
            self._publish_predictive_status(
                plan={},
                forecast=forecast,
                kind=kind,
                water=water,
                target=target,
                override=override or "⚠️ Température indisponible",
            )
            return None

        plan = self._build_runtime_predictive_plan(
            kind if kind == "end_season" else "auto",
            water,
            target,
            forecast,
        )
        self._publish_predictive_status(
            plan=plan,
            forecast=forecast,
            kind=kind,
            water=water,
            target=target,
            override=override,
        )
        return plan

    def _manage_predictive_heating(self, kind):
        forecast = self._refresh_predictive_forecast()
        water = self._predictive_water_temperature()
        target = self._pac_target_temperature()

        if water is None or target is None:
            self.chauffage_predictif_heat_requested = False
            self.chauffage_predictif_heat_target_c = None
            self._cancel_chauffage_start()
            self._fault(
                "chauffage_predictif_temperature",
                "température eau/consigne PAC indisponible; chauffage arrêté",
            )
            self._publish_predictive_status(
                plan={},
                forecast=forecast,
                kind=kind,
                water=water,
                target=target,
                override="⚠️ Température indisponible",
            )
            return self._pac_off(
                "chauffage prédictif: température indisponible"
            )

        self._recover("chauffage_predictif_temperature")
        plan = self._build_runtime_predictive_plan(
            kind,
            water,
            target,
            forecast,
        )
        self.chauffage_predictif_last_plan = plan

        # WAIT/PRESERVE-without-heat must stop the PAC immediately. Temperature
        # sampling only piggybacks on circulation that is already running; it
        # never owns pump speed and never creates an autonomous pump cycle.
        if not plan.get("should_heat"):
            self.chauffage_predictif_heat_requested = False
            self.chauffage_predictif_heat_target_c = None
            self._cancel_chauffage_start()

            if self._pac_power_active():
                self._pac_off(
                    f"chauffage prédictif: {plan.get('reason', 'attente')}",
                    post=True,
                )

            if (
                self.chauffage_predictif_measurement_active
                and self.chauffage_predictif_measurement_purpose
                in {"heating_learning", "target_check"}
            ):
                self.chauffage_predictif_measurement_purpose = (
                    "temperature_stabilization"
                )

            # If normal filtration is already running and a selected bathing
            # window is close, use pump-only circulation to refresh the pool
            # temperature. Never start the PAC for that measurement.
            self._maybe_measure_during_normal_filtration(plan)

            self._log_predictive_plan(plan, water, target, kind)
            self._publish_predictive_status(
                plan=plan,
                forecast=forecast,
                kind=kind,
                water=water,
                target=target,
            )
            return

        # A real heat request may need a fresh pool temperature. Starting the
        # circulation is allowed here because heating itself requires flow; the
        # measurement is never allowed to create an independent pump cycle.
        self._maybe_measure_during_normal_filtration(plan)

        if self._measurement_required_before_action(plan):
            self.chauffage_predictif_heat_requested = True
            self.chauffage_predictif_heat_target_c = (
                plan.get("heat_target_c") or target
            )

            # A decision calibration must be pump-only. This is especially
            # important when leaving a forced Turbo mode: do not let the old
            # preset keep heating while the MPC waits for a certified sample.
            try:
                self._pac_off(
                    "chauffage prédictif: mesure température avant décision",
                    post=False,
                )
            except Exception:
                pass

            if not self.pompe_est_on():
                try:
                    self.turn_on_pompe_mem(reason="mesure_temperature")
                except Exception:
                    pass

            self._request_predictive_measurement(
                "decision",
                start_pump=False,
            )
            self._publish_predictive_status(
                plan=plan,
                forecast=forecast,
                kind=kind,
                water=water,
                target=target,
                override="🌀 Stabilisation température au démarrage",
            )
            return

        self._log_predictive_plan(plan, water, target, kind)
        self._publish_predictive_status(
            plan=plan,
            forecast=forecast,
            kind=kind,
            water=water,
            target=target,
        )

        self.chauffage_predictif_heat_requested = True
        self.chauffage_predictif_heat_target_c = (
            plan.get("heat_target_c") or target
        )
        kind_label = {
            "auto": "automatique",
            "end_season": "fin de saison",
        }.get(kind, kind)
        action_label = {
            "PREHEAT": "préchauffage",
            "MAINTAIN": "maintien baignade",
            "PRESERVE": "préservation",
            "WAIT": "attente",
        }.get(plan.get("action"), str(plan.get("action") or "chauffage"))
        return self._request_chauffage_start(
            plan.get("preset") or self.chauffage_preset_smart,
            f"{kind_label} • {action_label}",
        )

    def _predictive_start_still_allowed(self, kind):
        if kind in {"season_start", "turbo"}:
            return True
        if kind == "end_season":
            if not self.chauffage_predictif:
                return True
            return bool(self.chauffage_predictif_heat_requested)
        if kind == "auto" and self.chauffage_predictif:
            if hasattr(self, "mode_auto_autorise") and not self.mode_auto_autorise():
                return False
            return bool(self.chauffage_predictif_heat_requested)
        return False
