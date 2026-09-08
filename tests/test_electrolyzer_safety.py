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


def make_app():
    app = object.__new__(module.FiltrationPiscine)
    app.entity_consigne_electrolyseur = "number.chlorinator"
    app.last_consigne_electrolyseur = None
    app.args = {"timeout_electrolyseur_force_s": 3.0}
    app.log = lambda *args, **kwargs: None
    return app


def test_same_chlorinator_value_is_not_written_again():
    app = make_app()
    calls = []
    app.get_state = lambda entity_id: "50"
    app.call_service = lambda *args, **kwargs: calls.append((args, kwargs))

    app.set_consigne_electrolyseur(50)

    assert calls == []
    assert app.last_consigne_electrolyseur == 50.0


def test_forced_chlorinator_write_uses_bounded_timeout():
    app = make_app()
    calls = []
    app.get_state = lambda entity_id: "50"

    def call_service(*args, **kwargs):
        calls.append((args, kwargs))

    app.call_service = call_service
    app.set_consigne_electrolyseur(0, force=True)

    assert len(calls) == 1
    assert calls[0][0][0] == "number/set_value"
    assert calls[0][1]["timeout"] == 3.0


def test_forced_stop_still_stops_pump_after_chlorinator_timeout():
    app = make_app()
    app.consigne_electrolyseur_arret = 0.0
    app.get_state = lambda entity_id: "50" if entity_id == "number.chlorinator" else "on"
    app.cancel_pending_start_sequence = lambda: None
    app.cancel_pending_stop_sequence = lambda: None
    app.arret_force_actif = lambda: True

    stopped = {"value": False}
    app.turn_off_pompe_direct = lambda: stopped.__setitem__("value", True)

    def call_service(*args, **kwargs):
        raise TimeoutError("chlorinator did not answer")

    app.call_service = call_service

    app.turn_off_pompe_mem(force=True)

    assert stopped["value"] is True
