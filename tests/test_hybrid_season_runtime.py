"""Integration simulations: heating commands, MPC and helper persistence.

Pure mocked I/O: no real pump, climate entity or HA service is actuated.
"""
import datetime
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "apps" / "pool_manager"))
from pool_heating import HeatingModeMixin
from pool_season import SeasonDecision


def heating(now=datetime.datetime(2026, 10, 12, 12)):
    app = object.__new__(HeatingModeMixin)
    app.args = {"entity_temperature_exterieure_moyenne_7j": "sensor.mean7"}
    app.chauffage_fin_saison_hybride = True
    app.chauffage_fin_saison_cutoff = (10, 31)
    app.chauffage_fin_saison_seuil_7j_c = 16
    app.chauffage_fin_saison_confirmation_h = 72
    app.chauffage_fin_saison_derogation_h = 24
    app.entity_chauffage_saison_terminee = "input_boolean.season_closed"
    app.entity_chauffage_froid_depuis = "input_datetime.cold_since"
    app.entity_chauffage_derogation_jusqua = "input_datetime.override_until"
    app.entity_chauffage = "input_select.piscine_chauffage"
    app.entity_pac_climate = "climate.pool"
    app._season_latched = False
    app._season_reset_pending = False
    app._season_cold_since_cache = None
    app._season_last_decision = None
    app._season_last_recommendation = False
    app._season_now = lambda: now
    app.chauffage_mode = lambda: "Fin de saison • Smart"
    app._states = {
        "input_boolean.season_closed": "off",
        "input_datetime.cold_since": "2000-01-01 00:00:00",
        "input_datetime.override_until": "2000-01-01 00:00:00",
        "sensor.mean7": "15",
    }
    app.get_state = lambda entity, attribute=None: app._states.get(entity)
    app.services = []
    app.call_service = lambda service, **kw: app.services.append((service, kw))
    app._fault = lambda *args: None
    app._recover = lambda *args: None
    app.log = lambda *args, **kw: None
    app._cancel_chauffage_start = lambda: app.services.append(("cancel_pending", {}))
    return app


def test_cutoff_at_midnight_persists_lock():
    app = heating(datetime.datetime(2026, 10, 31, 0, 0))
    assert app._season_locked("end_season")
    assert app._season_latched
    assert ("input_boolean/turn_on", {"entity_id": app.entity_chauffage_saison_terminee}) in app.services


def test_previous_day_does_not_close():
    app = heating(datetime.datetime(2026, 10, 30, 23, 59))
    assert not app._season_locked("end_season")


def test_helper_restores_lock_in_turbo_after_restart():
    app = heating(datetime.datetime(2027, 2, 1))
    app._states["input_boolean.season_closed"] = "on"
    assert app._season_locked("turbo")
    assert not app._season_heat_allowed("turbo")


def test_explicit_new_season_reset_survives_stale_ha_state():
    app = heating()
    app._states["input_boolean.season_closed"] = "on"
    assert app._season_locked("auto")
    app._season_set_closed(False)
    assert not app._season_locked("season_start")
    app._states["input_boolean.season_closed"] = "off"
    assert not app._season_locked("season_start")
    assert not app._season_reset_pending


@pytest.mark.parametrize("operation", ["physical", "request", "deferred"])
def test_all_three_pac_start_paths_refuse_closed_season(operation):
    app = heating(datetime.datetime(2026, 11, 1))
    app.chauffage_mode = lambda: "Fin de saison • Smart"
    app.chauffage_start_deadline = app._season_now() + datetime.timedelta(seconds=20)
    app.handle_chauffage_start = None
    app.chauffage_predictif = True
    app.chauffage_predictif_heat_requested = True
    if operation == "physical":
        assert app._set_pac_heat("Smart", "test") is False
    elif operation == "request":
        assert app._request_chauffage_start("Smart", "test") is None
    else:
        assert app._check_chauffage_start({}) is None
    assert not any(x.startswith("climate/") for x, _ in app.services)
    assert ("cancel_pending", {}) in app.services


def test_missing_forecast_does_not_set_permanent_lock():
    app = heating()
    app.chauffage_predictif_score_baignade_min = 55
    app.chauffage_predictif_prevision_max_age_s = 21600
    app._predictive_forecast_age_s = lambda: None
    app._raw_float = lambda entity: 14.0
    decision = app._season_policy_for_plan({"action": "PRESERVE"}, [])
    assert decision.state == "paused_weather"
    assert not app._season_latched


def test_unreachable_sunny_forecast_pauses_preservation():
    app = heating()
    app.chauffage_predictif_score_baignade_min = 55
    app.chauffage_predictif_prevision_max_age_s = 21600
    app._predictive_forecast_age_s = lambda: 300
    app._raw_float = lambda entity: 15.0
    forecast = [{"usage_score": 90.0}]
    decision = app._season_policy_for_plan({"action": "PRESERVE", "candidate": None}, forecast)
    assert not decision.heat_allowed
    assert decision.state == "paused_weather"  # 72 h not yet confirmed


def test_valid_swim_plan_keeps_heat_allowed():
    app = heating()
    app.chauffage_predictif_score_baignade_min = 55
    app.chauffage_predictif_prevision_max_age_s = 21600
    app._predictive_forecast_age_s = lambda: 300
    app._raw_float = lambda entity: 15.0
    decision = app._season_policy_for_plan(
        {"candidate": {"date": datetime.date(2026, 10, 17)}},
        [{"usage_score": 80.0}],
    )
    assert decision.heat_allowed and decision.state == "active"


def test_override_button_creates_a_bounded_24h_permission():
    app = heating()
    app.safety_tick = lambda args: None
    app._season_override_button_pressed("input_button.override", "state", None, "now", {})
    matches = [kw for name,kw in app.services if name == "input_datetime/set_datetime"]
    assert matches
    assert matches[-1]["datetime"] == "2026-10-13 12:00:00"


def test_override_button_refused_after_october_cutoff():
    app = heating(datetime.datetime(2026, 11, 1))
    app.safety_tick = lambda args: None
    app._season_override_button_pressed("input_button.override", "state", None, "now", {})
    assert not any(name=="input_datetime/set_datetime" for name,_ in app.services)


class FakeComfort:
    date = None
    def prepare(self, forecast, *args, **kw):
        return forecast
    def annotate(self, plan, *args, **kw):
        return plan
    def recover_daylight(self, plan, *args, **kw):
        return plan


def simulate_runtime_mpc(season_decision):
    app = heating()
    app.chauffage_predictif = True
    app.chauffage_preset_smart = "Smart"
    app.chauffage_preset_turbo = "Turbo"
    app._comfort_commitment = FakeComfort()
    app._refresh_predictive_forecast = lambda: [{"usage_score": 20}]
    app._predictive_water_temperature = lambda: 21.0
    app._pac_target_temperature = lambda: 26.0
    app._build_runtime_predictive_plan = lambda *a,**kw: {
        "action":"PRESERVE", "should_heat":True,
        "heat_target_c":22.5,"preset":"Smart",
        "reason":"ancien plancher 22 °C",
    }
    app._apply_pool_decision = lambda plan, *args, **kw: plan
    app._predictive_daylight_active = lambda: True
    app._season_policy_for_plan = lambda plan,forecast: season_decision
    app._comfort_last_log_key = None
    app.chauffage_predictif_measurement_active = False
    app._pac_state = lambda: "heat"
    app._pac_power_active = lambda: True
    app._pac_off = lambda reason, **kw: app.services.append(("pac_off", kw))
    app._maybe_measure_during_normal_filtration = lambda plan: None
    app._log_predictive_plan = lambda plan,*a: None
    app._publish_predictive_status = lambda **kw: app.services.append(("status",kw))
    app._request_chauffage_start = lambda *a: app.services.append(("pac_start",{}))
    return app


def test_mpc_22_degree_preserve_is_cancelled_when_no_swim():
    app = simulate_runtime_mpc(
        SeasonDecision("paused_weather",False,False,"Aucune baignade")
    )
    app._manage_predictive_heating("end_season")
    assert app.chauffage_predictif_last_plan["action"] == "WAIT"
    assert app.chauffage_predictif_last_plan["should_heat"] is False
    assert app.chauffage_predictif_heat_requested is False
    assert any(n=="pac_off" for n,_ in app.services)
    assert not any(n=="pac_start" for n,_ in app.services)


def test_existing_mpc_request_survives_good_swim_window():
    app = simulate_runtime_mpc(
        SeasonDecision("active",True,False,"Baignade réalisable")
    )
    app.chauffage_predictif_measurement_active = False
    app._maybe_measure_during_normal_filtration = lambda plan: None
    app._measurement_required_before_action = lambda plan: False
    app._log_predictive_plan = lambda plan,*args: None
    app._publish_predictive_status = lambda **kw: None
    app._request_chauffage_start = lambda *args: app.services.append(("pac_start",{}))
    app._manage_predictive_heating("end_season")
    assert any(n=="pac_start" for n,_ in app.services)


def test_override_button_in_automatic_mode_does_not_force_season_lock():
    app = heating(datetime.datetime(2026, 11, 1))
    app.chauffage_mode = lambda: "Automatique"
    app.safety_tick = lambda args: None
    app._season_override_button_pressed("input_button.override", "state", None, "now", {})
    assert not app._season_latched
    assert not any(name=="input_datetime/set_datetime" for name,_ in app.services)


def test_cold_confirmation_timer_resets_on_sensor_outage():
    app = heating()
    app._states["input_datetime.cold_since"] = "2026-10-09 11:00:00"
    app._raw_float = lambda entity: None
    avg, cold_since = app._season_cold_since(app._season_now())
    assert avg is None and cold_since is None
    assert any(name == "input_datetime/set_datetime" and
               kw.get("datetime") == "2000-01-01 00:00:00"
               for name, kw in app.services)
