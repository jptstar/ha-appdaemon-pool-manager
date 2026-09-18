# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Runtime integration for weather-aware, self-learning pool heating."""

import datetime
import json
import os

from pool_predictive import (
    build_predictive_plan,
    dashboard_forecast,
    extract_weather_forecast,
    normalize_cover_state,
    normalize_daily_forecast,
    update_heating_rate_model,
    update_loss_model,
)


class PredictiveHeatingSupport:
    """Weather forecast + persistent thermal learning shared by heating modes."""

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

    def _predictive_arg(self, new_key, legacy_key=None, default=None):
        if new_key in self.args:
            return self.args.get(new_key)
        if legacy_key and legacy_key in self.args:
            return self.args.get(legacy_key)
        return default

    def _initialize_predictive_heating(self):
        enabled_value = self._predictive_arg(
            "chauffage_predictif",
            "fin_saison_predictif",
            "false",
        )
        self.chauffage_predictif = self._bool_value(enabled_value)

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

        # Fallback only until the installation has learned enough real samples.
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

        # Optional absolute minimum water temperature. When absent, the existing
        # Auto / End-of-season deltas remain the compatibility fallback.
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
            int(
                float(
                    self.args.get(
                        "chauffage_predictif_apprentissage_min_s",
                        1800,
                    )
                )
            ),
        )
        self.chauffage_predictif_apprentissage_alpha = max(
            0.05,
            min(
                1.0,
                float(
                    self.args.get(
                        "chauffage_predictif_apprentissage_alpha",
                        0.25,
                    )
                ),
            ),
        )

        # Stored one level above the HACS package so package updates do not erase
        # weeks of thermal learning.
        default_learning_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "pool_manager_thermal_learning.json",
        )
        self.chauffage_predictif_learning_file = str(
            self.args.get(
                "chauffage_predictif_learning_file",
                default_learning_file,
            )
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
        self._heating_learning_session = None
        self._night_learning_session = None
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
            raw = extract_weather_forecast(
                result,
                self.entity_meteo_chauffage_predictif,
            )
            normalized = normalize_daily_forecast(
                raw,
                horizon_days=self.chauffage_predictif_horizon_jours,
                min_air_c=self.chauffage_predictif_temperature_baignade_min_c,
                ideal_air_c=self.chauffage_predictif_temperature_baignade_ideale_c,
            )
            if not normalized:
                raise ValueError("réponse météo sans prévisions daily")
            self.chauffage_predictif_forecast = normalized
            self.chauffage_predictif_forecast_at = datetime.datetime.now()
            self._recover(
                "chauffage_predictif_meteo",
                f"{len(normalized)} jours de prévision disponibles",
            )
            return normalized
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

    def _load_predictive_learning(self):
        try:
            with open(
                self.chauffage_predictif_learning_file,
                "r",
                encoding="utf-8",
            ) as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                self.chauffage_predictif_rate_model = dict(
                    payload.get("heating") or {}
                )
                self.chauffage_predictif_loss_model = dict(
                    payload.get("night_loss") or {}
                )
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
        payload = {
            "version": 1,
            "updated_at": datetime.datetime.now().isoformat(),
            "heating": self.chauffage_predictif_rate_model,
            "night_loss": self.chauffage_predictif_loss_model,
        }
        path = self.chauffage_predictif_learning_file
        tmp = f"{path}.tmp"
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                )
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

    def _predictive_water_temperature(self):
        """Use physical water only after representative circulation."""
        raw = None
        try:
            raw = self._raw_float(self.args.get("temperature_eau"))
        except Exception:
            pass

        if self._pump_flow_ok() and getattr(self, "fin_tempo", 0) == 1:
            if raw is not None:
                return raw

        try:
            memory = self._raw_float(self.args.get("mem_temp"))
        except Exception:
            memory = None
        return memory if memory is not None else raw

    def _predictive_physical_water(self):
        if not self._pump_flow_ok() or getattr(self, "fin_tempo", 0) != 1:
            return None
        try:
            return self._raw_float(self.args.get("temperature_eau"))
        except Exception:
            return None

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
                    min(
                        18.0,
                        (end - start).total_seconds() / 3600.0,
                    ),
                )
        except Exception:
            pass
        return 12.0

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
                    return max(
                        0.0,
                        (end - start).total_seconds() / 3600.0,
                    )
                return max(
                    0.0,
                    (end - now).total_seconds() / 3600.0,
                )
        except Exception:
            pass

        now = datetime.datetime.now()
        if now.hour < 8:
            return 12.0
        if now.hour >= 20:
            return 0.0
        return max(
            0.0,
            20.0 - (now.hour + now.minute / 60.0),
        )

    def _predictive_cover_state(self):
        entity = getattr(self, "entity_volet_piscine", None)
        if not entity:
            return "unknown"
        try:
            return normalize_cover_state(self.get_state(entity))
        except Exception:
            return "unknown"

    def _predictive_pac_preset(self):
        try:
            value = self.get_state(
                self.entity_pac_climate,
                attribute="preset_mode",
            )
            return str(value or self.chauffage_preset_smart)
        except Exception:
            return self.chauffage_preset_smart

    def _learn_heating_rate(self, now, water, ambient):
        active = self._pac_power_active() and water is not None
        if not active:
            self._heating_learning_session = None
            return

        preset = self._predictive_pac_preset()
        session = self._heating_learning_session
        if session is None or session.get("preset") != preset:
            self._heating_learning_session = {
                "started_at": now,
                "water": water,
                "preset": preset,
                "ambient_sum": float(ambient or 0.0),
                "ambient_count": 1 if ambient is not None else 0,
            }
            return

        if ambient is not None:
            session["ambient_sum"] += float(ambient)
            session["ambient_count"] += 1

        elapsed_s = (now - session["started_at"]).total_seconds()
        if elapsed_s < self.chauffage_predictif_apprentissage_min_s:
            return

        rate = (
            float(water) - float(session["water"])
        ) / (elapsed_s / 3600.0)
        avg_ambient = (
            session["ambient_sum"] / session["ambient_count"]
            if session["ambient_count"]
            else ambient
        )
        before = self.chauffage_predictif_rate_model
        learned = update_heating_rate_model(
            before,
            preset,
            avg_ambient,
            rate,
            alpha=self.chauffage_predictif_apprentissage_alpha,
        )
        if learned != before:
            self.chauffage_predictif_rate_model = learned
            self._save_predictive_learning()
            try:
                self.log(
                    f"Apprentissage PAC {preset}: {rate:.3f} °C/h "
                    f"à {avg_ambient if avg_ambient is not None else '?'} °C",
                    log="piscine_log",
                )
            except Exception:
                pass

        self._heating_learning_session = {
            "started_at": now,
            "water": water,
            "preset": preset,
            "ambient_sum": float(ambient or 0.0),
            "ambient_count": 1 if ambient is not None else 0,
        }

    def _learn_night_loss(self, now, water, ambient):
        daylight = self._predictive_daylight_active()
        pac_active = self._pac_power_active()
        cover = self._predictive_cover_state()
        session = self._night_learning_session

        if not daylight:
            if pac_active:
                self._night_learning_session = None
                return
            if session is None:
                reference = self._predictive_water_temperature()
                if reference is None:
                    return
                self._night_learning_session = {
                    "started_at": now,
                    "ended_at": None,
                    "water": float(reference),
                    "cover": cover,
                    "cover_valid": True,
                    "ambient_sum": float(ambient or 0.0),
                    "ambient_count": 1 if ambient is not None else 0,
                }
                return
            if session.get("ended_at") is None:
                if cover != session.get("cover"):
                    session["cover_valid"] = False
                if ambient is not None:
                    session["ambient_sum"] += float(ambient)
                    session["ambient_count"] += 1
            return

        if session is None:
            return

        # Any daytime PAC run before the first representative post-night water
        # measurement would contaminate the passive-loss sample.
        if pac_active:
            self._night_learning_session = None
            return

        if session.get("ended_at") is None:
            session["ended_at"] = now

        # Wait for the first representative physical water measurement after
        # sunrise; discard the sample if circulation comes much too late.
        if water is None:
            if (
                now - session["ended_at"]
            ).total_seconds() > 3 * 3600:
                self._night_learning_session = None
            return

        duration_s = (
            session["ended_at"] - session["started_at"]
        ).total_seconds()
        if (
            duration_s < 4 * 3600
            or not session.get("cover_valid", True)
        ):
            self._night_learning_session = None
            return

        loss_rate = (
            float(session["water"]) - float(water)
        ) / (duration_s / 3600.0)
        avg_ambient = (
            session["ambient_sum"] / session["ambient_count"]
            if session["ambient_count"]
            else ambient
        )
        before = self.chauffage_predictif_loss_model
        learned = update_loss_model(
            before,
            session.get("cover"),
            session["water"],
            avg_ambient,
            loss_rate,
            alpha=self.chauffage_predictif_apprentissage_alpha,
        )
        if learned != before:
            self.chauffage_predictif_loss_model = learned
            self._save_predictive_learning()
            try:
                self.log(
                    f"Apprentissage pertes nuit: {loss_rate:.3f} °C/h "
                    f"(air {avg_ambient if avg_ambient is not None else '?'} °C, "
                    f"volet {session.get('cover')})",
                    log="piscine_log",
                )
            except Exception:
                pass
        self._night_learning_session = None

    def _update_predictive_learning(self):
        if not self.chauffage_predictif_apprentissage:
            return

        now = datetime.datetime.now()
        water = self._predictive_physical_water()
        ambient = self._raw_float(
            getattr(self, "entity_temperature_exterieure", None)
        )
        self._learn_heating_rate(now, water, ambient)
        self._learn_night_loss(now, water, ambient)

    # --------------------------- planning + status ---------------------------

    def _predictive_profile_floor_delta(self, kind):
        if kind == "end_season":
            return self.chauffage_predictif_plancher_fin_saison_delta_c
        return self.chauffage_predictif_plancher_auto_delta_c

    def _build_runtime_predictive_plan(
        self,
        kind,
        water,
        target,
        forecast,
    ):
        day_hours = self._predictive_daylight_hours()
        remaining = self._predictive_daylight_hours_remaining()
        return build_predictive_plan(
            now=datetime.datetime.now(),
            water_c=water,
            target_c=target,
            forecast=forecast,
            base_heating_rate_c_per_h=(
                self.chauffage_predictif_gain_chauffe_c_par_h
            ),
            heating_rate_model=self.chauffage_predictif_rate_model,
            loss_model=self.chauffage_predictif_loss_model,
            cover_state=self._predictive_cover_state(),
            floor_delta_c=self._predictive_profile_floor_delta(kind),
            minimum_water_c=(
                self.chauffage_predictif_temperature_min_eau_c
            ),
            floor_recharge_c=self.chauffage_predictif_recharge_plancher_c,
            stop_margin_c=self.chauffage_predictif_marge_arret_c,
            score_min=self.chauffage_predictif_score_baignade_min,
            min_air_c=self.chauffage_predictif_temperature_baignade_min_c,
            smart_preset=self.chauffage_preset_smart,
            turbo_preset=self.chauffage_preset_turbo,
            day_hours=day_hours,
            today_day_hours_remaining=remaining,
            night_hours=max(6.0, 24.0 - day_hours),
            loss_fallback_delta10_c_per_h=(
                self.chauffage_predictif_perte_nuit_delta10_c_par_h
            ),
            daylight_active=self._predictive_daylight_active(),
        )

    @staticmethod
    def _iso_date(value):
        return (
            value.isoformat()
            if isinstance(value, datetime.date)
            else None
        )

    @staticmethod
    def _iso_datetime(value):
        return (
            value.isoformat()
            if isinstance(value, datetime.datetime)
            else None
        )

    def _predictive_status_state(
        self,
        plan,
        kind,
        override=None,
    ):
        if override:
            return override
        if plan and plan.get("should_heat"):
            preset = plan.get("preset") or self.chauffage_preset_smart
            return f"🔥 {preset}"
        candidate = (plan or {}).get("candidate") or {}
        if candidate.get("date") is not None:
            return f"🏊 {candidate['date'].strftime('%a %d/%m')}"
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
        rows = dashboard_forecast(
            forecast,
            plan or {},
            datetime.datetime.now(),
        )
        attributes = {
            "friendly_name": "Piscine chauffage prédictif",
            "icon": "mdi:pool-thermometer",
            "mode": kind,
            "enabled": bool(self.chauffage_predictif),
            "water_temperature": (
                round(float(water), 1)
                if water is not None
                else None
            ),
            "target_temperature": (
                round(float(target), 1)
                if target is not None
                else None
            ),
            "floor_temperature": (plan or {}).get("floor_c"),
            "heating_now": bool((plan or {}).get("should_heat")),
            "heat_target_temperature": (plan or {}).get(
                "heat_target_c"
            ),
            "recommended_preset": (plan or {}).get("preset"),
            "night_heating_allowed": bool(
                (plan or {}).get("allow_night")
            ),
            "reason": (plan or {}).get("reason"),
            "next_swim_date": self._iso_date(candidate.get("date")),
            "next_swim_score": candidate.get("score"),
            "next_swim_strategic_score": candidate.get(
                "strategic_score"
            ),
            "next_swim_confidence": candidate.get("confidence"),
            "next_swim_condition": candidate.get("condition"),
            "recovery_start_date": self._iso_date(
                (plan or {}).get("recovery_start_date")
            ),
            "required_gain_c": (plan or {}).get("required_gain_c"),
            "predicted_night_loss_c": (plan or {}).get(
                "predicted_loss_c"
            ),
            "projected_without_heat_c": (plan or {}).get(
                "projected_without_heat_c"
            ),
            "estimated_capacity_c": (plan or {}).get(
                "estimated_capacity_c"
            ),
            "forecast_horizon_days": (
                self.chauffage_predictif_horizon_jours
            ),
            "forecast": rows,
            "learned_heating_rates": (
                self.chauffage_predictif_rate_model
            ),
            "learned_night_losses": (
                self.chauffage_predictif_loss_model
            ),
            "forecast_updated_at": self._iso_datetime(
                self.chauffage_predictif_forecast_at
            ),
        }

        state = self._predictive_status_state(
            plan,
            kind,
            override=override,
        )
        signature = (
            state,
            attributes.get("water_temperature"),
            attributes.get("target_temperature"),
            attributes.get("heating_now"),
            attributes.get("recommended_preset"),
            attributes.get("night_heating_allowed"),
            attributes.get("next_swim_date"),
            attributes.get("recovery_start_date"),
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

    def _log_predictive_plan(
        self,
        plan,
        water,
        target,
        kind,
    ):
        candidate = (plan or {}).get("candidate") or {}
        signature = (
            kind,
            bool((plan or {}).get("should_heat")),
            (plan or {}).get("preset"),
            bool((plan or {}).get("allow_night")),
            candidate.get("date"),
            (plan or {}).get("recovery_start_date"),
            str((plan or {}).get("reason") or ""),
        )
        if signature == self.chauffage_predictif_last_log_signature:
            return

        self.chauffage_predictif_last_log_signature = signature
        try:
            self.log(
                f"Chauffage prédictif [{kind}]: "
                f"eau {water:.1f}/{target:.1f} °C | "
                f"{(plan or {}).get('reason', '')}",
                log="piscine_log",
            )
        except Exception:
            pass

    def _update_predictive_diagnostics(
        self,
        kind,
        override=None,
    ):
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
                override=(
                    override
                    or "⚠️ Température indisponible"
                ),
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
                "température eau/consigne PAC indisponible; "
                "chauffage arrêté",
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
        self._log_predictive_plan(
            plan,
            water,
            target,
            kind,
        )
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
                plan.get("heat_target_c")
                or target
            )
            preset = (
                plan.get("preset")
                or self.chauffage_preset_smart
            )
            return self._request_chauffage_start(
                preset,
                f"prédictif {kind}",
            )

        self.chauffage_predictif_heat_requested = False
        self.chauffage_predictif_heat_target_c = None
        self._cancel_chauffage_start()
        return self._pac_off(
            f"chauffage prédictif: "
            f"{plan.get('reason', 'attente')}",
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
            if (
                hasattr(self, "mode_auto_autorise")
                and not self.mode_auto_autorise()
            ):
                return False
            return bool(self.chauffage_predictif_heat_requested)
        return False
