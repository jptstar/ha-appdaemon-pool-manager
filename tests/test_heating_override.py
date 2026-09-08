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


def make_app(entity="input_boolean.pool_heating_override"):
    app = object.__new__(module.FiltrationPiscine)
    app.entity_derogation_chauffage = entity
    return app


def test_heating_override_is_active_when_helper_is_on():
    app = make_app()
    app.get_state = lambda entity_id, **kwargs: "on"
    assert app.derogation_chauffage_active() is True


def test_heating_override_is_inactive_when_helper_is_off():
    app = make_app()
    app.get_state = lambda entity_id, **kwargs: "off"
    assert app.derogation_chauffage_active() is False


def test_heating_override_creates_pac_flow_demand_even_before_pac_runs():
    app = make_app()
    app.get_state = lambda entity_id, **kwargs: "on"
    assert app.pac_besoin_chauffe() is True


def test_missing_override_keeps_previous_pac_demand_logic():
    app = make_app(entity=None)
    app.pac_autorisee = lambda: False
    assert app.pac_besoin_chauffe() is False
