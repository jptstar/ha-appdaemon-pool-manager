import importlib.util
import sys
import types
from pathlib import Path

hassapi = types.ModuleType("hassapi")
hassapi.Hass = object
sys.modules["hassapi"] = hassapi

MODULE_PATH = Path(__file__).parents[1] / "apps" / "pool_manager" / "filtration_piscine.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("filtration_piscine", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def make_app(temp="20"):
    app = object.__new__(module.FiltrationPiscine)
    app.args = {"temperature_eau": "sensor.pool_water_temperature"}
    app.protection_electrolyse_froid = True
    app.electrolyse_temperature_arret_c = 15.0
    app.electrolyse_temperature_reprise_c = 16.0
    app.electrolyse_basse_temp_bloquee = True
    app._safety_faults = set()
    app.log = lambda *args, **kwargs: None
    app.get_state = lambda entity_id, **kwargs: temp
    return app


def test_cold_water_blocks_electrolysis():
    app = make_app("14.9")
    assert app.electrolyse_temperature_autorisee() is False
    assert app.electrolyse_basse_temp_bloquee is True


def test_warm_water_releases_electrolysis():
    app = make_app("16.0")
    assert app.electrolyse_temperature_autorisee() is True
    assert app.electrolyse_basse_temp_bloquee is False


def test_hysteresis_keeps_lock_between_thresholds():
    app = make_app("15.5")
    app.electrolyse_basse_temp_bloquee = True
    assert app.electrolyse_temperature_autorisee() is False

    app.electrolyse_basse_temp_bloquee = False
    assert app.electrolyse_temperature_autorisee() is True


def test_unavailable_water_probe_blocks_electrolysis():
    app = make_app("unavailable")
    app.electrolyse_basse_temp_bloquee = False
    assert app.electrolyse_temperature_autorisee() is False
    assert app.electrolyse_basse_temp_bloquee is True


def test_protection_can_be_explicitly_disabled():
    app = make_app("5")
    app.protection_electrolyse_froid = False
    assert app.electrolyse_temperature_autorisee() is True
