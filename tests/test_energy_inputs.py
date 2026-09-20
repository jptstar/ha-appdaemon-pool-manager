import sys
from pathlib import Path


MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))

from pool_devices import (
    DevicesMixin,
    RESTITUTION_MODE_EXPORT_POSITIVE,
    RESTITUTION_MODE_NET_SIGNED,
    resolve_restitution_inst_mode,
)


class FakeEnergyDevices(DevicesMixin):
    marge_surplus_securite = 50

    def __init__(self, entity_id, value, mode="auto"):
        self.args = {"restitution_inst": entity_id}
        self.value = value
        self.restitution_inst_mode = mode
        self.faults = []
        self.recoveries = []

    def get_state(self, entity_id):
        return self.value

    def _fault(self, key, message):
        self.faults.append((key, message))

    def _recover(self, key, message=None):
        self.recoveries.append((key, message))


def test_auto_detects_legacy_positive_restitution_sensor():
    assert resolve_restitution_inst_mode(
        "auto", "sensor.restitution_reseau_inst"
    ) == RESTITUTION_MODE_EXPORT_POSITIVE


def test_auto_keeps_generic_grid_sensor_signed():
    assert resolve_restitution_inst_mode(
        "auto", "sensor.grid_power"
    ) == RESTITUTION_MODE_NET_SIGNED


def test_positive_restitution_is_normalized_and_becomes_surplus():
    devices = FakeEnergyDevices("sensor.restitution_reseau_inst", "206")

    reseau_net = devices.get_reseau_net_w()

    assert reseau_net == -206
    assert devices.get_surplus_depuis_reseau_net(reseau_net) == 156
    assert devices.faults == []
    assert devices.recoveries == [
        ("restitution_inst", "mesure électrique sensor.restitution_reseau_inst")
    ]


def test_signed_grid_sensor_preserves_import_and_export():
    importing = FakeEnergyDevices("sensor.grid_power", "1250")
    exporting = FakeEnergyDevices("sensor.grid_power", "-850")

    assert importing.get_reseau_net_w() == 1250
    assert importing.get_surplus_depuis_reseau_net(1250) == 0
    assert exporting.get_reseau_net_w() == -850
    assert exporting.get_surplus_depuis_reseau_net(-850) == 800


def test_explicit_mode_overrides_entity_name_detection():
    devices = FakeEnergyDevices(
        "sensor.grid_power",
        "400",
        mode="export_positive",
    )

    assert devices.get_reseau_net_w() == -400


def test_unavailable_grid_measurement_is_not_a_fake_zero():
    devices = FakeEnergyDevices(
        "sensor.restitution_reseau_inst",
        "unavailable",
    )

    assert devices.get_reseau_net_w() is None
    assert devices.faults == [
        (
            "restitution_inst",
            "mesure électrique sensor.restitution_reseau_inst indisponible; arbitrage solaire suspendu",
        )
    ]
    assert devices.recoveries == []
