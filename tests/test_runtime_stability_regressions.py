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


def _emergency_plan():
    target_day = datetime.date(2026, 9, 17)
    emergency = {
        "start": datetime.datetime(2026, 9, 17, 5, 23),
        "end": datetime.datetime(2026, 9, 17, 7, 0),
        "hours": 1.62,
        "kind": "emergency",
    }
    morning = {
        "start": datetime.datetime(2026, 9, 17, 7, 0),
        "end": datetime.datetime(2026, 9, 17, 11, 0),
        "hours": 4.0,
        "kind": "morning_topup",
    }
    return {
        "candidate": {
            "date": target_day,
            "swim_datetime": datetime.datetime(2026, 9, 17, 16, 0),
        },
        "active_segment": emergency,
        "next_segment": None,
        "schedule": [emergency, morning],
        "scheduled_hours": 5.62,
        "should_heat": True,
        "heat_target_c": 30.0,
        "reason": "urgence",
    }


def test_predictive_emergency_cannot_start_before_configured_morning_window():
    app = make_app(TAB_MODE[1])
    app.chauffage_predictif_veille_debut = "12:00:00"
    app.chauffage_predictif_veille_fin = "20:00:00"
    app.chauffage_predictif_matin_debut = "07:00:00"

    plan = app._suppress_out_of_window_predictive_emergency(
        _emergency_plan(),
        datetime.datetime(2026, 9, 17, 5, 23),
    )

    assert plan["should_heat"] is False
    assert plan["active_segment"] is None
    assert plan["next_segment"]["start"] == datetime.datetime(2026, 9, 17, 7, 0)
    assert plan["schedule"][0]["kind"] == "morning_topup"


def test_predictive_active_slot_is_latched_against_30_second_replanning():
    app = make_app(TAB_MODE[1])
    app.chauffage_predictif_veille_debut = "12:00:00"
    app.chauffage_predictif_veille_fin = "20:00:00"
    app.chauffage_predictif_matin_debut = "07:00:00"
    app.chauffage_predictif_marge_arret_c = 0.2
    app.chauffage_predictif_tempo_min_on_s = 900
    app._predictive_hold_until = None
    app._predictive_hold_target_c = None
    app._predictive_hold_kind = None

    now = datetime.datetime(2026, 9, 16, 12, 0)
    active = {
        "start": now,
        "end": datetime.datetime(2026, 9, 16, 14, 0),
        "hours": 2.0,
        "kind": "previous_day",
    }
    active_plan = {
        "candidate": {
            "date": datetime.date(2026, 9, 17),
            "swim_datetime": datetime.datetime(2026, 9, 17, 16, 0),
        },
        "active_segment": active,
        "schedule": [active],
        "should_heat": True,
        "heat_target_c": 30.0,
    }
    app._stabilize_predictive_plan(active_plan, "auto", 28.0, 30.0, now)

    recalculated_wait = {
        "candidate": active_plan["candidate"],
        "active_segment": None,
        "schedule": [],
        "should_heat": False,
        "heat_target_c": None,
    }
    held = app._stabilize_predictive_plan(
        recalculated_wait,
        "auto",
        28.1,
        30.0,
        now + datetime.timedelta(seconds=30),
    )

    assert held["should_heat"] is True
    assert held["heat_target_c"] == 30.0
    assert "stabilisé" in held["reason"]

    finished = app._stabilize_predictive_plan(
        {
            "candidate": active_plan["candidate"],
            "active_segment": None,
            "schedule": [],
            "should_heat": False,
            "heat_target_c": None,
        },
        "auto",
        29.9,
        30.0,
        now + datetime.timedelta(minutes=1),
    )
    assert finished["should_heat"] is False
    assert app._predictive_hold_until is None
