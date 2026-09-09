import importlib.util
import math
import sys
from datetime import datetime
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "apps" / "pool_manager" / "pool_daylight.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("pool_daylight", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_temperature_reference_is_smoothed_from_reliable_measurements():
    value = module.moyenne_reference_temperature(30.0, 32.0, 0.35)
    assert math.isclose(value, 30.7, rel_tol=1e-9)


def test_temperature_reference_uses_measurement_when_memory_is_invalid():
    assert module.moyenne_reference_temperature(None, 29.4, 0.35) == 29.4
    assert module.moyenne_reference_temperature(0.0, 29.4, 0.35) == 29.4


def test_daylight_progress_is_zero_before_sunrise_and_complete_after_sunset():
    start = datetime(2026, 9, 9, 7, 0)
    end = datetime(2026, 9, 9, 19, 0)

    assert module.progression_solaire(datetime(2026, 9, 9, 6, 30), start, end, 16.0) == 0.0
    assert module.progression_solaire(datetime(2026, 9, 9, 13, 0), start, end, 16.0) == 8.0
    assert module.progression_solaire(datetime(2026, 9, 9, 20, 0), start, end, 16.0) == 16.0


def test_intelligent_night_mixing_can_be_disabled_without_affecting_freeze_mode():
    assert module.brassage_nuit_intelligent_autorise("Intelligent", False) is False
    assert module.brassage_nuit_intelligent_autorise("Intelligent", True) is True
    assert module.brassage_nuit_intelligent_autorise("Hors Gel", False) is True


def test_cloudy_day_does_not_block_daytime_catchup():
    app = object.__new__(module.DaylightMixin)
    app.suivre_soleil_reel = True
    app._daylight_active = lambda: True
    app.seuil_surplus_demarrage_w = 500
    app.tempo_stabilite_surplus = 180
    app.debut_stabilite_surplus = datetime.now()

    ok, remaining = app.stabilite_surplus_ok(0)

    assert ok is True
    assert remaining == 0
    assert app.debut_stabilite_surplus is None


def test_cloudy_day_catchup_still_cannot_start_before_daylight():
    app = object.__new__(module.DaylightMixin)
    app.suivre_soleil_reel = True
    app._daylight_active = lambda: False
    app.seuil_surplus_demarrage_w = 500
    app.tempo_stabilite_surplus = 180
    app.debut_stabilite_surplus = datetime.now()

    ok, remaining = app.stabilite_surplus_ok(0)

    assert ok is False
    assert remaining == 180
    assert app.debut_stabilite_surplus is None
