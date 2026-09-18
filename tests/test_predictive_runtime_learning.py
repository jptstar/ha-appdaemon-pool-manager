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
    app.args = {"temperature_eau": "sensor.water"}
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

    app.chauffage_predictif_certified_water_c = None
    app.chauffage_predictif_certified_at = None
    app.chauffage_predictif_measurement_active = False
    app.chauffage_predictif_measurement_purpose = None
    app.chauffage_predictif_measurement_started_at = None
    app.chauffage_predictif_measurement_stable_at = None
    app.chauffage_predictif_measurement_stable_temp = None

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


def test_below_70_percent_does_not_count_toward_certification(tmp_path):
    app = _make_runtime(tmp_path)
    app.speed = 60
    app.chauffage_predictif_measurement_active = True
    app.chauffage_predictif_measurement_purpose = "decision"

    start = datetime.datetime(2026, 9, 18, 8, 0)
    app._update_certified_measurement(start)

    # The explicit request raises the pump to 70%; the 15-minute certification
    # timer therefore starts only on the next observation, not at 08:00.
    assert app.speed == 70
    app._update_certified_measurement(start + datetime.timedelta(minutes=5))
    assert app.chauffage_predictif_measurement_started_at == (
        start + datetime.timedelta(minutes=5)
    )

    app._update_certified_measurement(start + datetime.timedelta(minutes=15))
    assert app.chauffage_predictif_certified_water_c is None


def test_temperature_certifies_after_15_minutes_at_70_plus_stability(tmp_path):
    app = _make_runtime(tmp_path)
    app.speed = 70
    app.chauffage_predictif_measurement_active = True
    app.chauffage_predictif_measurement_purpose = "decision"

    start = datetime.datetime(2026, 9, 18, 8, 0)
    assert app._update_certified_measurement(start) is False
    assert app._update_certified_measurement(
        start + datetime.timedelta(minutes=15)
    ) is False

    app.water = 25.04
    assert app._update_certified_measurement(
        start + datetime.timedelta(minutes=16)
    ) is False

    app.water = 25.06
    assert app._update_certified_measurement(
        start + datetime.timedelta(minutes=17)
    ) is True

    assert app.chauffage_predictif_certified_water_c == 25.06
    assert app.chauffage_predictif_measurement_active is False


def test_unstable_probe_extends_certification_window(tmp_path):
    app = _make_runtime(tmp_path)
    app.speed = 70
    app.chauffage_predictif_measurement_active = True
    app.chauffage_predictif_measurement_purpose = "decision"

    start = datetime.datetime(2026, 9, 18, 8, 0)
    app._update_certified_measurement(start)
    app._update_certified_measurement(start + datetime.timedelta(minutes=15))

    app.water = 25.4
    app._update_certified_measurement(start + datetime.timedelta(minutes=16))
    assert app.chauffage_predictif_certified_water_c is None

    app.water = 25.42
    app._update_certified_measurement(start + datetime.timedelta(minutes=17))
    app._update_certified_measurement(start + datetime.timedelta(minutes=18))
    assert app.chauffage_predictif_certified_water_c == 25.42


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
