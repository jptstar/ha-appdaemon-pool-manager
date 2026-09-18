# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Runtime integration for self-learning adaptive pool heating."""

import datetime
import json
import os

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

        # A water measurement is "certified" only after enough real mixing.
        # 47% remains the hydraulic low limit; certification deliberately uses
        # the installation's stronger 70% reference speed.
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
        self.chauffage_predictif_measurement_started_at = None
        self.chauffage_predictif_measurement_stable_at = None
        self.chauffage_predictif_measurement_stable_temp = None

        self._heating_learning_session = None
        self._passive_learning_session = None
        self._last_certification_processed_at = None
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

    def _reset_measurement_tracker(self, keep_request=False):
        self.chauffage_predictif_measurement_started_at = None
        self.chauffage_predictif_measurement_stable_at = None
        self.chauffage_predictif_measurement_stable_temp = None
        if not keep_request:
            self.chauffage_predictif_measurement_active = False
            self.chauffage_predictif_measurement_purpose = None

    def _request_predictive_measurement(self, purpose, start_pump=False):
        if not self.chauffage_predictif:
            return False
        if self.arret_force_actif():
            return False

        if not self.chauffage_predictif_measurement_active:
            self.chauffage_predictif_measurement_active = True
            self.chauffage_predictif_measurement_purpose = str(purpose)
            self._reset_measurement_tracker(keep_request=True)

        if start_pump and not self.pompe_est_on():
            try:
                self.turn_on_pompe_mem()
            except Exception:
                return False

        if self.pompe_est_on():
            try:
                self.set_pump_percentage(
                    self.chauffage_predictif_mesure_vitesse_pct,
                    force=True,
                )
            except Exception:
                pass
        return True

    def _measurement_is_decision_gate(self):
        return self.chauffage_predictif_measurement_purpose in {
            "decision",
            "morning_decision",
        }

    def _register_certified_measurement(self, now, water):
        previous_at = self.chauffage_predictif_certified_at
        self.chauffage_predictif_certified_water_c = float(water)
        self.chauffage_predictif_certified_at = now
        self._last_certification_processed_at = now

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

        purpose = self.chauffage_predictif_measurement_purpose
        self._reset_measurement_tracker()
        self._save_predictive_learning()

        try:
            self.log(
                f"Température eau certifiée: {water:.2f} °C "
                f"({self.chauffage_predictif_mesure_vitesse_pct}% / "
                f"{self.chauffage_predictif_mesure_tempo_s // 60} min"
                + (f", {purpose}" if purpose else "")
                + ")",
                log="piscine_log",
            )
        except Exception:
            pass

        return previous_at

    def _update_certified_measurement(self, now=None):
        now = now or datetime.datetime.now()
        pump_on = self.pompe_est_on()
        speed = self.get_fan_percentage() if pump_on else None

        # Normal filtration at/above reference speed can certify temperature
        # automatically without creating a special measurement cycle.
        if (
            not self.chauffage_predictif_measurement_active
            and pump_on
            and speed is not None
            and speed >= self.chauffage_predictif_mesure_vitesse_pct
        ):
            self.chauffage_predictif_measurement_active = True
            self.chauffage_predictif_measurement_purpose = "natural_mixing"

        if not self.chauffage_predictif_measurement_active:
            self._reset_measurement_tracker()
            return False

        if not pump_on:
            self._reset_measurement_tracker(keep_request=True)
            return False

        # While an explicit measurement is active, all lower pump commands are
        # clamped in DevicesMixin. Reassert here as a second line of defence.
        if speed is None or speed < self.chauffage_predictif_mesure_vitesse_pct:
            try:
                self.set_pump_percentage(
                    self.chauffage_predictif_mesure_vitesse_pct,
                    force=True,
                )
            except Exception:
                pass
            self._reset_measurement_tracker(keep_request=True)
            return False

        if self.chauffage_predictif_measurement_started_at is None:
            self.chauffage_predictif_measurement_started_at = now
            return False

        elapsed = (
            now - self.chauffage_predictif_measurement_started_at
        ).total_seconds()
        if elapsed < self.chauffage_predictif_mesure_tempo_s:
            return False

        water = self._predictive_physical_water_raw()
        if water is None:
            return False

        if self.chauffage_predictif_measurement_stable_at is None:
            self.chauffage_predictif_measurement_stable_at = now
            self.chauffage_predictif_measurement_stable_temp = float(water)
            return False

        if (
            abs(
                float(water)
                - float(self.chauffage_predictif_measurement_stable_temp)
            )
            > self.chauffage_predictif_mesure_variation_max_c
        ):
            self.chauffage_predictif_measurement_stable_at = now
            self.chauffage_predictif_measurement_stable_temp = float(water)
            return False

        stable_s = (
            now - self.chauffage_predictif_measurement_stable_at
        ).total_seconds()
        if stable_s < self.chauffage_predictif_mesure_stabilite_s:
            return False

        self._register_certified_measurement(now, float(water))
        return True

    # ---------------------------- thermal learning ----------------------------

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
                rate = estimate_heating_rate(
                    self.chauffage_predictif_gain_chauffe_c_par_h,
                    self._predictive_pac_preset(),
                    ambient,
                    learned_model=self.chauffage_predictif_rate_model,
                )
                return water + rate * elapsed_h

            # When circulation is stopped, project passive cooling from the last
            # certified pool temperature. This is an estimate only; the first
            # new 70%/15 min certification corrects it the next day.
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
        return build_predictive_plan(
            now=datetime.datetime.now(),
            water_c=water,
            target_c=target,
            forecast=forecast,
            base_heating_rate_c_per_h=self.chauffage_predictif_gain_chauffe_c_par_h,
            heating_rate_model=self.chauffage_predictif_rate_model,
            loss_model=self.chauffage_predictif_loss_model,
            cover_state=self._predictive_cover_state(),
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

    @staticmethod
    def _iso_date(value):
        return value.isoformat() if isinstance(value, datetime.date) else None

    @staticmethod
    def _iso_datetime(value):
        return value.isoformat() if isinstance(value, datetime.datetime) else None

    def _predictive_status_state(self, plan, kind, override=None):
        if override:
            return override
        if self.chauffage_predictif_measurement_active:
            return "🌀 Mesure eau"
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
        if not self.entity_chauffage_predictif_status:
            return

        candidate = (plan or {}).get("candidate") or {}
        rows = dashboard_forecast(forecast, plan or {}, datetime.datetime.now())
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
            "measurement_reference_speed_pct": (
                self.chauffage_predictif_mesure_vitesse_pct
            ),
            "measurement_reference_seconds": (
                self.chauffage_predictif_mesure_tempo_s
            ),
            "target_temperature": (
                round(float(target), 1) if target is not None else None
            ),
            "floor_temperature": (plan or {}).get("floor_c"),
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
            "forecast_horizon_days": self.chauffage_predictif_horizon_jours,
            "forecast": rows,
            "learned_heating_rates": self.chauffage_predictif_rate_model,
            "learned_night_losses": self.chauffage_predictif_loss_model,
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
            attributes.get("target_temperature"),
            attributes.get("heating_now"),
            attributes.get("recommended_preset"),
            attributes.get("next_swim_date"),
            attributes.get("trajectory_target_temperature"),
            attributes.get("reason"),
            attributes.get("forecast_updated_at"),
        )
        if signature == self.chauffage_predictif_last_status_signature:
            return

        try:
            self.set_state(
                self.entity_chauffage_predictif_status,
                state=state,
                attributes=attributes,
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
        if self._certified_fresh(datetime.datetime.now(), same_day=True):
            return False
        return True

    def _maybe_measure_during_normal_filtration(self, plan):
        if self.chauffage_predictif_measurement_active:
            return False
        if self._certified_fresh(datetime.datetime.now(), same_day=True):
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

        # If normal filtration is already running, use it to obtain the first
        # certified measurement of the day before a nearby bathing opportunity.
        self._maybe_measure_during_normal_filtration(plan)

        # Never start a meaningful PAC recovery from a stale pipe/memory value.
        # First perform the 70% / 15-minute certified mixing cycle.
        if self._measurement_required_before_action(plan):
            self.chauffage_predictif_heat_requested = False
            self.chauffage_predictif_heat_target_c = None
            self._cancel_chauffage_start()
            self._request_predictive_measurement(
                "decision",
                start_pump=True,
            )
            self._publish_predictive_status(
                plan=plan,
                forecast=forecast,
                kind=kind,
                water=water,
                target=target,
                override="🌀 Mesure eau avant décision",
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

        if plan.get("should_heat"):
            self.chauffage_predictif_heat_requested = True
            self.chauffage_predictif_heat_target_c = (
                plan.get("heat_target_c") or target
            )
            return self._request_chauffage_start(
                plan.get("preset") or self.chauffage_preset_smart,
                f"prédictif {kind} {plan.get('action', '')}",
            )

        # If the PAC is currently active and the model only *estimates* that the
        # trajectory target has been reached, certify the real pool temperature
        # before switching it off. This avoids stopping on an optimistic model.
        if self._pac_power_active() and not self._certified_fresh(
            datetime.datetime.now(),
            same_day=True,
        ):
            self._request_predictive_measurement(
                "target_check",
                start_pump=False,
            )
            self._publish_predictive_status(
                plan=plan,
                forecast=forecast,
                kind=kind,
                water=water,
                target=target,
                override="🌀 Vérification température",
            )
            return

        self.chauffage_predictif_heat_requested = False
        self.chauffage_predictif_heat_target_c = None
        self._cancel_chauffage_start()
        return self._pac_off(
            f"chauffage prédictif: {plan.get('reason', 'attente')}",
            post=True,
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
