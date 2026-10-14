import importlib.util
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "apps" / "pool_manager" / "pool_heating.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("pool_heating", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_selector_modes_are_classified():
    assert module.chauffage_mode_kind("Désactivé") == "disabled"
    assert module.chauffage_mode_kind("Automatique") == "auto"
    assert module.chauffage_mode_kind("Début de saison • Smart") == "season_start"
    # v0.5 wording remains accepted for existing Home Assistant selectors.
    assert module.chauffage_mode_kind("Première chauffe • Smart") == "season_start"
    assert module.chauffage_mode_kind("Fin de saison • Smart") == "end_season"
    assert module.chauffage_mode_kind("Turbo • 6 h") == "turbo"
    assert module.chauffage_mode_kind("unexpected") == "unknown"


@pytest.mark.parametrize("owned", [False, True])
def test_unmanaged_auto_releases_only_controller_owned_heat(owned):
    app = object.__new__(module.HeatingModeMixin)
    app.entity_chauffage = "input_select.pool_heating"
    app.gestion_pac_auto = False
    app.chauffage_predictif = True
    app._chauffage_heat_owned = owned
    app._seasonal_heating_blocked = lambda: False
    app.chauffage_mode = lambda: "Automatique"
    app._season_locked = lambda kind: False
    app._update_predictive_learning = lambda: None
    app._recover = lambda *args: None
    app._mode = lambda: "Intelligent"
    app.arret_force_actif = lambda: False
    app._cancel_chauffage_start = lambda: None
    app._cancel_pac_start = lambda: None
    app._update_predictive_diagnostics = lambda *args, **kwargs: None
    calls = []
    app._pac_off = lambda *args, **kwargs: calls.append(kwargs) or False
    app._pac_state = lambda: "heat"
    app._manage_pac_auto()
    assert len(calls) == int(owned)
    if owned:
        assert calls[0]["respect_min_on"] is True
        assert app._pac_circulation_chauffage_requise() is True


@pytest.mark.parametrize("day,expected", [(9, False), (10, True), (13, True)])
def test_seasonal_cutoff_local_boundary(day, expected):
    assert module.seasonal_cutoff_reached(module.datetime.datetime(2026, 10, day)) is expected


def test_measurement_blocks_heat_before_service_and_keeps_deferred_stop():
    app = object.__new__(module.HeatingModeMixin)
    app.chauffage_predictif_measurement_active = True
    app.pac_deferred_stop_reason = "certification hors chauffe"
    app._seasonal_heating_blocked = lambda: False
    app._available = lambda _: pytest.fail("must not start while measuring")
    assert app._set_pac_heat("Smart", "test") is False
    assert app.pac_deferred_stop_reason == "certification hors chauffe"
    assert app._predictive_start_still_allowed("turbo") is False


def test_stale_turbo_cannot_bypass_seasonal_cutoff():
    app = object.__new__(module.HeatingModeMixin)
    app.entity_chauffage = "input_select.pool_heating"
    app.chauffage_arret_saisonnier = True
    app.chauffage_date_arret = (1, 1)
    app.chauffage_mode = lambda: "Turbo • 12 h"
    app._seasonal_turbo_override_until = None
    assert app._seasonal_heating_blocked()
    app._seasonal_turbo_override_until = module.datetime.datetime.now() + module.timedelta(hours=1)
    assert not app._seasonal_heating_blocked()
    app._seasonal_turbo_override_until = module.datetime.datetime.now() - module.timedelta(seconds=1)
    assert app._seasonal_heating_blocked()


def test_legacy_without_selector_keeps_seasonal_compatibility():
    app = object.__new__(module.HeatingModeMixin)
    app.entity_chauffage = None
    app.chauffage_arret_saisonnier = True
    app.chauffage_date_arret = (1, 1)
    assert not app._seasonal_cutoff_active()


def test_turbo_returns_to_disabled_after_cutoff():
    app = object.__new__(module.HeatingModeMixin)
    app._get_chauffage_precedent = lambda: "Fin de saison • Smart"
    app._seasonal_cutoff_active = lambda: True
    options = []
    app._set_chauffage_selector = options.append
    app.log = lambda *a, **kw: None
    app._restore_after_turbo()
    assert options == ["Désactivé"]


def test_cutoff_cancels_pending_heat_and_disables_selector_not_filtration():
    app = object.__new__(module.HeatingModeMixin)
    app.entity_chauffage = "input_select.pool_heating"
    app._seasonal_heating_blocked = lambda: True
    app.chauffage_mode = lambda: "Fin de saison • Smart"
    calls = []
    for name in ["_cancel_chauffage_start", "_cancel_pac_start", "_cancel_turbo_timer"]:
        setattr(app, name, lambda name=name: calls.append(name))
    app._set_chauffage_selector = lambda mode: calls.append(mode)
    app._update_predictive_diagnostics = lambda *a, **kw: None
    app._pac_off = lambda *a, **kw: calls.append((a, kw))
    app._manage_pac_auto()
    assert not app.chauffage_predictif_heat_requested
    assert app.chauffage_predictif_last_plan is None
    assert "Désactivé" in calls
    assert "_cancel_pac_start" in calls
    assert calls[-1][1]['respect_min_on'] is True


def test_all_turbo_durations_are_mapped_to_seconds():
    expected = {
        "Turbo • 1 h": 3600,
        "Turbo • 2 h": 7200,
        "Turbo • 3 h": 10800,
        "Turbo • 6 h": 21600,
        "Turbo • 12 h": 43200,
        "Turbo • 1 jour": 86400,
        "Turbo • 2 jours": 172800,
        "Turbo • 3 jours": 259200,
    }
    for option, seconds in expected.items():
        assert module.turbo_duration_seconds(option) == seconds


def test_season_start_completion_uses_margin_below_setpoint():
    assert module.premiere_chauffe_terminee(27.7, 28.0, 0.3) is True
    assert module.premiere_chauffe_terminee(27.69, 28.0, 0.3) is False
    assert module.premiere_chauffe_terminee(None, 28.0, 0.3) is False
    assert module.premiere_chauffe_terminee(28.0, None, 0.3) is False


def test_heating_mode_set_contains_every_supported_option():
    assert module.CHAUFFAGE_DESACTIVE in module.CHAUFFAGE_MODES
    assert module.CHAUFFAGE_AUTO in module.CHAUFFAGE_MODES
    assert module.CHAUFFAGE_DEBUT_SAISON in module.CHAUFFAGE_MODES
    assert module.CHAUFFAGE_PREMIERE_CHAUFFE in module.CHAUFFAGE_MODES
    assert module.CHAUFFAGE_FIN_SAISON in module.CHAUFFAGE_MODES
    assert set(module.TURBO_DURATIONS_H).issubset(module.CHAUFFAGE_MODES)


def test_manual_decision_expiry_is_checked_even_before_turbo_management():
    app = object.__new__(module.HeatingModeMixin)
    app.entity_chauffage = "input_select.piscine_chauffage"
    calls = []
    app._pool_choice = lambda now: calls.append(now)
    app._update_predictive_learning = lambda: (_ for _ in ()).throw(RuntimeError("stop"))

    with pytest.raises(RuntimeError, match="stop"):
        app._manage_pac_auto()

    assert len(calls) == 1


def test_leaving_forced_turbo_releases_physical_preset_immediately():
    app = object.__new__(module.HeatingModeMixin)
    app.chauffage_predictif = True
    app.chauffage_preset_smart = "Smart"
    app.chauffage_predictif_forecast_at = object()
    app.chauffage_predictif_last_log_signature = object()
    app.chauffage_predictif_heat_requested = False
    app.chauffage_predictif_heat_target_c = None
    app.chauffage_predictif_last_plan = None
    app.chauffage_predictif_measurement_active = False

    calls = []
    app._cancel_turbo_timer = lambda: calls.append(("cancel_turbo",))
    app._set_pac_preset = lambda preset: calls.append(("preset", preset)) or True
    app._cancel_chauffage_start = lambda: calls.append(("cancel_start",))
    app.safety_tick = lambda kwargs: calls.append(("safety_tick",))

    app.change_chauffage_mode(
        "input_select.piscine_chauffage",
        "state",
        "Turbo • 6 h",
        "Automatique",
        {},
    )

    assert ("cancel_turbo",) in calls
    assert ("preset", "Smart") in calls
    assert ("safety_tick",) in calls
