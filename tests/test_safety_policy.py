import datetime
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_common import TAB_MODE
from pool_auto_gate import AutoModeGateMixin
from pool_safety import (
    SafetyMixin,
    decide_hors_gel_continu,
    pac_auto_conditions_arret,
    pac_auto_conditions_demarrage,
)


def test_freeze_starts_continuous_circulation_even_if_mode_did_not_switch():
    assert decide_hors_gel_continu(0.5, False, TAB_MODE[1], 1.0, 3.0) is True


def test_freeze_hysteresis_keeps_circulation_between_thresholds():
    assert decide_hors_gel_continu(2.0, True, TAB_MODE[1], 1.0, 3.0) is True
    assert decide_hors_gel_continu(2.0, False, TAB_MODE[1], 1.0, 3.0) is False


def test_freeze_releases_above_high_threshold():
    assert decide_hors_gel_continu(3.1, True, TAB_MODE[2], 1.0, 3.0) is False


def test_freeze_sensor_loss_is_fail_safe_in_hors_gel_mode():
    assert decide_hors_gel_continu(None, False, TAB_MODE[2], 1.0, 3.0) is True


def test_freeze_sensor_loss_does_not_force_summer_circulation_from_idle():
    assert decide_hors_gel_continu(None, False, TAB_MODE[1], 1.0, 3.0) is False


def test_missing_mode_and_missing_temperature_falls_back_to_freeze_safety():
    assert decide_hors_gel_continu(None, False, None, 1.0, 3.0) is True


def test_forced_stop_remains_highest_priority_even_below_zero():
    assert decide_hors_gel_continu(-5.0, True, TAB_MODE[4], 1.0, 3.0) is False


def test_pac_start_requires_all_three_warm_conditions():
    thresholds = (25.0, 19.0, 18.0)
    assert pac_auto_conditions_demarrage(26.0, 20.0, 19.0, thresholds) is True
    assert pac_auto_conditions_demarrage(24.9, 20.0, 19.0, thresholds) is False
    assert pac_auto_conditions_demarrage(26.0, None, 19.0, thresholds) is False


def test_pac_stop_requires_all_three_cold_conditions():
    thresholds = (21.0, 18.0, 17.0)
    assert pac_auto_conditions_arret(20.0, 17.0, 16.0, thresholds) is True
    assert pac_auto_conditions_arret(20.0, 18.5, 16.0, thresholds) is False


class _Base:
    def _get_float_state_raw(self, entity_id, default=0.0):
        return default


class _SafetyApp(AutoModeGateMixin, SafetyMixin, _Base):
    pass


def make_safety_app(states):
    app = object.__new__(_SafetyApp)
    app.args = {
        "temperature_eau": "sensor.water",
        "mem_temp": "input_number.water_memory",
    }
    app.fail_safe_active = True
    app.temperature_eau_secours_c = 32.0
    app.last_valid_water_temp = None
    app._safety_faults = set()
    app.get_state = lambda entity_id, **kwargs: states.get(entity_id, "unavailable")
    app.log = lambda *args, **kwargs: None
    return app


def test_unavailable_water_probe_uses_memory_not_fake_10_degrees():
    app = make_safety_app({
        "sensor.water": "unavailable",
        "input_number.water_memory": "30.8",
    })
    assert app.get_float_state("sensor.water", 10.0) == 30.8


def test_water_probe_and_memory_loss_uses_conservative_fallback():
    app = make_safety_app({})
    assert app.get_float_state("sensor.water", 10.0) == 32.0


def test_active_pac_power_keeps_flow_demand_if_climate_state_is_lost():
    app = make_safety_app({"sensor.pac_power": "650"})
    app.entity_pac_conso = "sensor.pac_power"
    app.pac_flow_active_w = 200.0
    app.pac_auto_start_pending = False
    app.pac_post_circulation_until = None
    assert app._pac_circulation_securite_requise() is True


def test_auto_gate_blocks_seasonal_pac_policy_explicitly():
    app = make_safety_app({"input_boolean.pool_auto": "off"})
    app.gestion_pac_auto = True
    app.entity_mode_auto = "input_boolean.pool_auto"
    app.pac_auto_start_pending = True
    app.handle_pac_auto_start = None
    app.pac_auto_start_deadline = object()
    app._manage_pac_saisonnier()
    assert app.pac_auto_start_pending is False
    assert app.pac_auto_start_deadline is None


def test_auto_gate_aborts_pending_start_callback_before_flow_checks():
    app = make_safety_app({"input_boolean.pool_auto": "off"})
    app.gestion_pac_auto = True
    app.entity_mode_auto = "input_boolean.pool_auto"
    app.pac_auto_start_pending = True
    app.handle_pac_auto_start = "timer"
    app.pac_auto_start_deadline = object()
    app.cancel_timer = lambda handle: None
    app._check_pac_start({})
    assert app.pac_auto_start_pending is False
    assert app.handle_pac_auto_start is None
    assert app.pac_auto_start_deadline is None


def make_pac_cycle_app(state="heat"):
    app = make_safety_app({"climate.pool_pac": state})
    app.entity_pac_climate = "climate.pool_pac"
    app.entity_pac_conso = "sensor.pac_power"
    app.pac_flow_active_w = 200.0
    app.pac_min_on_s = 900
    app.pac_min_off_s = 300
    app.pac_last_start_at = None
    app.pac_compressor_started_at = None
    app.pac_last_stop_at = None
    app.pac_deferred_stop_reason = None
    app.pac_deferred_start_label = None
    app.pac_auto_start_pending = False
    app.pac_auto_start_deadline = None
    app.handle_pac_auto_start = None
    app.pac_post_circulation_s = 0
    app.pac_post_circulation_until = None
    app.handle_pac_post_circulation = None
    app.services = []
    app.call_service = lambda service, **kwargs: app.services.append((service, kwargs))
    return app


def test_predictive_stop_is_deferred_until_minimum_compressor_runtime():
    app = make_pac_cycle_app("heat")
    app.pac_compressor_started_at = datetime.datetime.now() - datetime.timedelta(seconds=60)

    stopped = app._pac_off(
        "chauffage prédictif: attente",
        respect_min_on=True,
    )

    assert stopped is False
    assert app.services == []
    assert app.pac_deferred_stop_reason == "chauffage prédictif: attente"
    assert app._pac_circulation_securite_requise() is True


def test_predictive_stop_runs_after_minimum_compressor_runtime():
    app = make_pac_cycle_app("heat")
    app.pac_compressor_started_at = datetime.datetime.now() - datetime.timedelta(seconds=901)

    stopped = app._pac_off(
        "chauffage prédictif: attente",
        respect_min_on=True,
    )

    assert stopped is True
    assert app.services == [
        (
            "climate/set_hvac_mode",
            {"entity_id": "climate.pool_pac", "hvac_mode": "off"},
        )
    ]
    assert app.pac_last_stop_at is not None


def test_safety_stop_bypasses_minimum_compressor_runtime():
    app = make_pac_cycle_app("heat")
    app.pac_compressor_started_at = datetime.datetime.now()

    stopped = app._pac_off("circulation pompe non confirmée", post=False)

    assert stopped is True
    assert len(app.services) == 1


def test_restart_is_deferred_during_minimum_compressor_off_time():
    app = make_pac_cycle_app("off")
    app.pac_last_stop_at = datetime.datetime.now() - datetime.timedelta(seconds=30)

    assert app._pac_start_deferred("fin de saison") is True
    assert app.services == []


def test_physical_pac_power_starts_minimum_runtime_clock():
    app = make_pac_cycle_app("heat")
    app.get_state = lambda entity_id, **kwargs: (
        "800" if entity_id == "sensor.pac_power" else "heat"
    )
    app._pump_flow_ok = lambda: True

    app._protect_pac_flow()

    assert app.pac_compressor_started_at is not None


def test_post_circulation_waits_for_pac_power_to_stay_low():
    app = make_pac_cycle_app("off")
    app.pac_post_circulation_stable_s = 30
    app.pac_post_circulation_until = (
        datetime.datetime.now() + datetime.timedelta(minutes=2)
    )
    app.pac_post_circulation_low_since = None
    app.handle_pac_post_circulation = "timer"
    app.flow_requests = 0
    app._ensure_pac_flow = lambda: setattr(
        app,
        "flow_requests",
        app.flow_requests + 1,
    )
    scheduled = []
    app.run_in = lambda callback, delay: scheduled.append((callback, delay)) or "next"
    app.traitement = lambda kwargs: scheduled.append(("traitement", kwargs))
    app._pac_power_active = lambda: True

    app._end_pac_post({})

    assert app.flow_requests == 1
    assert app.handle_pac_post_circulation == "next"
    assert scheduled[-1][1] <= 5

    app._pac_power_active = lambda: False
    app.pac_post_circulation_low_since = (
        datetime.datetime.now() - datetime.timedelta(seconds=31)
    )
    app._end_pac_post({})

    assert app.pac_post_circulation_until is None
    assert app.pac_post_circulation_low_since is None
    assert scheduled[-1][0] == "traitement"


def test_hvac_off_with_residual_pac_power_starts_post_circulation():
    app = make_pac_cycle_app("off")
    app.pac_post_circulation_s = 60
    app.pac_post_circulation_max_s = 180
    app.pac_post_circulation_low_since = None
    app.handle_pac_post_circulation = None
    app.calls = []
    app._pac_power_active = lambda: True
    app._ensure_pac_flow = lambda: app.calls.append(("flow",))
    app.run_in = lambda callback, delay: app.calls.append(("timer", delay)) or "post"

    assert app._pac_off("residual test", post=True) is True

    assert app.handle_pac_post_circulation == "post"
    assert app.pac_post_circulation_until is not None
    assert ("flow",) in app.calls
