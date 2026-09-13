# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 jptstar

"""Runtime support for the season-wide predictive heating engine."""

import datetime

from pool_predictive import (
    build_predictive_plan,
    dashboard_forecast,
    estimate_heating_rate,
    extract_weather_forecast,
    find_swim_opportunities,
    normalize_daily_forecast,
    update_heating_rate_model,
)


class PredictiveHeatingSupport:
    """Shared weather-aware heating support used by HeatingModeMixin."""

    @staticmethod
    def _bool_value(value, default=False):
        if value is None:
            return bool(default)
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _predictive_arg(self, new_key, legacy_key=None, default=None):
        if new_key in self.args:
            return self.args.get(new_key)
        if legacy_key and legacy_key in self.args:
            return self.args.get(legacy_key)
        return default

    def _initialize_predictive_heating(self):
        # v0.5 fin_saison_predictif remains a migration alias. If it was already
        # enabled, v0.6 upgrades it to the common season-wide predictive engine.
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
        self.chauffage_predictif_horizon_operationnel_jours = max(
            1,
            min(
                7,
                int(
                    float(
                        self.args.get(
                            "chauffage_predictif_horizon_operationnel_jours",
                            3,
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
        self.chauffage_predictif_heure_baignade = self._predictive_arg(
            "chauffage_predictif_heure_baignade",
            "fin_saison_heure_baignade_cible",
            "16:00:00",
        )
        self.chauffage_predictif_heure_eau_prete = self.args.get(
            "chauffage_predictif_heure_eau_prete",
            "11:00:00",
        )

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
        self.chauffage_predictif_marge_planification_h = max(
            0.0,
            float(
                self._predictive_arg(
                    "chauffage_predictif_marge_planification_h",
                    "fin_saison_marge_planification_h",
                    0.5,
                )
            ),
        )
        self.chauffage_predictif_marge_derniere_occasion_h = max(
            self.chauffage_predictif_marge_planification_h,
            float(
                self._predictive_arg(
                    "chauffage_predictif_marge_derniere_occasion_h",
                    "fin_saison_marge_derniere_occasion_h",
                    1.0,
                )
            ),
        )

        self.chauffage_predictif_veille_debut = self._predictive_arg(
            "chauffage_predictif_veille_debut",
            "fin_saison_heure_debut_chauffe",
            "12:00:00",
        )
        self.chauffage_predictif_veille_fin = self._predictive_arg(
            "chauffage_predictif_veille_fin",
            "fin_saison_heure_fin_chauffe",
            "20:00:00",
        )
        self.chauffage_predictif_matin_debut = self.args.get(
            "chauffage_predictif_matin_debut",
            "07:00:00",
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

        self.chauffage_predictif_forecast = []
        self.chauffage_predictif_forecast_at = None
        self.chauffage_predictif_heat_requested = False
        self.chauffage_predictif_heat_target_c = None
        self.chauffage_predictif_last_plan = None
        self.chauffage_predictif_last_log_signature = None
        self.chauffage_predictif_last_status_signature = None
        self.chauffage_predictif_last_rate = self.chauffage_predictif_gain_chauffe_c_par_h

        self.chauffage_predictif_rate_model = {}
        self.chauffage_predictif_learning_started_at = None
        self.chauffage_predictif_learning_water_c = None
        self.chauffage_predictif_learning_ambient_c = None

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
        """Fetch and cache Home Assistant daily forecasts."""
        if not self.entity_meteo_chauffage_predictif:
            self._fault(
                "chauffage_predictif_meteo",
                "chauffage prédictif sans entité météo; réserve thermique seulement",
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
            return self.chauffage_predictif_forecast
        except Exception as exc:
            age = self._predictive_forecast_age_s()
            if (
                self.chauffage_predictif_forecast
                and age is not None
                and age <= self.chauffage_predictif_prevision_max_age_s
            ):
                self._fault(
                    "chauffage_predictif_meteo",
                    f"prévision météo non actualisée ({exc}); cache "
                    f"{age / 3600.0:.1f} h utilisé",
                )
                return self.chauffage_predictif_forecast

            self.chauffage_predictif_forecast = []
            self._fault(
                "chauffage_predictif_meteo",
                f"prévisions météo indisponibles ({exc}); réserve thermique seulement",
            )
            return []

    def _predictive_water_temperature(self):
        """Use physical water only when circulation is representative.

        While stopped, prefer the persisted thermal reference instead of trusting
        a pipe sensor that can remain numerically available but stale.
        """
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
        if memory is not None:
            return memory
        return raw

    def _predictive_profile_floor_delta(self, kind):
        if kind == "end_season":
            return self.chauffage_predictif_plancher_fin_saison_delta_c
        return self.chauffage_predictif_plancher_auto_delta_c

    def _update_predictive_learning(self):
        """Learn real PAC heating speed in broad outdoor-temperature buckets."""
        if not self.chauffage_predictif_apprentissage:
            return

        try:
            active = self._pac_power_active() and self._pump_flow_ok()
        except Exception:
            active = False

        if not active:
            self.chauffage_predictif_learning_started_at = None
            self.chauffage_predictif_learning_water_c = None
            self.chauffage_predictif_learning_ambient_c = None
            return

        try:
            water = self._physical_water_temperature()
        except Exception:
            water = self._raw_float(self.args.get("temperature_eau"))
        ambient = self._raw_float(
            getattr(self, "entity_temperature_exterieure", None)
        )
        if water is None:
            return

        now = datetime.datetime.now()
        if self.chauffage_predictif_learning_started_at is None:
            self.chauffage_predictif_learning_started_at = now
            self.chauffage_predictif_learning_water_c = water
            self.chauffage_predictif_learning_ambient_c = ambient
            return

        elapsed_s = (
            now - self.chauffage_predictif_learning_started_at
        ).total_seconds()
        if elapsed_s < self.chauffage_predictif_apprentissage_min_s:
            return

        start_water = self.chauffage_predictif_learning_water_c
        if start_water is None:
            self.chauffage_predictif_learning_started_at = now
            self.chauffage_predictif_learning_water_c = water
            self.chauffage_predictif_learning_ambient_c = ambient
            return

        rate = (float(water) - float(start_water)) / (elapsed_s / 3600.0)
        sample_ambient = (
            self.chauffage_predictif_learning_ambient_c
            if self.chauffage_predictif_learning_ambient_c is not None
            else ambient
        )
        previous = dict(self.chauffage_predictif_rate_model)
        self.chauffage_predictif_rate_model = update_heating_rate_model(
            previous,
            sample_ambient,
            rate,
            alpha=self.chauffage_predictif_apprentissage_alpha,
        )
        if self.chauffage_predictif_rate_model != previous:
            try:
                self.log(
                    f"Apprentissage PAC: {rate:.3f} °C/h à "
                    f"{sample_ambient if sample_ambient is not None else '?'} °C",
                    log="piscine_log",
                )
            except Exception:
                pass

        self.chauffage_predictif_learning_started_at = now
        self.chauffage_predictif_learning_water_c = water
        self.chauffage_predictif_learning_ambient_c = ambient

    def _estimate_predictive_heating_rate(self, forecast):
        ambient = self._raw_float(
            getattr(self, "entity_temperature_exterieure", None)
        )

        opportunities = find_swim_opportunities(
            forecast,
            datetime.datetime.now(),
            score_min=self.chauffage_predictif_score_baignade_min,
            min_air_c=self.chauffage_predictif_temperature_baignade_min_c,
            swim_time=self.chauffage_predictif_heure_baignade,
        )
        if opportunities:
            candidate = opportunities[0]
            previous_index = max(0, int(candidate["index"]) - 1)
            if previous_index < len(forecast):
                predicted = forecast[previous_index].get("temperature")
                try:
                    ambient = float(predicted)
                except (TypeError, ValueError):
                    pass

        rate = estimate_heating_rate(
            self.chauffage_predictif_gain_chauffe_c_par_h,
            ambient,
            learned_model=self.chauffage_predictif_rate_model,
        )
        self.chauffage_predictif_last_rate = rate
        return rate

    @staticmethod
    def _iso_datetime(value):
        return value.isoformat() if isinstance(value, datetime.datetime) else None

    def _predictive_status_state(self, plan, kind, override=None):
        if override:
            return override
        if plan and plan.get("should_heat"):
            return "🔥 Chauffe maintenant"
        next_segment = (plan or {}).get("next_segment")
        if next_segment:
            return f"🔥 {next_segment['start'].strftime('%a %H:%M')}"
        candidate = (plan or {}).get("candidate")
        if candidate:
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
        rate,
        override=None,
    ):
        if not self.entity_chauffage_predictif_status:
            return

        candidate = (plan or {}).get("candidate") or {}
        active = (plan or {}).get("active_segment") or {}
        next_segment = (plan or {}).get("next_segment") or {}
        schedule = []
        for item in (plan or {}).get("schedule") or []:
            schedule.append(
                {
                    "start": self._iso_datetime(item.get("start")),
                    "end": self._iso_datetime(item.get("end")),
                    "hours": item.get("hours"),
                    "kind": item.get("kind"),
                }
            )

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
                round(float(water), 1) if water is not None else None
            ),
            "target_temperature": (
                round(float(target), 1) if target is not None else None
            ),
            "floor_temperature": (plan or {}).get("floor_c"),
            "heating_rate_c_per_h": round(float(rate), 3) if rate else None,
            "heating_now": bool((plan or {}).get("should_heat")),
            "heat_target_temperature": (plan or {}).get("heat_target_c"),
            "reason": (plan or {}).get("reason"),
            "next_swim_date": (
                candidate.get("date").isoformat()
                if candidate.get("date") is not None
                else None
            ),
            "next_swim_score": candidate.get("score"),
            "next_swim_strategic_score": candidate.get("strategic_score"),
            "next_swim_confidence": candidate.get("confidence"),
            "next_swim_condition": candidate.get("condition"),
            "forecast_horizon_days": self.chauffage_predictif_horizon_jours,
            "operational_horizon_days": self.chauffage_predictif_horizon_operationnel_jours,
            "last_chance": bool(candidate.get("last_chance")),
            "swim_datetime": self._iso_datetime(candidate.get("swim_datetime")),
            "ready_by": self._iso_datetime((plan or {}).get("ready_datetime")),
            "required_hours": (plan or {}).get("required_hours"),
            "scheduled_hours": (plan or {}).get("scheduled_hours"),
            "active_heating_start": self._iso_datetime(active.get("start")),
            "active_heating_end": self._iso_datetime(active.get("end")),
            "next_heating_start": self._iso_datetime(next_segment.get("start")),
            "next_heating_end": self._iso_datetime(next_segment.get("end")),
            "schedule": schedule,
            "forecast": rows,
            "learned_heating_rates": self.chauffage_predictif_rate_model,
            "forecast_updated_at": self._iso_datetime(
                self.chauffage_predictif_forecast_at
            ),
        }

        state = self._predictive_status_state(plan, kind, override=override)
        signature = (
            state,
            attributes.get("water_temperature"),
            attributes.get("target_temperature"),
            attributes.get("heating_now"),
            attributes.get("next_swim_date"),
            attributes.get("next_swim_score"),
            attributes.get("next_heating_start"),
            attributes.get("next_heating_end"),
            attributes.get("reason"),
            tuple(
                (slot.get("start"), slot.get("end"), slot.get("kind"))
                for slot in schedule
            ),
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
        next_segment = (plan or {}).get("next_segment") or {}
        signature = (
            kind,
            bool((plan or {}).get("should_heat")),
            (plan or {}).get("heat_target_c"),
            candidate.get("date"),
            bool(candidate.get("last_chance")),
            next_segment.get("start"),
            str((plan or {}).get("reason") or ""),
        )
        if signature == self.chauffage_predictif_last_log_signature:
            return
        self.chauffage_predictif_last_log_signature = signature
        try:
            self.log(
                f"Chauffage prédictif [{kind}]: eau {water:.1f}/{target:.1f} °C | "
                f"{(plan or {}).get('reason', '')}",
                log="piscine_log",
            )
        except Exception:
            pass

    def _build_runtime_predictive_plan(self, kind, water, target, forecast):
        rate = self._estimate_predictive_heating_rate(forecast)
        plan = build_predictive_plan(
            now=datetime.datetime.now(),
            water_c=water,
            target_c=target,
            forecast=forecast,
            heating_rate_c_per_h=rate,
            floor_delta_c=self._predictive_profile_floor_delta(kind),
            score_min=self.chauffage_predictif_score_baignade_min,
            min_air_c=self.chauffage_predictif_temperature_baignade_min_c,
            swim_time=self.chauffage_predictif_heure_baignade,
            ready_time=self.chauffage_predictif_heure_eau_prete,
            maintenance_band_c=self.chauffage_predictif_recharge_plancher_c,
            stop_margin_c=self.chauffage_predictif_marge_arret_c,
            safety_margin_h=self.chauffage_predictif_marge_planification_h,
            last_chance_margin_h=self.chauffage_predictif_marge_derniere_occasion_h,
            previous_day_start=self.chauffage_predictif_veille_debut,
            previous_day_end=self.chauffage_predictif_veille_fin,
            morning_start=self.chauffage_predictif_matin_debut,
            operational_horizon_days=self.chauffage_predictif_horizon_operationnel_jours,
        )
        return plan, rate

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
                rate=self.chauffage_predictif_last_rate,
                override=override or "⚠️ Température indisponible",
            )
            return None

        plan, rate = self._build_runtime_predictive_plan(
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
            rate=rate,
            override=override,
        )
        return plan

    def _manage_predictive_heating(self, kind):
        """Apply the common predictive engine in Auto and End-of-season modes."""
        forecast = self._refresh_predictive_forecast()
        water = self._predictive_water_temperature()
        target = self._pac_target_temperature()

        if water is None or target is None:
            self.chauffage_predictif_heat_requested = False
            self.chauffage_predictif_heat_target_c = None
            self._cancel_chauffage_start()
            self._fault(
                "chauffage_predictif_temperature",
                "température eau/consigne PAC indisponible; chauffage prédictif arrêté",
            )
            self._publish_predictive_status(
                plan={},
                forecast=forecast,
                kind=kind,
                water=water,
                target=target,
                rate=self.chauffage_predictif_last_rate,
                override="⚠️ Température indisponible",
            )
            return self._pac_off(
                "chauffage prédictif: température indisponible"
            )

        self._recover("chauffage_predictif_temperature")
        plan, rate = self._build_runtime_predictive_plan(
            kind,
            water,
            target,
            forecast,
        )
        self.chauffage_predictif_last_plan = plan
        self._log_predictive_plan(plan, water, target, kind)
        self._publish_predictive_status(
            plan=plan,
            forecast=forecast,
            kind=kind,
            water=water,
            target=target,
            rate=rate,
        )

        if plan.get("should_heat"):
            self.chauffage_predictif_heat_requested = True
            self.chauffage_predictif_heat_target_c = (
                plan.get("heat_target_c") or target
            )
            return self._request_chauffage_start(
                self.chauffage_preset_smart,
                f"prédictif {kind}",
            )

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
