import datetime
import sys
from pathlib import Path

MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_devices import DevicesMixin


class FakeDevices(DevicesMixin):
    args = {"fan_variateur_pompe": "fan.pool"}
    delta_vitesse_min = 5
    tempo_changement_vitesse = 30
    pas_vitesse_max = 10

    def __init__(self, current=50, last_command=70):
        self.current = current
        self.derniere_vitesse_commande = last_command
        self.last_changement_vitesse = datetime.datetime.now() - datetime.timedelta(seconds=60)
        self.calls = []

    def get_state(self, entity_id, attribute=None):
        if entity_id == "fan.pool" and attribute == "percentage":
            return self.current
        return None

    def call_service(self, service, **kwargs):
        self.calls.append((service, kwargs))

    def maj_electrolyseur(self):
        pass

    def log(self, *args, **kwargs):
        pass


def test_same_automatic_target_preserves_manual_speed():
    devices = FakeDevices(current=50, last_command=70)

    result = devices.set_pump_percentage(70)

    assert result == 50
    assert devices.calls == []
    assert devices.derniere_vitesse_commande == 70


def test_new_automatic_target_reclaims_control_from_manual_speed_with_ramp():
    devices = FakeDevices(current=50, last_command=70)

    result = devices.set_pump_percentage(80)

    assert result == 60
    assert devices.calls == [
        ("fan/set_percentage", {"entity_id": "fan.pool", "percentage": 60})
    ]
    assert devices.derniere_vitesse_commande == 60


def test_force_still_reasserts_safety_speed():
    devices = FakeDevices(current=50, last_command=70)

    result = devices.set_pump_percentage(70, force=True)

    assert result == 70
    assert devices.calls == [
        ("fan/set_percentage", {"entity_id": "fan.pool", "percentage": 70})
    ]


def test_temperature_mode_does_not_force_periodic_speed_writes():
    source = (MODULE_DIR / "pool_control.py").read_text(encoding="utf-8")
    assert "set_pump_percentage(self.vitesse_mode_temperature, force=True)" not in source
