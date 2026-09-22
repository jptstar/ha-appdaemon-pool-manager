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
from pool_strategy import StrategyMixin


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


class FakeSplitEnergyDevices(FakeEnergyDevices):
    def __init__(self, import_power, export_power):
        super().__init__("sensor.restitution_reseau_inst", export_power)
        self.entity_grid_import_power = "sensor.grid_import"
        self.states = {
            "sensor.grid_import": import_power,
            "sensor.restitution_reseau_inst": export_power,
        }

    def get_state(self, entity_id):
        return self.states.get(entity_id)


class FakePowerDebug(StrategyMixin):
    entity_pv_power = "sensor.solar_power"

    def __init__(self):
        self.debug = None

    def puissance_pompe_affichee(self, vitesse):
        return 516, True

    def get_pac_power_reelle(self):
        return 1624

    def get_reseau_net_w(self):
        return 1115

    def get_restitution_w(self):
        return 0

    def get_pv_power(self):
        return 2313

    def set_debug_w(self, value):
        self.debug = value


class FakePacSurplus(DevicesMixin):
    entity_pac_conso = "sensor.pac_power"

    def etat_pac(self):
        return "smart"

    def get_float_state(self, entity_id, default=0.0):
        return 1624


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


def test_separate_import_and_restitution_build_signed_grid_power():
    importing = FakeSplitEnergyDevices("1115", "0")
    exporting = FakeSplitEnergyDevices("0", "640")

    assert importing.get_reseau_net_w() == 1115
    assert importing.get_surplus_depuis_reseau_net(1115) == 0
    assert exporting.get_reseau_net_w() == -640
    assert exporting.get_surplus_depuis_reseau_net(-640) == 590


def test_unavailable_separate_import_does_not_become_zero():
    devices = FakeSplitEnergyDevices("unavailable", "0")

    assert devices.get_reseau_net_w() is None
    assert devices.faults == [
        (
            "grid_import_power",
            "mesure électrique sensor.grid_import indisponible; arbitrage solaire suspendu",
        )
    ]


def test_measured_export_does_not_subtract_pac_consumption_twice():
    devices = FakePacSurplus()

    surplus, pac_state, pac_power = devices.calcule_surplus_net_avec_pac(1115)

    assert surplus == 1115
    assert pac_state == "smart"
    assert pac_power == 1624


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


def test_power_debug_exposes_physical_flows_in_requested_order():
    debug = FakePowerDebug()

    debug.format_texte_solaire_debug(70)

    assert debug.debug == (
        "Pompe 516W réel | PAC 1624W | Réseau 1115W | "
        "Réinjection 0W | Solaire 2313W"
    )


def test_power_debug_without_running_pump_keeps_other_physical_flows():
    debug = FakePowerDebug()

    debug.set_debug_solaire_off(0, 1115, 2313)

    assert debug.debug == (
        "PAC 1624W | Réseau 1115W | Réinjection 0W | Solaire 2313W"
    )


def test_power_debug_clamps_negative_meter_noise_to_zero():
    debug = FakePowerDebug()
    debug.get_pac_power_reelle = lambda: -3
    debug.get_pv_power = lambda: -2

    debug.format_texte_solaire_debug(70)

    assert debug.debug == (
        "Pompe 516W réel | PAC 0W | Réseau 1115W | "
        "Réinjection 0W | Solaire 0W"
    )
