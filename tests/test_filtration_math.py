import importlib.util
import math
import sys
import types
from datetime import timedelta
from pathlib import Path

# The mathematical helpers do not need a running AppDaemon instance. Stub the
# import so the production module can be loaded in CI.
hassapi = types.ModuleType("hassapi")
hassapi.Hass = object
sys.modules["hassapi"] = hassapi

MODULE_PATH = Path(__file__).parents[1] / "apps" / "pool_manager" / "filtration_piscine.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("filtration_piscine", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_daily_target_is_never_above_24_hours():
    assert module.calcule_objectif_filtration(40, 2.0, False) == 24.0
    assert module.calcule_objectif_filtration(40, 3.0, True) <= 24.0


def test_adaptive_curve_keeps_temperature_over_two_until_25c():
    assert module.duree_abaque(16) == 8.0
    assert module.duree_abaque(20) == 10.0
    assert module.duree_abaque(24) == 12.0
    assert module.duree_abaque(25) == 12.5


def test_adaptive_curve_is_continuous_and_grows_exponentially_when_hot():
    just_below = module.duree_abaque(24.999)
    at_threshold = module.duree_abaque(25.0)
    just_above = module.duree_abaque(25.001)

    assert abs(at_threshold - just_below) < 0.01
    assert abs(just_above - at_threshold) < 0.01
    assert math.isclose(module.duree_abaque(30), 16.0503, rel_tol=1e-4)
    assert math.isclose(module.duree_abaque(31.1), 16.9578, rel_tol=1e-4)
    assert module.duree_abaque(34) < 20.0


def test_equivalent_hours_use_relative_flow_not_raw_speed_percentage():
    # Approximate hydraulic model from the example configuration:
    # 47% -> 8 m3/h, 70% -> ~11.47 m3/h, 100% -> 16 m3/h.
    debit_ref = 11.471698113207548

    at_reference = module.calcule_temps_filtration_equivalent(1.0, debit_ref, debit_ref)
    at_minimum = module.calcule_temps_filtration_equivalent(1.0, 8.0, debit_ref)
    at_maximum = module.calcule_temps_filtration_equivalent(1.0, 16.0, debit_ref)

    assert math.isclose(at_reference, 1.0, rel_tol=1e-9)
    assert math.isclose(at_minimum, 0.6973684211, rel_tol=1e-6)
    assert math.isclose(at_maximum, 1.3947368421, rel_tol=1e-6)

    # The previous speed-ratio shortcut would have counted only 47/70 ~= 0.671 h.
    assert at_minimum > (47.0 / 70.0)


def test_full_day_window_has_no_negative_or_next_day_time():
    start, end = module.calcule_plage_filtration(24, "10:00:00")
    assert start == timedelta(0)
    assert end == timedelta(hours=23, minutes=59, seconds=59)


def test_window_is_shifted_at_start_of_day_without_negative_time():
    start, end = module.calcule_plage_filtration(22, "10:00:00")
    assert start == timedelta(0)
    assert end > timedelta(hours=21)
    assert end <= timedelta(hours=23, minutes=59, seconds=59)


def test_window_is_shifted_at_end_of_day():
    start, end = module.calcule_plage_filtration(20, "15:00:00")
    assert start >= timedelta(0)
    assert end == timedelta(hours=23, minutes=59, seconds=59)
