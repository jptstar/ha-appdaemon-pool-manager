import datetime
import importlib.util
import sys
import types
from pathlib import Path


hassapi = types.ModuleType("hassapi")
hassapi.Hass = object
sys.modules["hassapi"] = hassapi

MODULE_PATH = Path(__file__).parents[1] / "apps" / "pool_manager" / "filtration_piscine.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("filtration_piscine_runtime", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

from pool_common import TAB_MODE


def make_app(mode=TAB_MODE[1]):
    app = object.__new__(module.FiltrationPiscine)
    app.args = {"mode_de_fonctionnement": "input_select.pool_mode"}
    app.mode = mode
    app.get_state = lambda entity_id, attribute=None: app.mode
    return app


def test_intelligent_night_mixing_can_really_be_disabled():
    app = make_app(TAB_MODE[1])
    app.brassage_nuit_intelligent = False
    assert app.is_night_brassage_slot(datetime.time(1, 5)) is False


def test_intelligent_night_mixing_can_still_be_enabled_explicitly():
    app = make_app(TAB_MODE[1])
    app.brassage_nuit_intelligent = True
    assert app.is_night_brassage_slot(datetime.time(1, 5)) is True


def test_remote_speed_is_reasserted_before_isaver_watchdog_takes_over():
    app = make_app(TAB_MODE[1])
    app.entity_pompe_local_panel_assist = "switch.local_assist"
    app.derniere_vitesse_commande = 47
    app.pompe_remote_keepalive_s = 30
    app.last_changement_vitesse = datetime.datetime(2026, 9, 17, 9, 0, 0)
    app.arret_force_actif = lambda: False
    app.pompe_est_on = lambda: True
    app.start_sequence_is_running = lambda: False
    app.stop_sequence_is_running = lambda: False

    states = {
        "input_select.pool_mode": TAB_MODE[1],
        "switch.local_assist": "off",
    }
    app.get_state = lambda entity_id, attribute=None: states.get(entity_id)
    calls = []
    app.set_pump_percentage = (
        lambda percentage, force=False: calls.append((percentage, force)) or percentage
    )

    assert app._maintain_remote_pump_authority(
        datetime.datetime(2026, 9, 17, 9, 0, 20)
    ) is False
    assert calls == []

    assert app._maintain_remote_pump_authority(
        datetime.datetime(2026, 9, 17, 9, 0, 31)
    ) is True
    assert calls == [(47, True)]


def test_aquagem_keepalive_works_without_local_assist_entity():
    app = make_app(TAB_MODE[1])
    app.entity_pompe_local_panel_assist = None
    app.pompe_remote_keepalive_enabled = True
    app.derniere_vitesse_commande = 70
    app.pompe_remote_keepalive_s = 30
    app.last_changement_vitesse = datetime.datetime(2026, 9, 19, 9, 0, 0)
    app.arret_force_actif = lambda: False
    app.pompe_est_on = lambda: True
    app.start_sequence_is_running = lambda: False
    app.stop_sequence_is_running = lambda: False

    states = {"input_select.pool_mode": TAB_MODE[1]}
    app.get_state = lambda entity_id, attribute=None: states.get(entity_id)
    calls = []
    app.set_pump_percentage = (
        lambda percentage, force=False: calls.append((percentage, force)) or percentage
    )

    assert app._maintain_remote_pump_authority(
        datetime.datetime(2026, 9, 19, 9, 0, 31)
    ) is True
    assert calls == [(70, True)]


def test_manual_capable_modes_do_not_receive_remote_keepalive():
    app = make_app(TAB_MODE[3])
    app.entity_pompe_local_panel_assist = None
    app.pompe_remote_keepalive_enabled = True
    app.derniere_vitesse_commande = 70
    app.pompe_remote_keepalive_s = 30
    app.last_changement_vitesse = datetime.datetime(2026, 9, 19, 9, 0, 0)
    app.arret_force_actif = lambda: False
    app.pompe_est_on = lambda: True
    app.start_sequence_is_running = lambda: False
    app.stop_sequence_is_running = lambda: False
    app.get_state = lambda entity_id, attribute=None: TAB_MODE[3]

    calls = []
    app.set_pump_percentage = (
        lambda percentage, force=False: calls.append((percentage, force)) or percentage
    )

    assert app._maintain_remote_pump_authority(
        datetime.datetime(2026, 9, 19, 9, 1, 0)
    ) is False
    assert calls == []


def test_intelligent_mode_self_heals_local_control_switch_if_reenabled():
    app = make_app(TAB_MODE[1])
    app.entity_pompe_local_panel_assist = "switch.local_assist"
    app.pompe_remote_keepalive_enabled = True
    app.derniere_vitesse_commande = 70
    app.pompe_remote_keepalive_s = 30
    app.last_changement_vitesse = datetime.datetime(2026, 9, 19, 9, 0, 50)
    app.arret_force_actif = lambda: False
    app.pompe_est_on = lambda: True
    app.start_sequence_is_running = lambda: False
    app.stop_sequence_is_running = lambda: False

    states = {
        "input_select.pool_mode": TAB_MODE[1],
        "switch.local_assist": "on",
    }
    app.get_state = lambda entity_id, attribute=None: states.get(entity_id)
    sync_calls = []
    app.sync_local_panel_policy = lambda mode=None: sync_calls.append(mode)

    assert app._maintain_remote_pump_authority(
        datetime.datetime(2026, 9, 19, 9, 1, 0)
    ) is False
    assert sync_calls == [TAB_MODE[1]]
