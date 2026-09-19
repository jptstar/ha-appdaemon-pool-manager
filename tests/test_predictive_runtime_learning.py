import datetime
import importlib.util
import sys
from pathlib import Path


MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))
MODULE_PATH = MODULE_DIR / "pool_predictive_runtime.py"

spec = importlib.util.spec_from_file_location("pool_predictive_runtime", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class _Runtime(module.PredictiveHeatingSupport):
    pass


def _make_runtime(tmp_path):
    app = object.__new__(_Runtime)
    app.args = {
        "temperature_eau": "sensor.water",
        "tempo_eau": "input_number.pool_water_circulation_delay",
    }
    app.chauffage_predictif = True
    app.chauffage_predictif_apprentissage = True
    app.chauffage_predictif_learning_file = str(tmp_path / "thermal.json")
    app.chauffage_predictif_rate_model = {}
    app.chauffage_predictif_loss_model = {}
    app.chauffage_predictif_apprentissage_alpha = 0.25

    app.chauffage_predictif_mesure_vitesse_pct = 70
    app.chauffage_predictif_mesure_tempo_s = 900
    app.chauffage_predictif_mesure_stabilite_s = 120
    app.chauffage_predictif_mesure_variation_max_c = 0.15
    app.chauffage_predictif_mesure_fraiche_s = 21600
    app.chauffage_predictif_mesure_intervalle_chauffe_s = 7200
    app.chauffage_predictif_mesure_timeout_grace_s = 300
    app.chauffage_predictif_mesure_retry_s = 900

    app.chauffage_predictif_certified_water_c = None
    app.chauffage_predictif_certified_at = None
    app.chauffage_predictif_measurement_active = False
    app.chauffage_predictif_measurement_purpose = None
    app.chauffage_predictif_measurement_requested_at = None
    app.chauffage_predictif_measurement_started_at = None
    app.chauffage_predictif_measurement_failed_at = None
    app.chauffage_predictif_measurement_stable_at = None
    app.chauffage_predictif_measurement_stable_temp = None
    app.chauffage_predictif_measurement_previous_speed = None
    app.fin_tempo = 1

    app._heating_learning_session = None
    app._passive_learning_session = None
    app._last_certification_processed_at = None

    app.water = 25.0
    app.speed = 70
    app.pump_on = True
    app.cover = "closed"
    app.pac_active = False
    app.ambient = 15.0

    app.pompe_est_on = lambda: app.pump_on
    app.get_fan_percentage = lambda: app.speed
    app._predictive_physical_water_raw = lambda: app.water
    app._predictive_cover_state = lambda: app.cover
    app._pac_power_active = lambda: app.pac_active
    app._raw_float = (
        lambda entity: app.ambient
        if entity != "sensor.water"
        else app.water
    )
    app.entity_temperature_exterieure = "sensor.air"
    app.arret_force_actif = lambda: False
    app.set_pump_percentage = lambda percentage, force=False: setattr(
        app, "speed", max(app.speed, int(percentage))
    )
    app.turn_on_pompe_mem = lambda: setattr(app, "pump_on", True)
    app.get_state = lambda entity, attribute=None: (
        900
        if entity == "input_number.pool_water_circulation_delay"
        else None
    )
    app.log = lambda *args, **kwargs: None
    return app


def test_thermal_learning_is_persisted_and_reloaded(tmp_path):
    app = _make_runtime(tmp_path)
    app.chauffage_predictif_rate_model = {
        "smart": {
            "15_20": {
                "rate": 0.31,
                "count": 8,
                "power_w": 1180.0,
                "kwh_per_c": 3.806,
            }
        }
    }
    app.chauffage_predictif_loss_model = {
        "closed": {"10_15": {"rate": 0.07, "count": 5}}
    }
    app.chauffage_predictif_certified_water_c = 26.4
    app.chauffage_predictif_certified_at = datetime.datetime(2026, 9, 18, 8, 15)
    app._save_predictive_learning()

    restored = _make_runtime(tmp_path)
    restored._load_predictive_learning()

    assert restored.chauffage_predictif_rate_model == app.chauffage_predictif_rate_model
    assert restored.chauffage_predictif_loss_model == app.chauffage_predictif_loss_model
    assert restored.chauffage_predictif_certified_water_c == 26.4
    assert restored.chauffage_predictif_certified_at == datetime.datetime(
        2026, 9, 18, 8, 15
    )


def test_below_70_percent_is_raised_to_certification_minimum(tmp_path):
    app = _make_runtime(tmp_path)
    app.speed = 60
    app.chauffage_predictif_measurement_active = True
    app.chauffage_predictif_measurement_purpose = "decision"

    start = datetime.datetime(2026, 9, 18, 8, 0)
    assert app._update_certified_measurement(start) is False

    # Calibration owns only a temporary minimum. The clock starts once the
    # required hydraulic speed is established.
    assert app.speed == 70
    assert app.chauffage_predictif_measurement_started_at == start

    assert app._update_certified_measurement(
        start + datetime.timedelta(minutes=14, seconds=59)
    ) is False
    assert app.chauffage_predictif_certified_water_c is None

    assert app._update_certified_measurement(
        start + datetime.timedelta(minutes=15)
    ) is True
    assert app.chauffage_predictif_certified_water_c == 25.0


def test_temperature_certifies_after_full_tempo_eau_at_70(tmp_path):
    app = _make_runtime(tmp_path)
    app.speed = 70
    app.chauffage_predictif_measurement_active = True
    app.chauffage_predictif_measurement_purpose = "decision"

    start = datetime.datetime(2026, 9, 18, 8, 0)
    assert app._update_certified_measurement(start) is False
    assert app._update_certified_measurement(
        start + datetime.timedelta(minutes=14, seconds=59)
    ) is False

    app.water = 25.06
    assert app._update_certified_measurement(
        start + datetime.timedelta(minutes=15)
    ) is True

    assert app.chauffage_predictif_certified_water_c == 25.06
    assert app.chauffage_predictif_measurement_active is False


def test_speed_above_70_is_preserved_during_calibration(tmp_path):
    app = _make_runtime(tmp_path)
    app.speed = 85
    app.chauffage_predictif_measurement_active = True
    app.chauffage_predictif_measurement_purpose = "decision"

    start = datetime.datetime(2026, 9, 18, 8, 0)
    assert app._update_certified_measurement(start) is False
    assert app.speed == 85

    assert app._update_certified_measurement(
        start + datetime.timedelta(minutes=15)
    ) is True
    assert app.speed == 85


def test_speed_dip_after_reference_does_not_restart_calibration(tmp_path):
    app = _make_runtime(tmp_path)
    app.speed = 70
    app.chauffage_predictif_measurement_active = True
    app.chauffage_predictif_measurement_purpose = "decision"

    start = datetime.datetime(2026, 9, 18, 8, 0)
    assert app._update_certified_measurement(start) is False
    assert app.chauffage_predictif_measurement_started_at == start

    # A later telemetry dip still re-commands the minimum but must not move the
    # already-established calibration start time.
    app.speed = 65
    assert app._update_certified_measurement(
        start + datetime.timedelta(minutes=5)
    ) is False
    assert app.speed == 70
    assert app.chauffage_predictif_measurement_started_at == start

    app.water = 25.12
    assert app._update_certified_measurement(
        start + datetime.timedelta(minutes=15)
    ) is True
    assert app.chauffage_predictif_certified_water_c == 25.12


def test_measurement_timeout_releases_stuck_cycle_and_blocks_immediate_retry(tmp_path):
    app = _make_runtime(tmp_path)
    app.speed = 60
    app.chauffage_predictif_measurement_active = True
    app.chauffage_predictif_measurement_purpose = "decision"
    app.set_pump_percentage = lambda percentage, force=False: app.speed
    faults = []
    app._fault = lambda key, message: faults.append((key, message))
    app._recover = lambda *args, **kwargs: None

    start = datetime.datetime(2026, 9, 18, 8, 0)
    assert app._update_certified_measurement(start) is False
    assert app.chauffage_predictif_measurement_active is True
    assert app.chauffage_predictif_measurement_started_at is None

    timeout = start + datetime.timedelta(seconds=301)
    assert app._update_certified_measurement(timeout) is False
    assert app.chauffage_predictif_measurement_active is False
    assert app.chauffage_predictif_measurement_failed_at == timeout
    assert app._measurement_retry_blocked(
        timeout + datetime.timedelta(minutes=5)
    ) is True
    assert any(key == "chauffage_predictif_measurement_timeout" for key, _ in faults)


def test_decision_measurement_stops_pac_before_calibration(tmp_path):
    app = _make_runtime(tmp_path)
    calls = []
    app.chauffage_predictif_heat_requested = False
    app.chauffage_predictif_heat_target_c = None
    app.chauffage_predictif_last_plan = None
    app._refresh_predictive_forecast = lambda: []
    app._predictive_water_temperature = lambda: 27.0
    app._pac_target_temperature = lambda: 30.0
    app._recover = lambda *args, **kwargs: None
    app._build_runtime_predictive_plan = lambda *args, **kwargs: {
        "action": "PREHEAT",
        "should_heat": True,
        "heat_target_c": 28.0,
        "preset": "Smart",
        "reason": "préparation",
        "candidate": {},
    }
    app._maybe_measure_during_normal_filtration = lambda plan: False
    app._measurement_required_before_action = lambda plan: True
    app._pac_off = lambda reason, post=True: calls.append(
        ("pac_off", reason, post)
    ) or True
    app._request_predictive_measurement = lambda purpose, start_pump=False: calls.append(
        ("measure", purpose)
    ) or True
    app._publish_predictive_status = lambda **kwargs: None
    app._log_predictive_plan = lambda *args, **kwargs: None

    app._manage_predictive_heating("auto")

    assert calls[0][0] == "pac_off"
    assert calls[0][2] is False
    assert ("measure", "decision") in calls
    assert app.chauffage_predictif_heat_requested is True


def test_night_loss_uses_two_certified_measurements_not_mem_temp(tmp_path):
    app = _make_runtime(tmp_path)

    evening = datetime.datetime(2026, 9, 18, 20, 15)
    app.chauffage_predictif_certified_water_c = 27.4
    app.chauffage_predictif_certified_at = evening
    app._passive_learning_session = {
        "started_at": evening,
        "water": 27.4,
        "cover": "closed",
        "ambient_sum": 12.0 * 10,
        "ambient_count": 10,
        "contaminated": False,
    }

    morning = datetime.datetime(2026, 9, 19, 7, 30)
    app._finalize_passive_learning(morning, 26.6)

    assert "closed" in app.chauffage_predictif_loss_model
    learned_bins = app.chauffage_predictif_loss_model["closed"]
    assert learned_bins
    entry = next(iter(learned_bins.values()))
    assert entry["rate"] > 0
    assert entry["count"] == 1


def test_night_loss_is_rejected_if_pac_or_cover_changed(tmp_path):
    app = _make_runtime(tmp_path)
    evening = datetime.datetime(2026, 9, 18, 20, 15)
    app._passive_learning_session = {
        "started_at": evening,
        "water": 27.4,
        "cover": "closed",
        "ambient_sum": 120.0,
        "ambient_count": 10,
        "contaminated": True,
    }
    app._finalize_passive_learning(
        datetime.datetime(2026, 9, 19, 7, 30),
        26.6,
    )
    assert app.chauffage_predictif_loss_model == {}


def test_raw_pipe_temperature_does_not_replace_certified_learning_value(tmp_path):
    app = _make_runtime(tmp_path)
    app.chauffage_predictif_certified_water_c = 26.0
    app.chauffage_predictif_certified_at = datetime.datetime.now()
    app.water = 22.0  # pipe/local reading, deliberately different
    app.pump_on = True
    app.speed = 47

    value = app._predictive_water_temperature()
    assert value == 26.0


def test_heating_estimate_starts_at_actual_pac_session_not_old_certification(tmp_path):
    app = _make_runtime(tmp_path)
    now = datetime.datetime.now()
    app.chauffage_predictif_certified_water_c = 25.0
    app.chauffage_predictif_certified_at = now - datetime.timedelta(hours=5)
    app.pac_active = True
    app.chauffage_predictif_gain_chauffe_c_par_h = 0.40
    app.chauffage_predictif_rate_model = {
        "smart": {"15_20": {"rate": 0.40, "count": 20}}
    }
    app.chauffage_preset_smart = "Smart"
    app._predictive_pac_preset = lambda: "Smart"
    app._heating_learning_session = {
        "started_at": now - datetime.timedelta(hours=1),
        "start_certified_at": app.chauffage_predictif_certified_at,
        "water": 25.0,
        "preset": "Smart",
    }

    estimated = app._predictive_water_temperature()
    assert 25.35 <= estimated <= 25.45


def test_wait_stops_active_pac_immediately_and_keeps_measurement_pump_only(tmp_path):
    app = _make_runtime(tmp_path)
    calls = []

    app.chauffage_predictif_heat_requested = True
    app.chauffage_predictif_heat_target_c = 31.0
    app.chauffage_predictif_last_plan = None
    app.chauffage_predictif_measurement_active = True
    app.chauffage_predictif_measurement_purpose = "heating_learning"

    app._refresh_predictive_forecast = lambda: []
    app._predictive_water_temperature = lambda: 28.7
    app._pac_target_temperature = lambda: 31.0
    app._recover = lambda *args, **kwargs: None
    app._build_runtime_predictive_plan = lambda *args, **kwargs: {
        "action": "WAIT",
        "should_heat": False,
        "reason": "trajectoire suffisante",
        "candidate": {},
        "floor_c": 22.0,
        "trajectory_target_c": 22.0,
    }
    app._cancel_chauffage_start = lambda: calls.append(("cancel_start",))
    app._pac_power_active = lambda: True
    app._pac_off = lambda reason, post=True: calls.append(
        ("pac_off", reason, post)
    ) or True
    app._maybe_measure_during_normal_filtration = lambda plan: False
    app._log_predictive_plan = lambda *args, **kwargs: None
    app._publish_predictive_status = lambda **kwargs: None

    app._manage_predictive_heating("end_season")

    assert any(call[0] == "pac_off" for call in calls)
    assert app.chauffage_predictif_heat_requested is False
    assert app.chauffage_predictif_heat_target_c is None
    assert app.chauffage_predictif_measurement_active is True
    assert (
        app.chauffage_predictif_measurement_purpose
        == "temperature_stabilization"
    )

def test_predictive_score_sensor_uses_usage_score_and_exposes_threshold(tmp_path):
    app = _make_runtime(tmp_path)
    app.entity_chauffage_predictif_score = "sensor.piscine_score_baignade"
    app.chauffage_predictif_score_baignade_min = 55.0
    calls = []
    app.set_state = lambda entity, **kwargs: calls.append((entity, kwargs))
    app._recover = lambda *args, **kwargs: None
    app._fault = lambda *args, **kwargs: None

    app._publish_predictive_score(
        {
            "candidate": {
                "date": datetime.date(2026, 9, 20),
                "score": 61.0,
                "strategic_score": 64.5,
                "usage_score": 72.0,
                "weekend": True,
                "usage_window": "daytime",
                "confidence": "high",
            }
        }
    )

    assert calls
    entity, payload = calls[-1]
    assert entity == "sensor.piscine_score_baignade"
    assert payload["state"] == 72.0
    assert payload["attributes"]["minimum_score"] == 55.0
    assert payload["attributes"]["weather_score"] == 61.0
    assert payload["attributes"]["strategic_score"] == 64.5
    assert payload["attributes"]["date"] == "2026-09-20"



def test_forced_turbo_countdown_uses_deadline_not_static_timer_remaining(tmp_path):
    app = _make_runtime(tmp_path)
    start = datetime.datetime(2026, 9, 19, 14, 0, 0)
    app.chauffage_turbo_ends_at = start + datetime.timedelta(hours=6)
    app.entity_chauffage_timer = "timer.piscine_chauffage"

    ends_at, remaining = app._forced_heating_timer_progress(start)

    assert ends_at == "2026-09-19T20:00:00"
    assert remaining == 6 * 3600

    _, later_remaining = app._forced_heating_timer_progress(
        start + datetime.timedelta(minutes=37, seconds=19)
    )
    assert later_remaining == 5 * 3600 + 22 * 60 + 41
