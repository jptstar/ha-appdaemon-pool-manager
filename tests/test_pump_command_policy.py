import datetime
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_common import TAB_MODE
from pool_devices import DevicesMixin


class FakeDevices(DevicesMixin):
    args = {
        "fan_variateur_pompe": "fan.pool",
        "mode_de_fonctionnement": "input_select.pool_mode",
    }
    delta_vitesse_min = 5
    tempo_changement_vitesse = 30
    pas_vitesse_max = 10
    entity_pompe_local_panel_assist = "switch.local_assist"

    def __init__(self, current=50, last_command=70, mode=TAB_MODE[1], assist="on"):
        self.current = current
        self.derniere_vitesse_commande = last_command
        self.last_changement_vitesse = datetime.datetime.now() - datetime.timedelta(seconds=60)
        self.mode = mode
        self.assist = assist
        self.calls = []

    def get_state(self, entity_id, attribute=None):
        if entity_id == "fan.pool" and attribute == "percentage":
            return self.current
        if entity_id == "input_select.pool_mode":
            return self.mode
        if entity_id == "switch.local_assist":
            return self.assist
        return None

    def call_service(self, service, **kwargs):
        self.calls.append((service, kwargs))

    def maj_electrolyseur(self):
        pass

    def log(self, *args, **kwargs):
        pass

    def arret_force_actif(self):
        return False


def test_automatic_control_uses_physical_speed_again():
    devices = FakeDevices(current=50, last_command=70, mode=TAB_MODE[1])

    result = devices.set_pump_percentage(70)

    assert result == 60
    assert devices.calls == [
        ("fan/set_percentage", {"entity_id": "fan.pool", "percentage": 60})
    ]


def test_unchanged_physical_speed_is_not_rewritten():
    devices = FakeDevices(current=70, last_command=70, mode=TAB_MODE[1])

    result = devices.set_pump_percentage(70)

    assert result == 70
    assert devices.calls == []


def test_temperature_and_forced_modes_enable_local_assist():
    for mode in (TAB_MODE[0], TAB_MODE[3]):
        devices = FakeDevices(mode=mode, assist="off")
        devices.sync_local_panel_policy(mode)
        assert devices.calls == [
            ("switch/turn_on", {"entity_id": "switch.local_assist"})
        ]


def test_intelligent_and_safety_modes_disable_local_assist():
    for mode in (TAB_MODE[1], TAB_MODE[2], TAB_MODE[4]):
        devices = FakeDevices(mode=mode, assist="on")
        devices.sync_local_panel_policy(mode)
        assert devices.calls == [
            ("switch/turn_off", {"entity_id": "switch.local_assist"})
        ]


def test_temperature_path_only_forces_initial_speed_once():
    source = (MODULE_DIR / "pool_control.py").read_text(encoding="utf-8")
    assert "elif not self.mode_speed_initialized:" in source
    assert "if not self.mode_speed_initialized:" in source
    assert "vitesse_appliquee = self.get_fan_percentage()" in source
