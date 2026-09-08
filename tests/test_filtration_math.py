import importlib.util
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
spec = importlib.util.spec_from_file_location("filtration_piscine", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_daily_target_is_never_above_24_hours():
    assert module.calcule_objectif_filtration(40, 2.0, False) == 24.0
    assert module.calcule_objectif_filtration(40, 3.0, True) <= 24.0


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
