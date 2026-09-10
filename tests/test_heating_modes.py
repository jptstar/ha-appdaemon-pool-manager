import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "apps" / "pool_manager" / "pool_heating.py"
spec = importlib.util.spec_from_file_location("pool_heating", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_selector_modes_are_classified():
    assert module.chauffage_mode_kind("Désactivé") == "disabled"
    assert module.chauffage_mode_kind("Automatique") == "auto"
    assert module.chauffage_mode_kind("Première chauffe • Smart") == "first_heat"
    assert module.chauffage_mode_kind("Fin de saison • Smart") == "end_season"
    assert module.chauffage_mode_kind("Turbo • 6 h") == "turbo"
    assert module.chauffage_mode_kind("unexpected") == "unknown"


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


def test_first_heat_completion_uses_margin_below_setpoint():
    assert module.premiere_chauffe_terminee(27.7, 28.0, 0.3) is True
    assert module.premiere_chauffe_terminee(27.69, 28.0, 0.3) is False
    assert module.premiere_chauffe_terminee(None, 28.0, 0.3) is False
    assert module.premiere_chauffe_terminee(28.0, None, 0.3) is False


def test_heating_mode_set_contains_every_supported_option():
    assert module.CHAUFFAGE_DESACTIVE in module.CHAUFFAGE_MODES
    assert module.CHAUFFAGE_AUTO in module.CHAUFFAGE_MODES
    assert module.CHAUFFAGE_PREMIERE_CHAUFFE in module.CHAUFFAGE_MODES
    assert module.CHAUFFAGE_FIN_SAISON in module.CHAUFFAGE_MODES
    assert set(module.TURBO_DURATIONS_H).issubset(module.CHAUFFAGE_MODES)
