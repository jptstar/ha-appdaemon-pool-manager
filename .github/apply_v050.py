from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected exactly one match in {path}: {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---------------------------------------------------------------------------
# pool_heating.py
# ---------------------------------------------------------------------------
path = "apps/pool_manager/pool_heating.py"

replace_once(
    path,
    "from pool_common import TAB_MODE\n",
    "from pool_common import TAB_MODE\n"
    "from pool_end_season import (\n"
    "    build_end_season_plan,\n"
    "    extract_weather_forecast,\n"
    "    normalize_daily_forecast,\n"
    ")\n",
)

replace_once(
    path,
    '''        self.chauffage_premiere_chauffe_marge_c = float(\n            self.args.get("chauffage_premiere_chauffe_marge_c", 0.3)\n        )\n\n''',
    '''        self.chauffage_premiere_chauffe_marge_c = float(\n            self.args.get("chauffage_premiere_chauffe_marge_c", 0.3)\n        )\n\n        # Predictive end-of-season planner is opt-in for backward compatibility.\n        # With it disabled, Fin de saison keeps the v0.4.4-v0.4.6 24/7 behavior.\n        self.fin_saison_predictif = str(\n            self.args.get("fin_saison_predictif", "false")\n        ).lower() == "true"\n        self.entity_meteo_fin_saison = self.args.get("entity_meteo_fin_saison")\n        self.fin_saison_horizon_jours = max(3, min(10, int(float(\n            self.args.get("fin_saison_horizon_jours", 10)\n        ))))\n        self.fin_saison_prevision_refresh_s = max(300, int(float(\n            self.args.get("fin_saison_prevision_refresh_s", 1800)\n        )))\n        self.fin_saison_prevision_max_age_s = max(\n            self.fin_saison_prevision_refresh_s,\n            int(float(self.args.get("fin_saison_prevision_max_age_s", 21600))),\n        )\n        self.fin_saison_temperature_baignade_min_c = float(\n            self.args.get("fin_saison_temperature_baignade_min_c", 21.0)\n        )\n        self.fin_saison_temperature_baignade_ideale_c = float(\n            self.args.get("fin_saison_temperature_baignade_ideale_c", 26.0)\n        )\n        self.fin_saison_score_baignade_min = float(\n            self.args.get("fin_saison_score_baignade_min", 55.0)\n        )\n        self.fin_saison_heure_baignade_cible = self.args.get(\n            "fin_saison_heure_baignade_cible", "16:00:00"\n        )\n        self.fin_saison_gain_chauffe_c_par_h = max(0.05, float(\n            self.args.get("fin_saison_gain_chauffe_c_par_h", 0.30)\n        ))\n        self.fin_saison_temperature_plancher_delta_c = max(0.0, float(\n            self.args.get("fin_saison_temperature_plancher_delta_c", 3.0)\n        ))\n        self.fin_saison_recharge_plancher_c = max(0.1, float(\n            self.args.get("fin_saison_recharge_plancher_c", 0.5)\n        ))\n        self.fin_saison_marge_arret_c = max(0.0, float(\n            self.args.get("fin_saison_marge_arret_c", 0.2)\n        ))\n        self.fin_saison_marge_planification_h = max(0.0, float(\n            self.args.get("fin_saison_marge_planification_h", 0.5)\n        ))\n        self.fin_saison_marge_derniere_occasion_h = max(\n            self.fin_saison_marge_planification_h,\n            float(self.args.get("fin_saison_marge_derniere_occasion_h", 1.0)),\n        )\n        self.fin_saison_heure_debut_chauffe = self.args.get(\n            "fin_saison_heure_debut_chauffe",\n            self.args.get("heure_debut_pac_auto", "08:00:00"),\n        )\n        self.fin_saison_heure_fin_chauffe = self.args.get(\n            "fin_saison_heure_fin_chauffe",\n            self.args.get("heure_fin_pac_auto", "20:00:00"),\n        )\n\n''',
)

replace_once(
    path,
    '''        self.handle_turbo_fallback = None\n\n        super().initialize()\n''',
    '''        self.handle_turbo_fallback = None\n\n        self.fin_saison_forecast = []\n        self.fin_saison_forecast_at = None\n        self.fin_saison_heat_requested = False\n        self.fin_saison_heat_target_c = None\n        self.fin_saison_last_plan = None\n        self.fin_saison_last_log_signature = None\n\n        super().initialize()\n''',
)

replace_once(
    path,
    '''        elif old_kind == "turbo":\n            self._cancel_turbo_timer()\n\n        self._cancel_chauffage_start()\n        self.safety_tick({})\n''',
    '''        elif old_kind == "turbo":\n            self._cancel_turbo_timer()\n\n        if new_kind == "end_season" and self.fin_saison_predictif:\n            # Force a fresh forecast when the predictive strategy is selected.\n            self.fin_saison_forecast_at = None\n            self.fin_saison_last_log_signature = None\n        if old_kind == "end_season" and new_kind != "end_season":\n            self.fin_saison_heat_requested = False\n            self.fin_saison_heat_target_c = None\n            self.fin_saison_last_plan = None\n\n        self._cancel_chauffage_start()\n        self.safety_tick({})\n''',
)

marker = '''    def _manage_pac_auto(self):\n'''
helpers = '''    def _fin_saison_forecast_age_s(self):\n        if self.fin_saison_forecast_at is None:\n            return None\n        return max(0.0, (datetime.datetime.now() - self.fin_saison_forecast_at).total_seconds())\n\n    def _refresh_fin_saison_forecast(self):\n        """Fetch and cache Home Assistant daily forecasts for the predictive mode."""\n        if not self.entity_meteo_fin_saison:\n            self._fault(\n                "fin_saison_meteo",\n                "Fin de saison prédictif sans entity_meteo_fin_saison; maintien plancher uniquement",\n            )\n            return self.fin_saison_forecast\n\n        age = self._fin_saison_forecast_age_s()\n        if age is not None and age < self.fin_saison_prevision_refresh_s:\n            return self.fin_saison_forecast\n\n        try:\n            result = self.call_service(\n                "weather/get_forecasts",\n                entity_id=self.entity_meteo_fin_saison,\n                type="daily",\n                return_result=True,\n                hass_timeout=10,\n            )\n            raw = extract_weather_forecast(result, self.entity_meteo_fin_saison)\n            normalized = normalize_daily_forecast(\n                raw,\n                horizon_days=self.fin_saison_horizon_jours,\n                min_air_c=self.fin_saison_temperature_baignade_min_c,\n                ideal_air_c=self.fin_saison_temperature_baignade_ideale_c,\n            )\n            if not normalized:\n                raise ValueError("réponse météo sans prévisions daily")\n            self.fin_saison_forecast = normalized\n            self.fin_saison_forecast_at = datetime.datetime.now()\n            self._recover(\n                "fin_saison_meteo",\n                f"{len(normalized)} jours de prévision disponibles",\n            )\n            return self.fin_saison_forecast\n        except Exception as exc:\n            age = self._fin_saison_forecast_age_s()\n            if self.fin_saison_forecast and age is not None and age <= self.fin_saison_prevision_max_age_s:\n                self._fault(\n                    "fin_saison_meteo",\n                    f"prévision météo non actualisée ({exc}); cache {age / 3600.0:.1f} h utilisé",\n                )\n                return self.fin_saison_forecast\n            self.fin_saison_forecast = []\n            self._fault(\n                "fin_saison_meteo",\n                f"prévisions météo indisponibles ({exc}); maintien plancher uniquement",\n            )\n            return []\n\n    def _log_fin_saison_plan(self, plan, water, target):\n        candidate = plan.get("candidate") or {}\n        signature = (\n            bool(plan.get("should_heat")),\n            plan.get("heat_target_c"),\n            candidate.get("date"),\n            bool(candidate.get("last_chance")),\n            str(plan.get("reason") or ""),\n        )\n        if signature == self.fin_saison_last_log_signature:\n            return\n        self.fin_saison_last_log_signature = signature\n        try:\n            self.log(\n                f"Fin de saison prédictif: eau {water:.1f}/{target:.1f} °C | "\n                f"{plan.get('reason', '')}",\n                log="piscine_log",\n            )\n        except Exception:\n            pass\n\n    def _manage_fin_saison_predictif(self):\n        """Heat only when needed for a likely bathing window over 7-10 days."""\n        water = self._premiere_chauffe_temperature()\n        target = self._pac_target_temperature()\n        if water is None or target is None:\n            self.fin_saison_heat_requested = False\n            self.fin_saison_heat_target_c = None\n            self._cancel_chauffage_start()\n            self._fault(\n                "fin_saison_temperature",\n                "température eau/consigne PAC indisponible; chauffage prédictif arrêté",\n            )\n            return self._pac_off("fin de saison prédictif: température indisponible")\n        self._recover("fin_saison_temperature")\n\n        forecast = self._refresh_fin_saison_forecast()\n        plan = build_end_season_plan(\n            now=datetime.datetime.now(),\n            water_c=water,\n            target_c=target,\n            forecast=forecast,\n            score_min=self.fin_saison_score_baignade_min,\n            min_air_c=self.fin_saison_temperature_baignade_min_c,\n            swim_time=self.fin_saison_heure_baignade_cible,\n            heating_rate_c_per_h=self.fin_saison_gain_chauffe_c_par_h,\n            floor_delta_c=self.fin_saison_temperature_plancher_delta_c,\n            maintenance_band_c=self.fin_saison_recharge_plancher_c,\n            stop_margin_c=self.fin_saison_marge_arret_c,\n            safety_margin_h=self.fin_saison_marge_planification_h,\n            last_chance_margin_h=self.fin_saison_marge_derniere_occasion_h,\n            heating_window_start=self.fin_saison_heure_debut_chauffe,\n            heating_window_end=self.fin_saison_heure_fin_chauffe,\n        )\n        self.fin_saison_last_plan = plan\n        self._log_fin_saison_plan(plan, water, target)\n\n        if plan.get("should_heat"):\n            self.fin_saison_heat_requested = True\n            self.fin_saison_heat_target_c = plan.get("heat_target_c") or target\n            return self._request_chauffage_start(\n                self.chauffage_preset_smart,\n                "fin de saison prédictif",\n            )\n\n        self.fin_saison_heat_requested = False\n        self.fin_saison_heat_target_c = None\n        self._cancel_chauffage_start()\n        return self._pac_off(\n            f"fin de saison prédictif: {plan.get('reason', 'attente')}",\n            post=True,\n        )\n\n'''
replace_once(path, marker, helpers + marker)

replace_once(
    path,
    '''        if kind == "end_season":\n            return self._request_chauffage_start(self.chauffage_preset_smart, "fin de saison")\n''',
    '''        if kind == "end_season":\n            if self.fin_saison_predictif:\n                return self._manage_fin_saison_predictif()\n            return self._request_chauffage_start(self.chauffage_preset_smart, "fin de saison")\n''',
)

replace_once(
    path,
    '''    def pac_besoin_chauffe(self):\n        if self.entity_chauffage and self.chauffage_mode_explicite():\n            return True\n        return super().pac_besoin_chauffe()\n''',
    '''    def pac_besoin_chauffe(self):\n        if self.entity_chauffage:\n            kind = chauffage_mode_kind(self.chauffage_mode())\n            if kind in {"first_heat", "turbo"}:\n                return True\n            if kind == "end_season":\n                if not self.fin_saison_predictif:\n                    return True\n                return bool(self.fin_saison_heat_requested)\n        return super().pac_besoin_chauffe()\n''',
)

# ---------------------------------------------------------------------------
# canonical example
# ---------------------------------------------------------------------------
path = "examples/filtration_piscine.yaml"
replace_once(
    path,
    '''  chauffage_preset_turbo: Turbo\n  chauffage_premiere_chauffe_marge_c: 0.3\n\n''',
    '''  chauffage_preset_turbo: Turbo\n  chauffage_premiere_chauffe_marge_c: 0.3\n\n  # v0.5.0: optional predictive end-of-season heating. With this enabled,\n  # Fin de saison no longer keeps pump/PAC availability 24/7. It looks across\n  # the available daily weather forecast (up to 10 days), identifies likely\n  # bathing windows and schedules only the heat needed before the next one.\n  fin_saison_predictif: false\n  entity_meteo_fin_saison: weather.home\n  fin_saison_horizon_jours: 10\n  fin_saison_prevision_refresh_s: 1800\n  fin_saison_prevision_max_age_s: 21600\n  fin_saison_temperature_baignade_min_c: 21\n  fin_saison_temperature_baignade_ideale_c: 26\n  fin_saison_score_baignade_min: 55\n  fin_saison_heure_baignade_cible: "16:00:00"\n  fin_saison_gain_chauffe_c_par_h: 0.30\n  fin_saison_temperature_plancher_delta_c: 3.0\n  fin_saison_recharge_plancher_c: 0.5\n  fin_saison_marge_arret_c: 0.2\n  fin_saison_marge_planification_h: 0.5\n  fin_saison_marge_derniere_occasion_h: 1.0\n  fin_saison_heure_debut_chauffe: "08:00:00"\n  fin_saison_heure_fin_chauffe: "20:00:00"\n\n''',
)

# ---------------------------------------------------------------------------
# README
# ---------------------------------------------------------------------------
path = "README.md"
replace_once(
    path,
    '''    pool_safety.py          # PAC sequencing, freeze and fail-safe layer\n    pool_daylight.py        # daylight window + thermal-reference memory\n''',
    '''    pool_safety.py          # PAC sequencing, freeze and fail-safe layer\n    pool_end_season.py      # predictive 7-10 day end-of-season planner\n    pool_daylight.py        # daylight window + thermal-reference memory\n''',
)

section_marker = '''## Heating override boundary\n'''
section = '''## Predictive end-of-season heating\n\nSince v0.5.0, `Fin de saison • Smart` can optionally become a predictive strategy instead of keeping PAC circulation available 24/7:\n\n```yaml\nfin_saison_predictif: true\nentity_meteo_fin_saison: weather.home\nfin_saison_horizon_jours: 10\nfin_saison_heure_baignade_cible: "16:00:00"\nfin_saison_gain_chauffe_c_par_h: 0.30\nfin_saison_temperature_plancher_delta_c: 3.0\n```\n\nThe planner asks Home Assistant for the weather entity's **daily** forecast using `weather.get_forecasts`. It uses as many days as the provider actually returns, up to the configured 10-day horizon. The strategic horizon identifies the next credible bathing opportunity from forecast maximum temperature, condition/cloudiness, rain and wind. A good day followed by several bad days is marked as a likely **last chance** and receives extra scheduling margin.\n\nThe operational decision works backwards from the configured bathing time. Pool Manager estimates the heating hours needed from the current water temperature and `fin_saison_gain_chauffe_c_par_h`, then places those hours as late as possible inside the preferred daytime heating window. This means a warm/sunny tomorrow normally does **not** cause needless heating through the preceding night. If the pool is too cold to recover before the opportunity using daytime hours alone, the calculated start can move earlier, including overnight when that is genuinely required to meet the deadline.\n\nWhen no credible bathing day is visible, the planner does not maintain the full setpoint. It only protects a configurable recovery floor (`setpoint - fin_saison_temperature_plancher_delta_c`) and recharges that floor during preferred daytime hours. The normal filtration strategy therefore regains control whenever predictive heating is waiting. PAC startup, minimum-flow protection, forced stop and freeze protection remain higher priority.\n\nForecasts are cached and refreshed every 30 minutes by default. If refresh temporarily fails, a recent cache can be reused for up to six hours. Without a usable forecast, the planner falls back to recovery-floor maintenance instead of inventing weather. The feature is **opt-in** so upgrading from v0.4.x does not silently change existing end-of-season behavior. AppDaemon 4.5+ is recommended because Home Assistant service response data is required for `weather.get_forecasts`.\n\n'''
replace_once(path, section_marker, section + section_marker)

# ---------------------------------------------------------------------------
# CHANGELOG
# ---------------------------------------------------------------------------
path = "CHANGELOG.md"
replace_once(
    path,
    "# Changelog\n\n",
    '''# Changelog\n\n## 0.5.0 - Predictive end-of-season heating\n\n- Add an opt-in predictive strategy for `Fin de saison • Smart` so the pump/PAC no longer need to remain available 24/7 merely to preserve the setpoint.\n- Read Home Assistant daily weather forecasts with `weather.get_forecasts` and use up to a 10-day strategic horizon, limited by the number of days the configured provider actually supplies.\n- Score likely bathing opportunities from forecast high temperature, sun/cloud condition, precipitation probability/amount and wind.\n- Detect a good day followed by several poor days as a likely last bathing opportunity and give it additional scheduling margin.\n- Work backwards from a configurable bathing time and measured/estimated PAC heating rate, placing required heating hours as late as possible in a preferred daytime window.\n- Avoid needless overnight heating when a warm/sunny next day still provides enough time to recover the requested water temperature; start earlier, including overnight, only when required to meet the bathing deadline.\n- When no credible bathing window is visible, maintain only a configurable recovery floor instead of the full PAC setpoint.\n- Cache forecasts, tolerate a temporary refresh failure with a bounded stale cache, and fall back to recovery-floor behavior when forecast response data is unavailable.\n- Keep legacy 24/7 `Fin de saison` behavior unchanged unless `fin_saison_predictif: true` is explicitly configured.\n- Keep forced stop, Hors Gel and PAC minimum-flow fail-safes above the predictive planner.\n\n''',
)
